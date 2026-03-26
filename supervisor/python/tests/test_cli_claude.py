"""Tests for the Claude CLI invocation wrapper."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from aquarco_supervisor.cli.claude import (
    ClaudeOutput,
    _extract_from_result_message,
    _extract_json,
    _find_result_message,
    _parse_output,
    execute_claude,
)
from aquarco_supervisor.exceptions import AgentExecutionError, AgentTimeoutError


# ---------------------------------------------------------------------------
# Helpers for stream-json mock stdout
# ---------------------------------------------------------------------------

class _AsyncLineReader:
    """Async-iterable mock stdout that yields pre-encoded NDJSON lines."""

    def __init__(self, raw_lines: list[bytes]) -> None:
        self._data = list(raw_lines)

    def __aiter__(self) -> "_AsyncLineReader":
        return self

    async def __anext__(self) -> bytes:
        if not self._data:
            raise StopAsyncIteration
        return self._data.pop(0)


class _HangingReader:
    """Async-iterable mock stdout that hangs indefinitely (for timeout tests)."""

    def __aiter__(self) -> "_HangingReader":
        return self

    async def __anext__(self) -> bytes:
        await asyncio.sleep(3600)
        raise StopAsyncIteration


def _make_ndjson_stdout(*dicts: dict[str, Any]) -> _AsyncLineReader:
    """Create an async-iterable mock stdout yielding NDJSON-encoded lines."""
    raw_lines = [(json.dumps(d) + "\n").encode() for d in dicts]
    return _AsyncLineReader(raw_lines)


# --- _extract_json ---

def test_extract_json_from_code_block() -> None:
    text = '```json\n{"key": "value", "count": 42}\n```'
    result = _extract_json(text)
    assert result == {"key": "value", "count": 42}


def test_extract_json_from_inline_json() -> None:
    text = 'Some preamble\n{"status": "ok"}\nsome suffix'
    result = _extract_json(text)
    assert result == {"status": "ok"}


def test_extract_json_returns_none_for_plain_text() -> None:
    result = _extract_json("No JSON here at all.")
    assert result is None


def test_extract_json_ignores_invalid_json_in_code_block() -> None:
    text = "```json\n{invalid json}\n```"
    result = _extract_json(text)
    # Falls through to line-by-line attempt, which also fails
    assert result is None


def test_extract_json_ignores_json_arrays_in_lines() -> None:
    """Arrays on a line are not returned as a dict."""
    text = "[1, 2, 3]"
    result = _extract_json(text)
    assert result is None


def test_extract_json_returns_first_valid_dict() -> None:
    text = 'garbage\n{"first": 1}\n{"second": 2}'
    result = _extract_json(text)
    assert result == {"first": 1}


# --- _parse_output ---

def test_parse_output_empty_string() -> None:
    result = _parse_output("", "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_whitespace_only() -> None:
    result = _parse_output("   \n  ", "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_invalid_json() -> None:
    result = _parse_output("not json at all", "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_valid_json_no_result_key() -> None:
    """When JSON has no 'result' key, the parsed dict is returned as-is."""
    raw = json.dumps({"status": "ok", "data": 123})
    result = _parse_output(raw, "task-001", 0)
    assert result == {"status": "ok", "data": 123}


def test_parse_output_result_with_embedded_json() -> None:
    """When result contains a JSON object, it is extracted."""
    inner = {"complexity": "high", "summary": "looks good"}
    raw = json.dumps({"result": json.dumps(inner)})
    result = _parse_output(raw, "task-001", 0)
    assert result == inner


def test_parse_output_result_with_code_block_json() -> None:
    """Extracts JSON from a ```json code block inside the result field."""
    inner_json = '```json\n{"verdict": "approved"}\n```'
    raw = json.dumps({"result": inner_json})
    result = _parse_output(raw, "task-001", 0)
    assert result == {"verdict": "approved"}


def test_parse_output_result_plain_text() -> None:
    """When result is plain text (no JSON), returns _result_text snippet."""
    raw = json.dumps({"result": "Everything looks fine."})
    result = _parse_output(raw, "task-001", 0)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "Everything looks fine."


def test_parse_output_result_empty_string() -> None:
    """When result is an empty string, the outer parsed dict is returned."""
    raw = json.dumps({"result": "", "other": "data"})
    result = _parse_output(raw, "task-001", 0)
    assert result == {"result": "", "other": "data"}


# --- _parse_output with list format (Claude CLI --output-format json) ---

def test_parse_output_list_with_structured_output() -> None:
    """Extracts structured_output from result message in list format."""
    messages = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "content": [{"type": "text", "text": "reviewing..."}]},
        {
            "type": "result",
            "subtype": "success",
            "result": "Here is my review summary.",
            "structured_output": {"summary": "all good", "recommendation": "approve"},
            "total_cost_usd": 0.25,
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 500, "output_tokens": 200, "cache_creation_input_tokens": 50},
            "duration_ms": 5000,
            "num_turns": 3,
            "session_id": "test-session-123",
        },
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["summary"] == "all good"
    assert result["recommendation"] == "approve"
    assert result["_cost_usd"] == 0.25
    assert result["_input_tokens"] == 600  # 100 + 500 cache_read
    assert result["_output_tokens"] == 200
    assert result["_cache_creation_tokens"] == 50
    assert result["_duration_ms"] == 5000
    assert result["_num_turns"] == 3
    assert result["_session_id"] == "test-session-123"


def test_parse_output_list_structured_output_as_string() -> None:
    """Handles structured_output as a JSON string (some CLI versions)."""
    messages = [
        {
            "type": "result",
            "result": "Done.",
            "structured_output": json.dumps({"verdict": "pass"}),
        },
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["verdict"] == "pass"


def test_parse_output_list_no_structured_output_falls_back_to_result() -> None:
    """When no structured_output, extracts JSON from result text."""
    messages = [
        {
            "type": "result",
            "result": '```json\n{"status": "ok"}\n```',
            "total_cost_usd": 0.1,
            "usage": {"input_tokens": 50, "cache_read_input_tokens": 0, "output_tokens": 100},
        },
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["status"] == "ok"
    assert result["_cost_usd"] == 0.1


def test_parse_output_list_plain_text_result_with_metadata() -> None:
    """Plain text result still captures metadata."""
    messages = [
        {
            "type": "result",
            "result": "Everything looks fine.",
            "total_cost_usd": 0.05,
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 0, "output_tokens": 50},
            "duration_ms": 2000,
        },
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "Everything looks fine."
    assert result["_cost_usd"] == 0.05
    assert result["_duration_ms"] == 2000


# --- execute_claude ---

@pytest.mark.asyncio
async def test_execute_claude_raises_when_prompt_file_missing(tmp_path: Any) -> None:
    """Raises AgentExecutionError when the prompt file doesn't exist."""
    prompt_file = tmp_path / "nonexistent.md"

    with pytest.raises(AgentExecutionError, match="Prompt file not found"):
        await execute_claude(
            prompt_file=prompt_file,
            context={"task_id": "t1"},
            work_dir=str(tmp_path),
        )


@pytest.mark.asyncio
async def test_execute_claude_raises_on_nonzero_exit(tmp_path: Any) -> None:
    """Raises AgentExecutionError when Claude CLI exits with non-zero code."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    tmp_path / "logs"

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = _make_ndjson_stdout()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        with pytest.raises(AgentExecutionError, match="Claude CLI exited with code 1"):
            await execute_claude(
                prompt_file=prompt_file,
                context={"task_id": "t1"},
                work_dir=str(tmp_path),
                task_id="t1",
                stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_raises_timeout(tmp_path: Any) -> None:
    """Raises AgentTimeoutError when the process exceeds timeout."""
    import asyncio

    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()
    # Simulate a hanging process — stdout never yields
    mock_proc.stdout = _HangingReader()

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        with pytest.raises(AgentTimeoutError, match="timed out"):
            await execute_claude(
                prompt_file=prompt_file,
                context={"task_id": "t1"},
                work_dir=str(tmp_path),
                timeout_seconds=1,
                task_id="t1",
                stage_num=0,
            )

    mock_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_execute_claude_returns_parsed_output(tmp_path: Any) -> None:
    """Returns structured output when Claude exits successfully."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    structured = {"complexity": "low", "summary": "Done"}
    result_event = {
        "type": "result",
        "subtype": "success",
        "result": json.dumps(structured),
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout(result_event)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        result = await execute_claude(
            prompt_file=prompt_file,
            context={"task_id": "t1"},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    assert result.structured["complexity"] == "low"
    assert result.structured["summary"] == "Done"


@pytest.mark.asyncio
async def test_execute_claude_passes_allowed_tools(tmp_path: Any) -> None:
    """allowed_tools are forwarded as --allowedTools argument."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout()

    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            allowed_tools=["Bash", "Read"],
            task_id="t1",
            stage_num=0,
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--allowedTools" in args_str
    assert "Bash" in args_str


@pytest.mark.asyncio
async def test_execute_claude_passes_denied_tools(tmp_path: Any) -> None:
    """denied_tools are forwarded as --disallowedTools argument."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout()

    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            denied_tools=["WebSearch"],
            task_id="t1",
            stage_num=0,
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--disallowedTools" in args_str
    assert "WebSearch" in args_str


@pytest.mark.asyncio
async def test_execute_claude_uses_system_prompt_file(tmp_path: Any) -> None:
    """Passes --system-prompt-file with the prompt path instead of reading its contents."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout()

    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--system-prompt-file" in args_str
    assert str(prompt_file) in args_str
    assert "--system-prompt" not in args_str.replace("--system-prompt-file", "")


def test_format_schema_prompt_contains_schema() -> None:
    """_format_schema_prompt produces markdown with the JSON schema."""
    from aquarco_supervisor.cli.claude import _format_schema_prompt

    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    result = _format_schema_prompt(schema)
    assert "## Output Format" in result
    assert "```json" in result
    assert '"summary"' in result
    assert "You MUST respond with a JSON object" in result


@pytest.mark.asyncio
async def test_execute_claude_passes_output_schema_flags(tmp_path: Any) -> None:
    """output_schema adds --append-system-prompt and --json-schema flags."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout()

    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            output_schema=schema,
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--append-system-prompt" in args_str
    assert "--json-schema" in args_str
    assert '"summary"' in args_str


@pytest.mark.asyncio
async def test_execute_claude_no_schema_flags_when_none(tmp_path: Any) -> None:
    """When output_schema is None, no schema flags are added."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout()

    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            output_schema=None,
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--append-system-prompt" not in args_str
    assert "--json-schema" not in args_str


# --- ClaudeOutput dataclass ---

def test_claude_output_defaults() -> None:
    """ClaudeOutput has sensible defaults for structured and raw."""
    output = ClaudeOutput()
    assert output.structured == {}
    assert output.raw == ""


def test_claude_output_custom_values() -> None:
    """ClaudeOutput stores both structured data and raw text."""
    output = ClaudeOutput(structured={"key": "val"}, raw="raw text here")
    assert output.structured == {"key": "val"}
    assert output.raw == "raw text here"


# --- _parse_output: no _raw_output in any path ---

def test_parse_output_empty_has_no_raw_output_key() -> None:
    """Empty input should NOT include _raw_output key."""
    result = _parse_output("", "task-001", 0)
    assert "_raw_output" not in result


def test_parse_output_invalid_json_has_no_raw_output_key() -> None:
    """Invalid JSON should NOT include _raw_output key."""
    result = _parse_output("not json", "task-001", 0)
    assert "_raw_output" not in result


def test_parse_output_plain_text_result_has_no_raw_output_key() -> None:
    """Plain text result should NOT include _raw_output key."""
    raw = json.dumps({"result": "Just some text"})
    result = _parse_output(raw, "task-001", 0)
    assert "_raw_output" not in result
    assert result["_result_text"] == "Just some text"


def test_parse_output_list_format_has_no_raw_output_and_no_parsed_messages() -> None:
    """List-format output should NOT include _raw_output or _parsed_messages."""
    messages = [
        {"type": "result", "result": "Some plain text from assistant"},
    ]
    raw = json.dumps(messages)
    result = _parse_output(raw, "task-001", 0)
    assert "_raw_output" not in result
    assert "_parsed_messages" not in result
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "Some plain text from assistant"


# --- _parse_output: _result_text truncation ---

def test_parse_output_result_text_truncated_to_2000() -> None:
    """Long plain text in result field is truncated to 2000 chars."""
    long_text = "x" * 5000
    raw = json.dumps({"result": long_text})
    result = _parse_output(raw, "task-001", 0)
    assert result["_no_structured_output"] is True
    assert len(result["_result_text"]) == 2000
    assert result["_result_text"] == "x" * 2000


def test_parse_output_list_result_text_truncated_to_2000() -> None:
    """Long result text from list-format is truncated to 2000 chars."""
    long_text = "y" * 5000
    messages = [{"type": "result", "result": long_text}]
    raw = json.dumps(messages)
    result = _parse_output(raw, "task-001", 0)
    assert len(result["_result_text"]) == 2000


# --- execute_claude returns ClaudeOutput with raw ---

@pytest.mark.asyncio
async def test_execute_claude_returns_claude_output_with_raw(tmp_path: Any) -> None:
    """execute_claude returns ClaudeOutput with both structured and raw fields."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    structured = {"status": "ok"}
    result_event = {"type": "result", "subtype": "success", "result": json.dumps(structured)}
    result_event_str = json.dumps(result_event)

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout(result_event)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        result = await execute_claude(
            prompt_file=prompt_file,
            context={"task_id": "t1"},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    assert isinstance(result, ClaudeOutput)
    assert result.structured["status"] == "ok"
    assert result.raw == result_event_str


@pytest.mark.asyncio
async def test_execute_claude_cleans_up_context_file(tmp_path: Any) -> None:
    """Temporary context file is deleted even when an exception occurs."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    # Track files created in tmp
    created_files: list[Path] = []

    original_mkstemp = __import__("tempfile").mkstemp

    def tracking_mkstemp(**kwargs: Any) -> Any:
        fd, path = original_mkstemp(**kwargs)
        created_files.append(Path(path))
        return fd, path

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = _make_ndjson_stdout()

    with patch("tempfile.mkstemp", side_effect=tracking_mkstemp), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        with pytest.raises(AgentExecutionError):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
            )

    # All created temp files should be cleaned up
    for f in created_files:
        assert not f.exists(), f"Temp file {f} was not cleaned up"


# --- _find_result_message ---

def test_find_result_message_found() -> None:
    from aquarco_supervisor.cli.claude import _find_result_message

    messages = [
        {"role": "assistant", "content": "hi"},
        {"type": "result", "result": "done"},
    ]
    assert _find_result_message(messages) == {"type": "result", "result": "done"}


def test_find_result_message_not_found() -> None:
    from aquarco_supervisor.cli.claude import _find_result_message

    assert _find_result_message([{"role": "assistant"}]) is None


def test_find_result_message_empty_list() -> None:
    from aquarco_supervisor.cli.claude import _find_result_message

    assert _find_result_message([]) is None


def test_find_result_message_skips_non_dicts() -> None:
    from aquarco_supervisor.cli.claude import _find_result_message

    messages = ["string", 42, None, {"type": "result", "result": "ok"}]
    assert _find_result_message(messages) == {"type": "result", "result": "ok"}


# --- _extract_from_result_message ---

def test_extract_structured_output_dict() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {"structured_output": {"summary": "done", "issues": []}, "result": "text"}
    result = _extract_from_result_message(msg)
    assert result["summary"] == "done"


def test_extract_structured_output_string() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {"structured_output": '{"key": "val"}'}
    result = _extract_from_result_message(msg)
    assert result["key"] == "val"


def test_extract_structured_output_invalid_string_falls_back() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {"structured_output": "not json", "result": '{"fallback": true}'}
    result = _extract_from_result_message(msg)
    assert result["fallback"] is True


def test_extract_no_structured_extracts_from_result() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {"result": '```json\n{"answer": 42}\n```'}
    result = _extract_from_result_message(msg)
    assert result["answer"] == 42


def test_extract_plain_text_result() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {"result": "Just some text"}
    result = _extract_from_result_message(msg)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "Just some text"


def test_extract_no_result_no_structured_returns_msg() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {"type": "result", "duration_ms": 5000}
    result = _extract_from_result_message(msg)
    assert result["type"] == "result"
    assert result["duration_ms"] == 5000


def test_extract_empty_result_no_structured_returns_msg() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {"type": "result", "result": ""}
    result = _extract_from_result_message(msg)
    assert result["type"] == "result"


def test_extract_metadata_fields() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {
        "structured_output": {"ok": True},
        "total_cost_usd": 0.05,
        "usage": {
            "input_tokens": 100,
            "cache_read_input_tokens": 50,
            "output_tokens": 200,
            "cache_creation_input_tokens": 10,
        },
        "duration_ms": 3000,
        "num_turns": 5,
        "session_id": "sess-123",
    }
    result = _extract_from_result_message(msg)
    assert result["_cost_usd"] == 0.05
    assert result["_input_tokens"] == 150
    assert result["_output_tokens"] == 200
    assert result["_cache_creation_tokens"] == 10
    assert result["_duration_ms"] == 3000
    assert result["_num_turns"] == 5
    assert result["_session_id"] == "sess-123"


def test_extract_result_with_structured_none() -> None:
    from aquarco_supervisor.cli.claude import _extract_from_result_message

    msg = {"result": "non-json text", "structured_output": None}
    result = _extract_from_result_message(msg)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "non-json text"


# --- _parse_output: list format with result message ---

def test_parse_output_list_with_result_message() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "working..."}]},
        {"type": "result", "structured_output": {"tests_passed": 5}, "result": "done"},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["tests_passed"] == 5


def test_parse_output_list_no_result_fallback_to_assistant() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": '{"extracted": "ok"}'}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["extracted"] == "ok"


def test_parse_output_list_string_content_fallback() -> None:
    messages = [{"role": "assistant", "content": '{"from_string": true}'}]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["from_string"] is True


def test_parse_output_list_no_json_in_fallback() -> None:
    messages = [{"role": "assistant", "content": "Just chatting"}]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_list_no_assistant_messages() -> None:
    messages = [{"role": "user", "content": "hello"}]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == ""


def test_parse_output_single_dict_with_structured_output() -> None:
    raw = json.dumps({
        "type": "result",
        "structured_output": {"verdict": "pass"},
        "total_cost_usd": 0.01,
    })
    result = _parse_output(raw, "task-001", 0)
    assert result["verdict"] == "pass"
    assert result["_cost_usd"] == 0.01


# --- execute_claude extra_env ---

@pytest.mark.asyncio
async def test_execute_claude_passes_extra_env(tmp_path: Any) -> None:
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout()

    captured_kwargs: dict = {}

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        await execute_claude(
            prompt_file=prompt_file, context={}, work_dir=str(tmp_path),
            task_id="t1", stage_num=0, extra_env={"CUSTOM_VAR": "custom_value"},
        )

    assert captured_kwargs["env"] is not None
    assert captured_kwargs["env"]["CUSTOM_VAR"] == "custom_value"


@pytest.mark.asyncio
async def test_execute_claude_no_extra_env_passes_none(tmp_path: Any) -> None:
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = _make_ndjson_stdout()

    captured_kwargs: dict = {}

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        await execute_claude(
            prompt_file=prompt_file, context={}, work_dir=str(tmp_path),
            task_id="t1", stage_num=0,
        )

    assert captured_kwargs["env"] is None


# --- _find_result_message ---

def test_find_result_message_returns_result_type() -> None:
    """Finds the message with type='result' in a list."""
    messages = [
        {"type": "assistant", "content": "hello"},
        {"type": "result", "result": "done", "total_cost_usd": 0.1},
    ]
    found = _find_result_message(messages)
    assert found is not None
    assert found["type"] == "result"
    assert found["total_cost_usd"] == 0.1


def test_find_result_message_returns_none_when_missing() -> None:
    """Returns None when no result message exists."""
    messages = [
        {"type": "assistant", "content": "hello"},
        {"type": "system", "subtype": "init"},
    ]
    assert _find_result_message(messages) is None


def test_find_result_message_skips_non_dicts() -> None:
    """Gracefully skips non-dict entries in the message list."""
    messages = ["not a dict", 42, {"type": "result", "result": "ok"}]
    found = _find_result_message(messages)
    assert found is not None
    assert found["result"] == "ok"


def test_find_result_message_empty_list() -> None:
    """Returns None for empty list."""
    assert _find_result_message([]) is None


# --- _extract_from_result_message ---

def test_extract_from_result_message_structured_dict() -> None:
    """Extracts structured_output when it's a dict."""
    msg = {
        "type": "result",
        "structured_output": {"verdict": "pass", "score": 95},
        "result": "Some text",
        "total_cost_usd": 0.5,
    }
    result = _extract_from_result_message(msg)
    assert result["verdict"] == "pass"
    assert result["score"] == 95
    assert result["_cost_usd"] == 0.5


def test_extract_from_result_message_structured_string() -> None:
    """Parses structured_output when it's a JSON string."""
    msg = {
        "type": "result",
        "structured_output": '{"status": "ok", "count": 3}',
        "result": "text",
    }
    result = _extract_from_result_message(msg)
    assert result["status"] == "ok"
    assert result["count"] == 3


def test_extract_from_result_message_structured_invalid_string() -> None:
    """Falls back to result text when structured_output is invalid JSON string."""
    msg = {
        "type": "result",
        "structured_output": "not valid json",
        "result": '{"fallback": true}',
    }
    result = _extract_from_result_message(msg)
    assert result["fallback"] is True


def test_extract_from_result_message_structured_non_dict_json_string() -> None:
    """Falls back when structured_output is valid JSON but not a dict."""
    msg = {
        "type": "result",
        "structured_output": "[1, 2, 3]",
        "result": '{"from_result": true}',
    }
    result = _extract_from_result_message(msg)
    assert result["from_result"] is True


def test_extract_from_result_message_no_structured_no_result() -> None:
    """When no structured_output and no result text, returns full message dict."""
    msg = {"type": "result", "total_cost_usd": 0.1, "duration_ms": 500}
    result = _extract_from_result_message(msg)
    # Falls into the 'elif not msg.get("result") and not structured' branch
    assert result["type"] == "result"
    assert result["total_cost_usd"] == 0.1


def test_extract_from_result_message_empty_result_no_structured() -> None:
    """Empty result string with no structured_output returns raw msg dict."""
    msg = {"type": "result", "result": "", "extra": "data"}
    result = _extract_from_result_message(msg)
    assert result == msg


def test_extract_from_result_message_usage_with_missing_keys() -> None:
    """Usage extraction handles missing optional keys gracefully."""
    msg = {
        "type": "result",
        "structured_output": {"ok": True},
        "usage": {"input_tokens": 100, "output_tokens": 50},
        # cache_read_input_tokens and cache_creation_input_tokens missing
    }
    result = _extract_from_result_message(msg)
    assert result["_input_tokens"] == 100  # 100 + 0 (no cache_read)
    assert result["_output_tokens"] == 50
    assert result["_cache_creation_tokens"] == 0


def test_extract_from_result_message_usage_non_dict_ignored() -> None:
    """Non-dict usage value is silently ignored."""
    msg = {
        "type": "result",
        "structured_output": {"ok": True},
        "usage": "invalid",
    }
    result = _extract_from_result_message(msg)
    assert "_input_tokens" not in result
    assert "_output_tokens" not in result


def test_extract_from_result_message_all_metadata() -> None:
    """All metadata fields are extracted with correct prefixed keys."""
    msg = {
        "type": "result",
        "structured_output": {"data": 1},
        "total_cost_usd": 1.23,
        "usage": {
            "input_tokens": 200,
            "cache_read_input_tokens": 300,
            "output_tokens": 400,
            "cache_creation_input_tokens": 100,
        },
        "duration_ms": 15000,
        "num_turns": 5,
        "session_id": "sess-abc",
    }
    result = _extract_from_result_message(msg)
    assert result["data"] == 1
    assert result["_cost_usd"] == 1.23
    assert result["_input_tokens"] == 500  # 200 + 300
    assert result["_output_tokens"] == 400
    assert result["_cache_creation_tokens"] == 100
    assert result["_duration_ms"] == 15000
    assert result["_num_turns"] == 5
    assert result["_session_id"] == "sess-abc"


def test_extract_from_result_message_no_metadata() -> None:
    """When no metadata keys exist, no underscore-prefixed keys are added."""
    msg = {
        "type": "result",
        "structured_output": {"clean": True},
    }
    result = _extract_from_result_message(msg)
    assert result == {"clean": True}
    assert "_cost_usd" not in result
    assert "_input_tokens" not in result
    assert "_duration_ms" not in result


def test_extract_from_result_message_result_text_plain_no_json() -> None:
    """Plain text result with no extractable JSON marks _no_structured_output."""
    msg = {
        "type": "result",
        "result": "All looks good, no issues found.",
        "total_cost_usd": 0.02,
    }
    result = _extract_from_result_message(msg)
    assert result["_no_structured_output"] is True
    assert "All looks good" in result["_result_text"]
    assert result["_cost_usd"] == 0.02


def test_extract_from_result_message_result_has_none_structured_output() -> None:
    """None structured_output falls through to result text extraction."""
    msg = {
        "type": "result",
        "structured_output": None,
        "result": '{"extracted": true}',
    }
    result = _extract_from_result_message(msg)
    assert result["extracted"] is True


# --- _parse_output: list format fallback paths ---

def test_parse_output_list_no_result_msg_extracts_assistant_text() -> None:
    """Fallback: concatenate assistant text when no result message exists."""
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": '{"from_text": true}'}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["from_text"] is True


def test_parse_output_list_no_result_msg_string_content() -> None:
    """Fallback handles assistant content as a plain string."""
    messages = [
        {"role": "assistant", "content": '{"stringy": true}'},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["stringy"] is True


def test_parse_output_list_no_result_msg_no_json_in_text() -> None:
    """Fallback with no JSON in assistant text returns _no_structured_output."""
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "just words"}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True
    assert "just words" in result["_result_text"]


def test_parse_output_list_no_result_msg_empty_messages() -> None:
    """Empty message list returns _no_structured_output."""
    result = _parse_output(json.dumps([]), "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_list_no_result_no_assistant() -> None:
    """Messages without result or assistant role return _no_structured_output."""
    messages = [{"type": "system", "subtype": "init"}]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_list_fallback_truncates_result_text() -> None:
    """Fallback path truncates _result_text to 2000 chars."""
    long_text = "z" * 5000
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": long_text}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True
    assert len(result["_result_text"]) == 2000


# --- _parse_output: single dict format ---

def test_parse_output_single_dict_with_structured_output() -> None:
    """Single dict (older CLI) with structured_output is extracted."""
    raw = json.dumps({
        "type": "result",
        "structured_output": {"verdict": "pass"},
        "total_cost_usd": 0.3,
        "usage": {"input_tokens": 50, "cache_read_input_tokens": 0, "output_tokens": 75},
    })
    result = _parse_output(raw, "task-001", 0)
    assert result["verdict"] == "pass"
    assert result["_cost_usd"] == 0.3
    assert result["_input_tokens"] == 50
    assert result["_output_tokens"] == 75


# --- _monitor_for_inactivity_stream ---

from aquarco_supervisor.cli.claude import _monitor_for_inactivity_stream

# Save the real asyncio.sleep before any patching happens at module level
_real_sleep = asyncio.sleep


def _make_fake_proc(*, returncode: int | None = None) -> MagicMock:
    """Create a minimal process mock for inactivity monitor tests."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    return proc


@pytest.mark.asyncio
async def test_monitor_stream_returns_false_on_normal_exit() -> None:
    """Monitor returns False when the process exits normally without inactivity kill."""
    proc = _make_fake_proc(returncode=None)
    last_event_time = [asyncio.get_event_loop().time()]
    result_seen = asyncio.Event()

    async def fast_sleep(delay: float) -> None:
        # Simulate process exit on first poll
        proc.returncode = 0
        await _real_sleep(0)

    with patch("asyncio.sleep", side_effect=fast_sleep):
        result = await _monitor_for_inactivity_stream(
            proc,
            last_event_time,
            result_seen,
            inactivity_timeout=5.0,
            poll_interval=0.01,
            task_id="t1",
            stage_num=0,
        )

    assert result is False
    proc.kill.assert_not_called()


@pytest.mark.asyncio
async def test_monitor_stream_kills_after_result_event_stale() -> None:
    """Monitor kills the process after inactivity_timeout once result_seen is set."""
    proc = _make_fake_proc(returncode=None)
    result_seen = asyncio.Event()
    result_seen.set()  # Simulate result event already received

    base_time = 1000.0
    # First call: last_event_time[0] is base_time, elapsed = 0 (no kill)
    # Second call: elapsed = 200 s > inactivity_timeout → kill
    loop_time_values = [base_time, base_time + 200.0]
    loop_time_iter = iter(loop_time_values)
    last_event_time = [base_time]

    async def counted_sleep(delay: float) -> None:
        await _real_sleep(0)

    mock_loop = MagicMock()
    mock_loop.time = MagicMock(side_effect=loop_time_iter)

    with patch("asyncio.sleep", side_effect=counted_sleep), \
         patch("asyncio.get_event_loop", return_value=mock_loop):
        result = await _monitor_for_inactivity_stream(
            proc,
            last_event_time,
            result_seen,
            inactivity_timeout=90.0,
            poll_interval=0.01,
            task_id="t1",
            stage_num=0,
        )

    assert result is True
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_stream_no_kill_without_result_event() -> None:
    """Monitor does NOT kill when result_seen is never set (no result event yet)."""
    proc = _make_fake_proc(returncode=None)
    last_event_time = [asyncio.get_event_loop().time()]
    result_seen = asyncio.Event()  # Never set

    poll_count = 0

    async def counted_sleep(delay: float) -> None:
        nonlocal poll_count
        poll_count += 1
        if poll_count >= 3:
            proc.returncode = 0
        await _real_sleep(0)

    with patch("asyncio.sleep", side_effect=counted_sleep):
        result = await _monitor_for_inactivity_stream(
            proc,
            last_event_time,
            result_seen,
            inactivity_timeout=0.001,  # very short — would trigger if result_seen
            poll_interval=0.01,
            task_id="t1",
            stage_num=0,
        )

    assert result is False
    proc.kill.assert_not_called()

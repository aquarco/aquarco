"""Tests for the Claude CLI invocation wrapper."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.claude import (
    ClaudeOutput,
    _extract_from_result_message,
    _extract_json,
    _find_result_message,
    _parse_output,
    _tail_file,
    execute_claude,
)
from aquarco_supervisor.cli import claude as claude_mod
from aquarco_supervisor.exceptions import AgentExecutionError, AgentTimeoutError


@pytest.fixture(autouse=True)
def _patch_log_dir(tmp_path: Path) -> Any:
    """Redirect _LOG_DIR to tmp_path so tests don't need /var/log/aquarco."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with patch.object(claude_mod, "_LOG_DIR", log_dir):
        yield


# ---------------------------------------------------------------------------
# Helpers for file-based tests
# ---------------------------------------------------------------------------

def _write_ndjson_file(path: Path, *dicts: dict[str, Any]) -> None:
    """Write NDJSON lines to a file (simulates Claude CLI stdout)."""
    with open(path, "w") as f:
        for d in dicts:
            f.write(json.dumps(d) + "\n")


def _make_proc_mock(
    returncode: int | None = 0,
    *,
    wait_result: None = None,
) -> MagicMock:
    """Create a process mock with the given returncode."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=wait_result)
    return proc


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
    """Raises AgentExecutionError when Claude CLI exits with non-zero code and no result event."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")
    stdout_file = tmp_path / "stdout.ndjson"
    stdout_file.write_text("")  # empty — no result event

    mock_proc = _make_proc_mock(returncode=1)

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        # First mkstemp is context file, second is stdout file
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        with pytest.raises(AgentExecutionError, match="Claude CLI exited with code 1"):
            await execute_claude(
                prompt_file=prompt_file,
                context={"task_id": "t1"},
                work_dir=str(tmp_path),
                task_id="t1",
                stage_num=0,
            )


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
    result_line = json.dumps(result_event)

    mock_proc = _make_proc_mock(returncode=0)

    async def fake_tail(path, proc, **kwargs):
        return [result_line], True

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        result = await execute_claude(
            prompt_file=prompt_file,
            context={"task_id": "t1"},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    assert result.structured["complexity"] == "low"
    assert result.structured["summary"] == "Done"
    assert result.raw == result_line


@pytest.mark.asyncio
async def test_execute_claude_result_event_ignores_bad_returncode(tmp_path: Any) -> None:
    """When result event was seen, non-zero returncode is treated as success."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    result_event = {"type": "result", "result": json.dumps({"status": "ok"})}
    result_line = json.dumps(result_event)

    mock_proc = _make_proc_mock(returncode=-9)  # killed

    async def fake_tail(path, proc, **kwargs):
        return [result_line], True

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        result = await execute_claude(
            prompt_file=prompt_file,
            context={"task_id": "t1"},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    assert result.structured["status"] == "ok"


@pytest.mark.asyncio
async def test_execute_claude_passes_allowed_tools(tmp_path: Any) -> None:
    """allowed_tools are forwarded as --allowedTools argument."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        # exit 0 with no result event and no lines → falls through to
        # _parse_ndjson_output which returns _no_structured_output
        result = await execute_claude(
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

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        result = await execute_claude(
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
    """Passes --system-prompt-file with the prompt path."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        result = await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--system-prompt-file" in args_str
    assert str(prompt_file) in args_str


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

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        result = await execute_claude(
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
    result = _parse_output("", "task-001", 0)
    assert "_raw_output" not in result


def test_parse_output_invalid_json_has_no_raw_output_key() -> None:
    result = _parse_output("not json", "task-001", 0)
    assert "_raw_output" not in result


def test_parse_output_plain_text_result_has_no_raw_output_key() -> None:
    raw = json.dumps({"result": "Just some text"})
    result = _parse_output(raw, "task-001", 0)
    assert "_raw_output" not in result
    assert result["_result_text"] == "Just some text"


def test_parse_output_list_format_has_no_raw_output_and_no_parsed_messages() -> None:
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
    long_text = "x" * 5000
    raw = json.dumps({"result": long_text})
    result = _parse_output(raw, "task-001", 0)
    assert result["_no_structured_output"] is True
    assert len(result["_result_text"]) == 2000


def test_parse_output_list_result_text_truncated_to_2000() -> None:
    long_text = "y" * 5000
    messages = [{"type": "result", "result": long_text}]
    raw = json.dumps(messages)
    result = _parse_output(raw, "task-001", 0)
    assert len(result["_result_text"]) == 2000


# --- _find_result_message ---

def test_find_result_message_found() -> None:
    messages = [
        {"role": "assistant", "content": "hi"},
        {"type": "result", "result": "done"},
    ]
    assert _find_result_message(messages) == {"type": "result", "result": "done"}


def test_find_result_message_not_found() -> None:
    assert _find_result_message([{"role": "assistant"}]) is None


def test_find_result_message_empty_list() -> None:
    assert _find_result_message([]) is None


def test_find_result_message_skips_non_dicts() -> None:
    messages = ["string", 42, None, {"type": "result", "result": "ok"}]
    assert _find_result_message(messages) == {"type": "result", "result": "ok"}


# --- _extract_from_result_message ---

def test_extract_structured_output_dict() -> None:
    msg = {"structured_output": {"summary": "done", "issues": []}, "result": "text"}
    result = _extract_from_result_message(msg)
    assert result["summary"] == "done"


def test_extract_structured_output_string() -> None:
    msg = {"structured_output": json.dumps({"verdict": "pass"}), "result": "text"}
    result = _extract_from_result_message(msg)
    assert result["verdict"] == "pass"


def test_extract_structured_output_invalid_string_falls_to_result() -> None:
    msg = {"structured_output": "not json", "result": '{"fallback": true}'}
    result = _extract_from_result_message(msg)
    assert result["fallback"] is True


def test_extract_result_text_json() -> None:
    msg = {"result": '{"status": "ok"}'}
    result = _extract_from_result_message(msg)
    assert result["status"] == "ok"


def test_extract_result_text_plain() -> None:
    msg = {"result": "Everything looks fine."}
    result = _extract_from_result_message(msg)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "Everything looks fine."


def test_extract_no_result_no_structured() -> None:
    msg = {"type": "result", "subtype": "error_max_turns"}
    result = _extract_from_result_message(msg)
    assert result == msg


def test_extract_metadata_fields() -> None:
    msg = {
        "result": '{"ok": true}',
        "subtype": "success",
        "total_cost_usd": 0.5,
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 200, "cache_creation_input_tokens": 10},
        "duration_ms": 3000,
        "num_turns": 5,
        "session_id": "sess-123",
    }
    result = _extract_from_result_message(msg)
    assert result["ok"] is True
    assert result["_subtype"] == "success"
    assert result["_cost_usd"] == 0.5
    assert result["_input_tokens"] == 150
    assert result["_output_tokens"] == 200
    assert result["_cache_creation_tokens"] == 10
    assert result["_duration_ms"] == 3000
    assert result["_num_turns"] == 5
    assert result["_session_id"] == "sess-123"


def test_extract_handles_missing_optional_keys() -> None:
    msg = {"result": '{"ok": true}'}
    result = _extract_from_result_message(msg)
    assert result["ok"] is True
    assert "_subtype" not in result
    assert "_cost_usd" not in result


# --- _tail_file ---

@pytest.mark.asyncio
async def test_tail_file_reads_ndjson_lines(tmp_path: Any) -> None:
    """_tail_file reads all NDJSON lines from a file."""
    stdout_file = tmp_path / "stdout.ndjson"
    events = [
        {"type": "assistant", "content": "hello"},
        {"type": "result", "result": '{"status": "ok"}'},
    ]
    _write_ndjson_file(stdout_file, *events)

    proc = _make_proc_mock(returncode=0)
    lines, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )

    assert len(lines) == 2
    assert result_seen is True
    assert '"result"' in lines[1]


@pytest.mark.asyncio
async def test_tail_file_detects_result_event(tmp_path: Any) -> None:
    """result_seen is True when a {type: 'result'} line is found."""
    stdout_file = tmp_path / "stdout.ndjson"
    _write_ndjson_file(stdout_file, {"type": "result", "result": "done"})

    proc = _make_proc_mock(returncode=0)
    _, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert result_seen is True


@pytest.mark.asyncio
async def test_tail_file_no_result_event(tmp_path: Any) -> None:
    """result_seen is False when no result event in file."""
    stdout_file = tmp_path / "stdout.ndjson"
    _write_ndjson_file(stdout_file, {"type": "assistant", "content": "hi"})

    proc = _make_proc_mock(returncode=0)
    lines, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert result_seen is False
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_tail_file_calls_on_live_output(tmp_path: Any) -> None:
    """on_live_output callback is invoked per NDJSON line."""
    stdout_file = tmp_path / "stdout.ndjson"
    events = [
        {"type": "assistant", "content": "working"},
        {"type": "result", "result": "done"},
    ]
    _write_ndjson_file(stdout_file, *events)

    captured: list[str] = []

    async def on_live(line: str) -> None:
        captured.append(line)

    proc = _make_proc_mock(returncode=0)
    await _tail_file(
        stdout_file, proc,
        on_live_output=on_live,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert len(captured) == 2


@pytest.mark.asyncio
async def test_tail_file_skips_empty_lines(tmp_path: Any) -> None:
    """Empty lines in the file are skipped."""
    stdout_file = tmp_path / "stdout.ndjson"
    with open(stdout_file, "w") as f:
        f.write("\n")
        f.write(json.dumps({"type": "result", "result": "ok"}) + "\n")
        f.write("\n")
        f.write("  \n")

    proc = _make_proc_mock(returncode=0)
    lines, _ = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_tail_file_timeout_kills_process(tmp_path: Any) -> None:
    """Process is killed when timeout expires."""
    stdout_file = tmp_path / "stdout.ndjson"
    stdout_file.write_text("")

    proc = MagicMock()
    proc.returncode = None  # never exits on its own
    proc.kill = MagicMock()
    # After kill, returncode becomes -9
    async def fake_wait():
        proc.returncode = -9
    proc.wait = AsyncMock(side_effect=fake_wait)

    lines, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=0.1,
        task_id="t1", stage_num=0,
    )

    proc.kill.assert_called_once()
    assert result_seen is False


@pytest.mark.asyncio
async def test_tail_file_post_result_grace_terminates(tmp_path: Any) -> None:
    """After result event, process is terminated after grace period."""
    from aquarco_supervisor.cli import claude as claude_mod

    stdout_file = tmp_path / "stdout.ndjson"
    _write_ndjson_file(stdout_file, {"type": "result", "result": "done"})

    proc = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    async def fake_wait():
        proc.returncode = -15
    proc.wait = AsyncMock(side_effect=fake_wait)

    # Use very short grace period for testing
    original_grace = claude_mod._POST_RESULT_GRACE_SECONDS
    claude_mod._POST_RESULT_GRACE_SECONDS = 0.1
    try:
        lines, result_seen = await _tail_file(
            stdout_file, proc,
            timeout_seconds=10.0,
            task_id="t1", stage_num=0,
        )
    finally:
        claude_mod._POST_RESULT_GRACE_SECONDS = original_grace

    assert result_seen is True
    proc.terminate.assert_called_once()
    assert len(lines) == 1


# --- Temp file helper ---

def _make_temp_file(path: Path) -> tuple[int, str]:
    """Create a real temp file and return (fd, path) for mkstemp mock."""
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600)
    return fd, str(path)


# --- Single-dict format (older CLI) ---

def test_parse_output_single_dict_with_structured_output() -> None:
    msg = {
        "type": "result",
        "structured_output": {"summary": "all good"},
        "total_cost_usd": 0.1,
    }
    result = _parse_output(json.dumps(msg), "task-001", 0)
    assert result["summary"] == "all good"
    assert result["_cost_usd"] == 0.1


def test_parse_output_single_dict_with_metadata() -> None:
    msg = {
        "type": "result",
        "result": '{"ok": true}',
        "duration_ms": 5000,
        "num_turns": 7,
    }
    result = _parse_output(json.dumps(msg), "task-001", 0)
    assert result["ok"] is True
    assert result["_duration_ms"] == 5000
    assert result["_num_turns"] == 7


# --- _parse_output list format fallback paths ---

def test_parse_output_list_no_result_extracts_from_assistant_text() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": '{"status": "ok"}'}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["status"] == "ok"


def test_parse_output_list_plain_assistant_text() -> None:
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "Just some text"}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True
    assert "Just some text" in result["_result_text"]


def test_parse_output_list_string_content() -> None:
    messages = [
        {"role": "assistant", "content": '{"inline": true}'},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["inline"] is True


def test_parse_output_list_empty_messages() -> None:
    result = _parse_output(json.dumps([]), "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_list_no_assistant_messages() -> None:
    messages = [{"type": "system", "subtype": "init"}]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True

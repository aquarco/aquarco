"""Tests for the Claude CLI invocation wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from aquarco_supervisor.cli.claude import ClaudeOutput, _extract_json, _parse_output, execute_claude
from aquarco_supervisor.exceptions import AgentExecutionError, AgentTimeoutError

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
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error output"))

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
    mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

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
    mock_proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_claude_returns_parsed_output(tmp_path: Any) -> None:
    """Returns structured output when Claude exits successfully."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    structured = {"complexity": "low", "summary": "Done"}
    raw_json = json.dumps({"result": json.dumps(structured)})

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(raw_json.encode(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open(read_data=raw_json)):
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
    mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))

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
    mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))

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
    mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))

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
    mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))

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
    mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))

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
    raw_json = json.dumps({"result": json.dumps(structured)})

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(raw_json.encode(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open(read_data=raw_json)):
        result = await execute_claude(
            prompt_file=prompt_file,
            context={"task_id": "t1"},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    assert isinstance(result, ClaudeOutput)
    assert result.structured == {"status": "ok"}
    assert result.raw == raw_json


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
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

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

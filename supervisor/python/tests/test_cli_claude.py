"""Tests for the Claude CLI invocation wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from aifishtank_supervisor.cli.claude import _extract_json, _parse_output, execute_claude
from aifishtank_supervisor.exceptions import AgentExecutionError, AgentTimeoutError

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
    assert result["_raw_output"] == ""


def test_parse_output_whitespace_only() -> None:
    result = _parse_output("   \n  ", "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_invalid_json() -> None:
    result = _parse_output("not json at all", "task-001", 0)
    assert result["_no_structured_output"] is True
    assert result["_raw_output"] == "not json at all"


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
    """When result is plain text (no JSON), returns raw output wrapper."""
    raw = json.dumps({"result": "Everything looks fine."})
    result = _parse_output(raw, "task-001", 0)
    assert result["_no_structured_output"] is True
    assert result["_raw_output"] == "Everything looks fine."


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

    assert result["complexity"] == "low"
    assert result["summary"] == "Done"


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

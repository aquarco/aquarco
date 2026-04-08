"""Tests for file-based tailing, NDJSON parsing, and dry-run mode in claude.py.

Covers:
- _tail_file persistent file handle behaviour
- _tail_file partial line handling
- _parse_ndjson_output for stream-json lines
- _is_rate_limited_in_lines detection
- Dry-run mode via CLAUDE_DRY_RUN env var
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.claude import (
    ClaudeOutput,
    _is_rate_limited_in_lines,
    _tail_file,
    execute_claude,
)
from aquarco_supervisor.cli import claude as claude_mod
from aquarco_supervisor.exceptions import AgentExecutionError


@pytest.fixture(autouse=True)
def _patch_log_dir(tmp_path: Path) -> Any:
    """Redirect LOG_DIR to tmp_path so tests don't need /var/log/aquarco."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with patch.object(claude_mod, "LOG_DIR", log_dir):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_ndjson_file(path: Path, *dicts: dict[str, Any]) -> None:
    with open(path, "w") as f:
        for d in dicts:
            f.write(json.dumps(d) + "\n")


def _make_proc_mock(returncode: int | None = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


def _make_temp_file(path: Path) -> tuple[int, str]:
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600)
    return fd, str(path)


# ---------------------------------------------------------------------------
# _tail_file: partial line handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_file_handles_partial_lines(tmp_path: Path) -> None:
    """Partial lines without trailing newline are flushed in the final read."""
    stdout_file = tmp_path / "stdout.ndjson"
    # Write a complete line followed by a partial (no trailing newline)
    with open(stdout_file, "w") as f:
        f.write(json.dumps({"type": "assistant", "content": "line1"}) + "\n")
        f.write(json.dumps({"type": "result", "result": "done"}))  # no trailing \n

    proc = _make_proc_mock(returncode=0)
    tail_lines, result_line, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )

    assert len(tail_lines) == 2
    assert result_seen is True
    assert result_line is not None
    # Verify partial was flushed
    parsed_last = json.loads(tail_lines[1])
    assert parsed_last["type"] == "result"


@pytest.mark.asyncio
async def test_tail_file_handles_cr_lf_lines(tmp_path: Path) -> None:
    """Carriage return characters are stripped from lines."""
    stdout_file = tmp_path / "stdout.ndjson"
    with open(stdout_file, "wb") as f:
        line = json.dumps({"type": "result", "result": "ok"})
        f.write((line + "\r\n").encode("utf-8"))

    proc = _make_proc_mock(returncode=0)
    tail_lines, result_line, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )

    assert len(tail_lines) == 1
    assert result_seen is True
    # Ensure no \r in the stored line
    assert "\r" not in tail_lines[0]


@pytest.mark.asyncio
async def test_tail_file_empty_file_returns_nothing(tmp_path: Path) -> None:
    """An empty stdout file yields no lines and result_seen=False."""
    stdout_file = tmp_path / "stdout.ndjson"
    stdout_file.write_text("")

    proc = _make_proc_mock(returncode=0)
    tail_lines, result_line, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )

    assert tail_lines == []
    assert result_line is None
    assert result_seen is False


@pytest.mark.asyncio
async def test_tail_file_live_output_error_does_not_break(tmp_path: Path) -> None:
    """on_live_output exceptions are swallowed (best-effort)."""
    stdout_file = tmp_path / "stdout.ndjson"
    _write_ndjson_file(stdout_file, {"type": "result", "result": "ok"})

    async def bad_callback(line: str) -> None:
        raise ValueError("callback crash")

    proc = _make_proc_mock(returncode=0)
    tail_lines, result_line, result_seen = await _tail_file(
        stdout_file, proc,
        on_live_output=bad_callback,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )

    # Should still succeed despite callback errors
    assert len(tail_lines) == 1
    assert result_seen is True


@pytest.mark.asyncio
async def test_tail_file_invalid_json_lines_still_collected(tmp_path: Path) -> None:
    """Non-JSON lines are still collected as raw strings."""
    stdout_file = tmp_path / "stdout.ndjson"
    with open(stdout_file, "w") as f:
        f.write("not json at all\n")
        f.write(json.dumps({"type": "result", "result": "done"}) + "\n")

    proc = _make_proc_mock(returncode=0)
    tail_lines, result_line, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )

    assert len(tail_lines) == 2
    assert result_seen is True
    assert tail_lines[0] == "not json at all"


# ---------------------------------------------------------------------------
# _parse_ndjson_output
# ---------------------------------------------------------------------------


def test_parse_ndjson_output_empty_lines() -> None:
    """Empty line list returns _no_structured_output."""
    from aquarco_supervisor.cli.claude import _parse_ndjson_output

    result = _parse_ndjson_output([], "t1", 0)
    assert result["_no_structured_output"] is True


def test_parse_ndjson_output_with_result_event() -> None:
    """Extracts structured output from result event line."""
    from aquarco_supervisor.cli.claude import _parse_ndjson_output

    result_event = {
        "type": "result",
        "subtype": "success",
        "structured_output": {"summary": "all good"},
        "total_cost_usd": 0.5,
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 200},
    }
    lines = [
        json.dumps({"type": "assistant", "content": "working"}),
        json.dumps(result_event),
    ]

    result = _parse_ndjson_output(lines, "t1", 0)
    assert result["summary"] == "all good"
    assert result["_cost_usd"] == 0.5
    assert result["_input_tokens"] == 100
    assert result["_cache_read_tokens"] == 50


def test_parse_ndjson_output_fallback_to_assistant_text() -> None:
    """Falls back to extracting JSON from assistant text blocks."""
    from aquarco_supervisor.cli.claude import _parse_ndjson_output

    lines = [
        json.dumps({
            "role": "assistant",
            "content": [{"type": "text", "text": '{"verdict": "pass"}'}],
        }),
    ]

    result = _parse_ndjson_output(lines, "t1", 0)
    assert result["verdict"] == "pass"


def test_parse_ndjson_output_skips_invalid_json_lines() -> None:
    """Invalid JSON lines are silently skipped."""
    from aquarco_supervisor.cli.claude import _parse_ndjson_output

    lines = [
        "not valid json",
        json.dumps({"type": "result", "result": '{"ok": true}'}),
    ]

    result = _parse_ndjson_output(lines, "t1", 0)
    assert result["ok"] is True


def test_parse_ndjson_output_no_result_no_assistant_text() -> None:
    """When no result and no assistant text, returns _no_structured_output."""
    from aquarco_supervisor.cli.claude import _parse_ndjson_output

    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
    ]

    result = _parse_ndjson_output(lines, "t1", 0)
    assert result["_no_structured_output"] is True


def test_parse_ndjson_output_assistant_string_content() -> None:
    """Handles assistant messages with string content (not list)."""
    from aquarco_supervisor.cli.claude import _parse_ndjson_output

    lines = [
        json.dumps({"role": "assistant", "content": '{"inline": true}'}),
    ]

    result = _parse_ndjson_output(lines, "t1", 0)
    assert result["inline"] is True


# ---------------------------------------------------------------------------
# _is_rate_limited_in_lines
# ---------------------------------------------------------------------------


def test_is_rate_limited_in_lines_detects_rate_limit_error() -> None:
    lines = ['{"error": "rate_limit_error", "message": "too many"}']
    assert _is_rate_limited_in_lines(lines) is True


def test_is_rate_limited_in_lines_detects_429() -> None:
    lines = ['Error: status code 429 received from API']
    assert _is_rate_limited_in_lines(lines) is True


def test_is_rate_limited_in_lines_returns_false_for_normal() -> None:
    lines = ['{"type": "result", "result": "ok"}']
    assert _is_rate_limited_in_lines(lines) is False


def test_is_rate_limited_in_lines_empty() -> None:
    assert _is_rate_limited_in_lines([]) is False


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_dry_run_uses_script(tmp_path: Path) -> None:
    """When CLAUDE_DRY_RUN=1 and the dry-run script exists, the subprocess is
    invoked with the script path (not the 'claude' binary) as its first argument."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list[str] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        # args[0] is the executable, args[1:] are the CLI arguments
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path: Path, proc: Any, **kwargs: Any) -> tuple[list[str], str | None, bool]:
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("aquarco_supervisor.cli.claude.asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"), \
         patch.dict(os.environ, {"CLAUDE_DRY_RUN": "1"}):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={"task_id": "t1"},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    # The real claude-dry-run.sh exists in the repo so the path resolution
    # inside execute_claude should find it and use it as the executable.
    assert len(captured_args) > 0, "create_subprocess_exec was not called"
    assert captured_args[0].endswith("claude-dry-run.sh"), (
        f"Expected first arg to end with 'claude-dry-run.sh', got {captured_args[0]!r}"
    )


@pytest.mark.asyncio
async def test_execute_claude_no_dry_run_uses_claude_binary(tmp_path: Path) -> None:
    """Without CLAUDE_DRY_RUN, the 'claude' binary is used."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"), \
         patch.dict(os.environ, {}, clear=False):
        # Ensure CLAUDE_DRY_RUN is NOT set
        os.environ.pop("CLAUDE_DRY_RUN", None)
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        result = await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    # First captured arg should be "claude" (the default binary)
    assert captured_args[0] == "claude"

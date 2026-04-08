"""Tests for --output-format stream-json implementation.

Covers the acceptance criteria from design document (issue #17):
- _is_rate_limited_in_lines: stdout-based rate-limit detection
- _parse_ndjson_output: NDJSON → structured output parsing
- _tail_file: file-based stdout tailing
- execute_claude: uses stream-json, on_live_output, rate-limit detection
- evaluate_ai_condition: migrated to stream-json
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
    _parse_ndjson_output,
    _tail_file,
    execute_claude,
)
from aquarco_supervisor.cli import claude as claude_mod
from aquarco_supervisor.exceptions import (
    AgentExecutionError,
    RateLimitError,
)


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
    """Write NDJSON lines to a file."""
    with open(path, "w") as f:
        for d in dicts:
            f.write(json.dumps(d) + "\n")


def _make_proc(returncode: int | None = 0) -> MagicMock:
    """Create a minimal process mock."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


def _make_temp_file(path: Path) -> tuple[int, str]:
    """Create a real temp file and return (fd, path) for mkstemp mock."""
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600)
    return fd, str(path)


# ---------------------------------------------------------------------------
# Tests: _is_rate_limited_in_lines (D6)
# ---------------------------------------------------------------------------


def test_is_rate_limited_in_lines_detects_rate_limit_error() -> None:
    lines = [
        json.dumps({"type": "system"}),
        json.dumps({"type": "error", "message": "rate_limit_error occurred"}),
    ]
    assert _is_rate_limited_in_lines(lines) is True


def test_is_rate_limited_in_lines_detects_429() -> None:
    lines = [json.dumps({"error": "status code 429: too many requests"})]
    assert _is_rate_limited_in_lines(lines) is True


def test_is_rate_limited_in_lines_case_insensitive() -> None:
    assert _is_rate_limited_in_lines(["RATE_LIMIT_ERROR in response"]) is True
    assert _is_rate_limited_in_lines(["Hit STATUS CODE 429 from API"]) is True


def test_is_rate_limited_in_lines_returns_false_when_clean() -> None:
    lines = [
        json.dumps({"type": "result", "subtype": "success"}),
        json.dumps({"type": "system"}),
    ]
    assert _is_rate_limited_in_lines(lines) is False


def test_is_rate_limited_in_lines_empty_list() -> None:
    assert _is_rate_limited_in_lines([]) is False


def test_is_rate_limited_partial_strings_no_match() -> None:
    """Substrings that don't form the full marker don't trigger."""
    assert _is_rate_limited_in_lines(["rate_limit is fine"]) is False
    assert _is_rate_limited_in_lines(["status code 200"]) is False


# ---------------------------------------------------------------------------
# Tests: _parse_ndjson_output (D5)
# ---------------------------------------------------------------------------


def test_parse_ndjson_output_empty_lines() -> None:
    result = _parse_ndjson_output([], "task-1", 0)
    assert result.get("_no_structured_output") is True


def test_parse_ndjson_output_finds_result_line() -> None:
    structured = {"verdict": "pass", "score": 95}
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "assistant", "content": [{"type": "text", "text": "thinking..."}]}),
        json.dumps({
            "type": "result",
            "subtype": "success",
            "structured_output": structured,
            "total_cost_usd": 0.05,
            "usage": {"input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 50},
            "duration_ms": 1500,
            "num_turns": 2,
            "session_id": "sess-abc",
        }),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result["verdict"] == "pass"
    assert result["score"] == 95
    assert result["_cost_usd"] == 0.05
    assert result["_input_tokens"] == 100
    assert result["_cache_read_tokens"] == 0
    assert result["_output_tokens"] == 50
    assert result["_duration_ms"] == 1500
    assert result["_num_turns"] == 2
    assert result["_session_id"] == "sess-abc"


def test_parse_ndjson_output_skips_invalid_json_lines() -> None:
    structured = {"ok": True}
    lines = [
        "not valid json at all",
        json.dumps({"type": "result", "structured_output": structured}),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result["ok"] is True


def test_parse_ndjson_output_fallback_to_assistant_text_blocks() -> None:
    lines = [
        json.dumps({
            "role": "assistant",
            "content": [{"type": "text", "text": '{"answer": true, "reasoning": "yes"}'}],
        }),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("answer") is True


def test_parse_ndjson_output_fallback_string_content() -> None:
    lines = [json.dumps({"role": "assistant", "content": '{"key": "val"}'})]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("key") == "val"


def test_parse_ndjson_output_no_result_no_assistant() -> None:
    lines = [json.dumps({"type": "system", "subtype": "init"})]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("_no_structured_output") is True


def test_parse_ndjson_output_only_invalid_lines() -> None:
    lines = ["garbage", "more garbage", "!!!"]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("_no_structured_output") is True


def test_parse_ndjson_output_result_text_truncated_to_2000() -> None:
    long_text = "x" * 5000
    lines = [
        json.dumps({"role": "assistant", "content": [{"type": "text", "text": long_text}]}),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("_no_structured_output") is True
    assert len(result.get("_result_text", "")) == 2000


# ---------------------------------------------------------------------------
# Tests: _tail_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_file_returns_all_non_empty_lines(tmp_path: Path) -> None:
    """Returns all non-empty decoded lines from stdout file."""
    stdout_file = tmp_path / "stdout.ndjson"
    init = {"type": "system", "subtype": "init"}
    result_evt = {"type": "result", "subtype": "success"}
    _write_ndjson_file(stdout_file, init, result_evt)

    proc = _make_proc(returncode=0)
    lines, _, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )

    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "system"
    assert json.loads(lines[1])["type"] == "result"
    assert result_seen is True


@pytest.mark.asyncio
async def test_tail_file_sets_result_seen_event(tmp_path: Path) -> None:
    """Sets result_seen when a {type: 'result'} line arrives."""
    stdout_file = tmp_path / "stdout.ndjson"
    _write_ndjson_file(stdout_file, {"type": "result", "subtype": "success"})

    proc = _make_proc(returncode=0)
    _, _, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert result_seen is True


@pytest.mark.asyncio
async def test_tail_file_does_not_set_result_seen_without_result_line(tmp_path: Path) -> None:
    """result_seen remains False when no result line."""
    stdout_file = tmp_path / "stdout.ndjson"
    _write_ndjson_file(stdout_file, {"type": "system"})

    proc = _make_proc(returncode=0)
    _, _, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert result_seen is False


@pytest.mark.asyncio
async def test_tail_file_calls_on_live_output_per_event(tmp_path: Path) -> None:
    """on_live_output callback is invoked once per NDJSON event."""
    stdout_file = tmp_path / "stdout.ndjson"
    events = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "content": "thinking"},
        {"type": "result", "subtype": "success"},
    ]
    _write_ndjson_file(stdout_file, *events)

    received: list[str] = []
    async def on_output(line: str) -> None:
        received.append(line)

    proc = _make_proc(returncode=0)
    await _tail_file(
        stdout_file, proc,
        on_live_output=on_output,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert len(received) == 3
    assert json.loads(received[0])["type"] == "system"
    assert json.loads(received[2])["type"] == "result"


@pytest.mark.asyncio
async def test_tail_file_skips_empty_lines(tmp_path: Path) -> None:
    """Empty lines in the file are skipped."""
    stdout_file = tmp_path / "stdout.ndjson"
    with open(stdout_file, "w") as f:
        f.write("\n")
        f.write(json.dumps({"type": "result"}) + "\n")
        f.write("\n")
        f.write("  \n")

    proc = _make_proc(returncode=0)
    lines, _, _ = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_tail_file_handles_invalid_json_gracefully(tmp_path: Path) -> None:
    """Invalid JSON lines are collected but don't set result_seen."""
    stdout_file = tmp_path / "stdout.ndjson"
    with open(stdout_file, "w") as f:
        f.write("not valid json\n")
        f.write(json.dumps({"type": "assistant"}) + "\n")

    proc = _make_proc(returncode=0)
    lines, _, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert len(lines) == 2
    assert result_seen is False


@pytest.mark.asyncio
async def test_tail_file_non_dict_json_does_not_set_result_seen(tmp_path: Path) -> None:
    """Non-dict JSON (arrays, strings) don't set result_seen."""
    stdout_file = tmp_path / "stdout.ndjson"
    with open(stdout_file, "w") as f:
        f.write("[1, 2, 3]\n")
        f.write('"just a string"\n')

    proc = _make_proc(returncode=0)
    lines, _, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert len(lines) == 2
    assert result_seen is False


@pytest.mark.asyncio
async def test_tail_file_no_callback_when_none(tmp_path: Path) -> None:
    """Works correctly with on_live_output=None."""
    stdout_file = tmp_path / "stdout.ndjson"
    _write_ndjson_file(stdout_file, {"type": "result", "result": "ok"})

    proc = _make_proc(returncode=0)
    lines, _, _ = await _tail_file(
        stdout_file, proc,
        on_live_output=None,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# Tests: execute_claude uses --output-format stream-json (D1, D8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_uses_stream_json_format(tmp_path: Path) -> None:
    """execute_claude passes --output-format stream-json to Claude CLI."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=0)
    captured_args: list[Any] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        await execute_claude(
            prompt_file=prompt_file, context={},
            work_dir=str(tmp_path), task_id="t1", stage_num=0,
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--output-format stream-json" in args_str
    assert "--output-format json" not in args_str


@pytest.mark.asyncio
async def test_execute_claude_resume_uses_stream_json_format(tmp_path: Path) -> None:
    """Resume branch also uses --output-format stream-json."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=0)
    captured_args: list[Any] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        await execute_claude(
            prompt_file=prompt_file, context={},
            work_dir=str(tmp_path), task_id="t1", stage_num=0,
            resume_session_id="session-xyz",
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--output-format stream-json" in args_str
    assert "--resume" in args_str
    assert "session-xyz" in args_str


@pytest.mark.asyncio
async def test_execute_claude_rate_limit_from_stdout_lines(tmp_path: Path) -> None:
    """Raises RateLimitError when stdout contains rate_limit_error."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=1)
    rate_limit_line = '{"error": "rate_limit_error: too many requests"}'

    async def fake_tail(path, proc, **kwargs):
        return [rate_limit_line], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"), \
         patch("aquarco_supervisor.cli.claude._is_rate_limited", return_value=False):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        with pytest.raises(RateLimitError):
            await execute_claude(
                prompt_file=prompt_file, context={},
                work_dir=str(tmp_path), task_id="t1", stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_rate_limit_from_debug_log_fallback(tmp_path: Path) -> None:
    """Raises RateLimitError from debug log when stdout has no marker."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=1)

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"), \
         patch("aquarco_supervisor.cli.claude._is_rate_limited", return_value=True):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        with pytest.raises(RateLimitError):
            await execute_claude(
                prompt_file=prompt_file, context={},
                work_dir=str(tmp_path), task_id="t1", stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_raw_output_is_joined_ndjson_lines(tmp_path: Path) -> None:
    """raw field of ClaudeOutput is the last 128 KB of the stdout file content."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    init_line = json.dumps({"type": "system", "subtype": "init"})
    result_line = json.dumps({"type": "result", "subtype": "success", "result": '{"x": 1}'})

    mock_proc = _make_proc(returncode=0)

    async def fake_tail(path, proc, **kwargs):
        # Write content to the stdout file so _read_file_tail can read it
        with open(path, "w") as f:
            f.write(init_line + "\n")
            f.write(result_line + "\n")
        return [init_line, result_line], result_line, True

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        output = await execute_claude(
            prompt_file=prompt_file, context={},
            work_dir=str(tmp_path), task_id="t1", stage_num=0,
        )

    assert output.raw is not None
    assert output.raw_output_path is not None
    assert init_line in output.raw
    assert result_line in output.raw


@pytest.mark.asyncio
async def test_execute_claude_result_seen_ignores_bad_returncode(tmp_path: Path) -> None:
    """When result event was captured, non-zero returncode is not an error."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    result_line = json.dumps({"type": "result", "result": '{"ok": true}'})
    mock_proc = _make_proc(returncode=-9)

    async def fake_tail(path, proc, **kwargs):
        return [result_line], result_line, True

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        output = await execute_claude(
            prompt_file=prompt_file, context={},
            work_dir=str(tmp_path), task_id="t1", stage_num=0,
        )

    assert output.structured["ok"] is True


# ---------------------------------------------------------------------------
# Tests: evaluate_ai_condition delegates to execute_claude
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluate_ai_condition_delegates_to_execute_claude(tmp_path: Path) -> None:
    """evaluate_ai_condition delegates to execute_claude and returns structured output."""
    from aquarco_supervisor.cli.claude import ClaudeOutput
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    mock_output = ClaudeOutput(
        structured={"answer": True, "message": "Condition met", "_cost_usd": 0.01},
        raw='{"type":"result","structured_output":{"answer":true,"message":"Condition met"}}',
    )

    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        AsyncMock(return_value=mock_output),
    ) as mock_exec:
        result = await evaluate_ai_condition(
            prompt="Is this good?",
            context={"summary": "test"},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    assert result["answer"] is True
    assert result["message"] == "Condition met"
    assert result["_cost_usd"] == 0.01
    assert "_raw_output" in result
    mock_exec.assert_called_once()

"""Tests for --output-format stream-json implementation.

Covers the acceptance criteria from design document (issue #17):
- _read_stream_json: async NDJSON line reader
- _is_rate_limited_in_lines: stdout-based rate-limit detection
- _parse_ndjson_output: NDJSON → structured output parsing
- execute_claude: uses stream-json, on_live_output, rate-limit detection
- evaluate_ai_condition: migrated to stream-json
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from aquarco_supervisor.cli.claude import (
    ClaudeOutput,
    _is_rate_limited_in_lines,
    _monitor_for_inactivity_stream,
    _parse_ndjson_output,
    _read_stream_json,
    execute_claude,
)
from aquarco_supervisor.exceptions import (
    AgentExecutionError,
    AgentInactivityError,
    RateLimitError,
)


# ---------------------------------------------------------------------------
# Helpers (same pattern as test_cli_claude.py)
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


def _make_ndjson_stdout(*dicts: dict[str, Any]) -> _AsyncLineReader:
    """Create async-iterable mock stdout yielding NDJSON-encoded lines."""
    raw_lines = [(json.dumps(d) + "\n").encode() for d in dicts]
    return _AsyncLineReader(raw_lines)


def _make_proc(returncode: int = 0, stdout: Any = None) -> MagicMock:
    """Create a minimal process mock."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.stdout = stdout or _make_ndjson_stdout()
    return proc


# ---------------------------------------------------------------------------
# Tests: _is_rate_limited_in_lines (D6)
# ---------------------------------------------------------------------------


def test_is_rate_limited_in_lines_detects_rate_limit_error() -> None:
    """Returns True when any line contains 'rate_limit_error'."""
    lines = [
        json.dumps({"type": "system"}),
        json.dumps({"type": "error", "message": "rate_limit_error occurred"}),
    ]
    assert _is_rate_limited_in_lines(lines) is True


def test_is_rate_limited_in_lines_detects_429() -> None:
    """Returns True when any line contains 'status code 429'."""
    lines = [
        json.dumps({"error": "status code 429: too many requests"}),
    ]
    assert _is_rate_limited_in_lines(lines) is True


def test_is_rate_limited_in_lines_case_insensitive() -> None:
    """Detection is case-insensitive (RATE_LIMIT_ERROR, STATUS CODE 429)."""
    lines = ["RATE_LIMIT_ERROR in response"]
    assert _is_rate_limited_in_lines(lines) is True

    lines2 = ["Hit STATUS CODE 429 from API"]
    assert _is_rate_limited_in_lines(lines2) is True


def test_is_rate_limited_in_lines_returns_false_when_clean() -> None:
    """Returns False when no rate-limit markers are present."""
    lines = [
        json.dumps({"type": "result", "subtype": "success"}),
        json.dumps({"type": "system"}),
    ]
    assert _is_rate_limited_in_lines(lines) is False


def test_is_rate_limited_in_lines_empty_list() -> None:
    """Returns False for an empty list."""
    assert _is_rate_limited_in_lines([]) is False


# ---------------------------------------------------------------------------
# Tests: _parse_ndjson_output (D5)
# ---------------------------------------------------------------------------


def test_parse_ndjson_output_empty_lines() -> None:
    """Empty line list returns _no_structured_output sentinel."""
    result = _parse_ndjson_output([], "task-1", 0)
    assert result.get("_no_structured_output") is True


def test_parse_ndjson_output_finds_result_line() -> None:
    """Finds the type=result line and extracts structured_output."""
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
    assert result["_output_tokens"] == 50
    assert result["_duration_ms"] == 1500
    assert result["_num_turns"] == 2
    assert result["_session_id"] == "sess-abc"


def test_parse_ndjson_output_skips_invalid_json_lines() -> None:
    """Invalid JSON lines are silently skipped; result line still found."""
    structured = {"ok": True}
    lines = [
        "not valid json at all",
        json.dumps({"type": "result", "structured_output": structured}),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result["ok"] is True


def test_parse_ndjson_output_fallback_to_assistant_text_blocks() -> None:
    """Falls back to assistant text content when no result line is present."""
    lines = [
        json.dumps({
            "role": "assistant",
            "content": [{"type": "text", "text": '{"answer": true, "reasoning": "yes"}'}],
        }),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("answer") is True


def test_parse_ndjson_output_fallback_string_content() -> None:
    """Falls back to assistant content when content is a plain string."""
    lines = [
        json.dumps({"role": "assistant", "content": '{"key": "val"}'}),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("key") == "val"


def test_parse_ndjson_output_no_result_no_assistant() -> None:
    """Returns _no_structured_output when no result and no assistant messages."""
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("_no_structured_output") is True


def test_parse_ndjson_output_only_invalid_lines() -> None:
    """Returns _no_structured_output when all lines are invalid JSON."""
    lines = ["garbage", "more garbage", "!!!"]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("_no_structured_output") is True


def test_parse_ndjson_output_result_text_truncated_to_2000() -> None:
    """Fallback _result_text is truncated to 2000 characters."""
    long_text = "x" * 5000
    lines = [
        json.dumps({"role": "assistant", "content": [{"type": "text", "text": long_text}]}),
    ]
    result = _parse_ndjson_output(lines, "task-1", 0)
    assert result.get("_no_structured_output") is True
    assert len(result.get("_result_text", "")) == 2000


# ---------------------------------------------------------------------------
# Tests: _read_stream_json (D2, D3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_stream_json_returns_all_non_empty_lines() -> None:
    """Returns all non-empty decoded lines."""
    proc = _make_proc()
    init = {"type": "system", "subtype": "init"}
    result_evt = {"type": "result", "subtype": "success"}
    proc.stdout = _AsyncLineReader([
        (json.dumps(init) + "\n").encode(),
        b"\n",                   # empty line — should be skipped
        (json.dumps(result_evt) + "\n").encode(),
    ])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()

    lines = await _read_stream_json(proc, last_event_time, result_seen, None)

    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "system"
    assert json.loads(lines[1])["type"] == "result"


@pytest.mark.asyncio
async def test_read_stream_json_sets_result_seen_event() -> None:
    """Sets result_seen when a {type: 'result'} line arrives."""
    proc = _make_proc()
    proc.stdout = _AsyncLineReader([
        (json.dumps({"type": "system"}) + "\n").encode(),
        (json.dumps({"type": "result", "subtype": "success"}) + "\n").encode(),
    ])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()

    assert not result_seen.is_set()
    await _read_stream_json(proc, last_event_time, result_seen, None)
    assert result_seen.is_set()


@pytest.mark.asyncio
async def test_read_stream_json_does_not_set_result_seen_without_result_line() -> None:
    """result_seen remains unset if no type=result line arrives."""
    proc = _make_proc()
    proc.stdout = _AsyncLineReader([
        (json.dumps({"type": "system"}) + "\n").encode(),
        (json.dumps({"type": "assistant"}) + "\n").encode(),
    ])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()

    await _read_stream_json(proc, last_event_time, result_seen, None)
    assert not result_seen.is_set()


@pytest.mark.asyncio
async def test_read_stream_json_updates_last_event_time() -> None:
    """Updates last_event_time[0] on each non-empty line received."""
    proc = _make_proc()
    proc.stdout = _AsyncLineReader([
        (json.dumps({"type": "system"}) + "\n").encode(),
        (json.dumps({"type": "result"}) + "\n").encode(),
    ])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()

    with patch("asyncio.get_event_loop") as mock_loop:
        mock_loop.return_value.time.side_effect = [1000.0, 2000.0]
        await _read_stream_json(proc, last_event_time, result_seen, None)

    # After two events, the last recorded time should be the second call's value
    assert last_event_time[0] == 2000.0


@pytest.mark.asyncio
async def test_read_stream_json_calls_on_live_output_per_event() -> None:
    """Calls on_live_output once for each non-empty line (D2: one call per event)."""
    proc = _make_proc()
    events = [
        {"type": "system"},
        {"type": "assistant", "message": "thinking"},
        {"type": "result"},
    ]
    proc.stdout = _AsyncLineReader([(json.dumps(e) + "\n").encode() for e in events])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()

    received: list[str] = []

    async def callback(line: str) -> None:
        received.append(line)

    await _read_stream_json(proc, last_event_time, result_seen, callback)

    assert len(received) == 3
    assert json.loads(received[0])["type"] == "system"
    assert json.loads(received[2])["type"] == "result"


@pytest.mark.asyncio
async def test_read_stream_json_skips_empty_lines() -> None:
    """Empty lines are not appended to returned list and don't trigger callback."""
    proc = _make_proc()
    proc.stdout = _AsyncLineReader([
        b"\n",
        b"   \n",
        (json.dumps({"type": "system"}) + "\n").encode(),
        b"\n",
    ])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()
    received: list[str] = []

    async def callback(line: str) -> None:
        received.append(line)

    lines = await _read_stream_json(proc, last_event_time, result_seen, callback)

    assert len(lines) == 1
    assert len(received) == 1


@pytest.mark.asyncio
async def test_read_stream_json_no_callback_when_none() -> None:
    """When on_live_output is None, reading proceeds without errors."""
    proc = _make_proc()
    proc.stdout = _AsyncLineReader([
        (json.dumps({"type": "system"}) + "\n").encode(),
        (json.dumps({"type": "result"}) + "\n").encode(),
    ])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()

    # Should not raise even with None callback
    lines = await _read_stream_json(proc, last_event_time, result_seen, None)
    assert len(lines) == 2
    assert result_seen.is_set()


# ---------------------------------------------------------------------------
# Tests: execute_claude uses --output-format stream-json (D1, D8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_uses_stream_json_format(tmp_path: Path) -> None:
    """execute_claude passes --output-format stream-json (not json) to Claude CLI."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=0)
    captured_args: list[Any] = []

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
    assert "--output-format stream-json" in args_str
    # Must NOT use the old blocking json format
    assert "--output-format json" not in args_str


@pytest.mark.asyncio
async def test_execute_claude_resume_uses_stream_json_format(tmp_path: Path) -> None:
    """Resume branch also uses --output-format stream-json (D8)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=0)
    captured_args: list[Any] = []

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
            resume_session_id="session-xyz",
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--output-format stream-json" in args_str
    assert "--resume" in args_str
    assert "session-xyz" in args_str


@pytest.mark.asyncio
async def test_execute_claude_on_live_output_called_per_event(tmp_path: Path) -> None:
    """on_live_output callback is invoked once per NDJSON event (D2)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    events = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "content": "thinking"},
        {"type": "result", "subtype": "success", "result": '{"done": true}'},
    ]
    mock_proc = _make_proc(returncode=0, stdout=_make_ndjson_stdout(*events))

    received_lines: list[str] = []

    async def on_output(line: str) -> None:
        received_lines.append(line)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            on_live_output=on_output,
        )

    # Each event should trigger exactly one callback
    assert len(received_lines) == 3
    assert json.loads(received_lines[0])["type"] == "system"
    assert json.loads(received_lines[2])["type"] == "result"


@pytest.mark.asyncio
async def test_execute_claude_rate_limit_from_stdout_lines(tmp_path: Path) -> None:
    """Raises RateLimitError when stdout contains rate_limit_error (D6)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=1, stdout=_AsyncLineReader([
        b'{"error": "rate_limit_error: too many requests"}\n',
    ]))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()), \
         patch("aquarco_supervisor.cli.claude._is_rate_limited", return_value=False):
        with pytest.raises(RateLimitError):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t1",
                stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_rate_limit_from_debug_log_fallback(tmp_path: Path) -> None:
    """Raises RateLimitError from debug log when stdout lines have no marker (D6 fallback)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=1, stdout=_make_ndjson_stdout())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()), \
         patch("aquarco_supervisor.cli.claude._is_rate_limited", return_value=True):
        with pytest.raises(RateLimitError):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t1",
                stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_inactivity_raises_when_no_lines(tmp_path: Path) -> None:
    """Raises AgentInactivityError when killed for inactivity with zero output lines."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    # Proc that never exits normally — inactivity monitor will kill it
    mock_proc = _make_proc(returncode=None, stdout=_make_ndjson_stdout())

    async def fake_inactivity(*args: Any, **kwargs: Any) -> bool:
        """Simulates inactivity kill — sets returncode and returns True."""
        mock_proc.returncode = -9
        return True

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()), \
         patch("aquarco_supervisor.cli.claude._monitor_for_inactivity_stream",
               side_effect=fake_inactivity):
        with pytest.raises(AgentInactivityError):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t1",
                stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_raw_output_is_joined_ndjson_lines(tmp_path: Path) -> None:
    """raw field of ClaudeOutput is the NDJSON lines joined by newlines."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    init_line = json.dumps({"type": "system", "subtype": "init"})
    result_line = json.dumps({"type": "result", "subtype": "success", "result": '{"x": 1}'})
    mock_proc = _make_proc(returncode=0, stdout=_AsyncLineReader([
        (init_line + "\n").encode(),
        (result_line + "\n").encode(),
    ]))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        output = await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    assert output.raw == f"{init_line}\n{result_line}"


# ---------------------------------------------------------------------------
# Tests: evaluate_ai_condition uses stream-json (D7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_ai_condition_uses_stream_json() -> None:
    """evaluate_ai_condition builds args with --output-format stream-json (D7)."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    result_event = {
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": True, "reasoning": "yes"},
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _AsyncLineReader([(json.dumps(result_event) + "\n").encode()])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")

    captured_args: list[Any] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink"):
        result = await evaluate_ai_condition(
            "Is the sky blue?",
            {"some": "context"},
            task_id="t1",
            stage_num=0,
        )

    assert result is True
    args_str = " ".join(str(a) for a in captured_args)
    assert "--output-format stream-json" in args_str


@pytest.mark.asyncio
async def test_evaluate_ai_condition_returns_false_on_nonzero_exit() -> None:
    """evaluate_ai_condition returns False when CLI exits with non-zero code."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _AsyncLineReader([])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"some error")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink"):
        result = await evaluate_ai_condition(
            "Is this true?",
            {},
            task_id="t1",
            stage_num=0,
        )

    assert result is False


@pytest.mark.asyncio
async def test_evaluate_ai_condition_returns_false_on_timeout() -> None:
    """evaluate_ai_condition returns False and kills proc on timeout."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    class _HangingStdout:
        def __aiter__(self) -> "_HangingStdout":
            return self

        async def __anext__(self) -> bytes:
            await asyncio.sleep(3600)
            raise StopAsyncIteration

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _HangingStdout()
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink"):
        result = await evaluate_ai_condition(
            "Will this timeout?",
            {},
            task_id="t1",
            stage_num=0,
            timeout_seconds=1,
        )

    assert result is False
    mock_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_evaluate_ai_condition_parses_ndjson_answer_false() -> None:
    """evaluate_ai_condition returns False when answer field is false."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    result_event = {
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": False, "reasoning": "no evidence"},
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _AsyncLineReader([(json.dumps(result_event) + "\n").encode()])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink"):
        result = await evaluate_ai_condition(
            "Is the moon made of cheese?",
            {},
            task_id="t1",
            stage_num=0,
        )

    assert result is False


# ---------------------------------------------------------------------------
# Tests: _read_stream_json edge cases (lines 55-56)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_stream_json_handles_invalid_json_lines_gracefully() -> None:
    """Non-JSON lines are appended to results but do not set result_seen or raise."""
    proc = _make_proc()
    proc.stdout = _AsyncLineReader([
        b"not-json-at-all\n",
        (json.dumps({"type": "result"}) + "\n").encode(),
    ])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()

    lines = await _read_stream_json(proc, last_event_time, result_seen, None)

    # Both lines captured (invalid JSON is still accumulated)
    assert len(lines) == 2
    assert lines[0] == "not-json-at-all"
    # result_seen set by the valid result line
    assert result_seen.is_set()


@pytest.mark.asyncio
async def test_read_stream_json_non_dict_json_does_not_set_result_seen() -> None:
    """JSON values that are not dicts (e.g. arrays) do not set result_seen."""
    proc = _make_proc()
    proc.stdout = _AsyncLineReader([
        b'["array", "value"]\n',
        b'"just a string"\n',
    ])
    last_event_time: list[float] = [0.0]
    result_seen = asyncio.Event()

    lines = await _read_stream_json(proc, last_event_time, result_seen, None)

    assert len(lines) == 2
    assert not result_seen.is_set()


# ---------------------------------------------------------------------------
# Tests: execute_claude — inactivity kill WITH output lines (lines 293-295)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_inactivity_with_lines_returns_output(tmp_path: Path) -> None:
    """When killed for inactivity but lines exist, returns ClaudeOutput instead of raising."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    result_line = json.dumps({
        "type": "result",
        "subtype": "success",
        "structured_output": {"partial": True},
        "total_cost_usd": 0.01,
    })
    mock_proc = _make_proc(returncode=None, stdout=_AsyncLineReader(
        [(result_line + "\n").encode()]
    ))

    async def fake_inactivity(*args: Any, **kwargs: Any) -> bool:
        """Simulates inactivity kill after result event — sets returncode."""
        mock_proc.returncode = -9
        return True

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()), \
         patch("aquarco_supervisor.cli.claude._monitor_for_inactivity_stream",
               side_effect=fake_inactivity):
        output = await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    # Should return partial output rather than raise AgentInactivityError
    assert isinstance(output, ClaudeOutput)
    assert result_line in output.raw


# ---------------------------------------------------------------------------
# Tests: _is_rate_limited edge cases (lines 533-540)
# ---------------------------------------------------------------------------


def test_is_rate_limited_in_lines_partial_rate_limit_string() -> None:
    """Partial strings that don't match either pattern return False."""
    lines = ["rate_limit_exceeded_for_other_reason"]
    # 'rate_limit_error' is not present, 'status code 429' is not present
    # 'rate_limit_' is not the same as 'rate_limit_error'
    # This tests the exact string matching
    assert _is_rate_limited_in_lines(lines) is False


def test_is_rate_limited_in_lines_status_code_without_429() -> None:
    """'status code 400' does not trigger rate-limit detection."""
    lines = ["status code 400: bad request"]
    assert _is_rate_limited_in_lines(lines) is False


# ---------------------------------------------------------------------------
# Tests: _monitor_for_inactivity_stream (D4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_for_inactivity_stream_returns_false_on_normal_exit() -> None:
    """Returns False when process exits naturally before inactivity timeout."""
    proc = _make_proc(returncode=None)

    last_event_time: list[float] = [asyncio.get_event_loop().time()]
    result_seen = asyncio.Event()
    result_seen.set()

    # Simulate process exiting quickly
    async def set_returncode() -> None:
        proc.returncode = 0

    asyncio.create_task(set_returncode())

    killed = await _monitor_for_inactivity_stream(
        proc,
        last_event_time,
        result_seen,
        inactivity_timeout=30.0,
        poll_interval=0.01,
    )

    assert killed is False


@pytest.mark.asyncio
async def test_monitor_for_inactivity_stream_kills_on_timeout() -> None:
    """Returns True and kills process when inactivity timeout is exceeded."""
    proc = _make_proc(returncode=None)
    # Set last_event_time far in the past to trigger inactivity immediately
    past_time = asyncio.get_event_loop().time() - 1000.0
    last_event_time: list[float] = [past_time]
    result_seen = asyncio.Event()
    result_seen.set()

    killed = await _monitor_for_inactivity_stream(
        proc,
        last_event_time,
        result_seen,
        inactivity_timeout=1.0,
        poll_interval=0.01,
    )

    assert killed is True
    proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_monitor_for_inactivity_stream_waits_for_result_seen() -> None:
    """Does not apply inactivity timeout until result_seen is set."""
    proc = _make_proc(returncode=None)

    # Set last_event_time far in the past (would trigger inactivity if result_seen)
    past_time = asyncio.get_event_loop().time() - 1000.0
    last_event_time: list[float] = [past_time]
    result_seen = asyncio.Event()
    # NOT setting result_seen — monitor should skip inactivity check

    # Set returncode after a couple of polls to exit the monitor
    poll_count = 0

    original_sleep = asyncio.sleep

    async def controlled_sleep(t: float) -> None:
        nonlocal poll_count
        poll_count += 1
        if poll_count >= 2:
            proc.returncode = 0
        await original_sleep(0)

    with patch("asyncio.sleep", side_effect=controlled_sleep):
        killed = await _monitor_for_inactivity_stream(
            proc,
            last_event_time,
            result_seen,
            inactivity_timeout=1.0,
            poll_interval=0.01,
        )

    # Process exited without result_seen ever set → not killed for inactivity
    assert killed is False
    proc.kill.assert_not_called()

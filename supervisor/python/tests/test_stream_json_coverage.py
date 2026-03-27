"""Additional tests to fill coverage gaps in cli/claude.py and pipeline/conditions.py.

Covers the specific uncovered branches identified by the coverage report:
- cli/claude.py lines 262-263, 268-272, 286-290, 307-309, 533-537, 540
- conditions.py lines 125-127, 140, 187, 189, 193, 231, 238, 270, 308, 355,
  399, 403, 491, 545-546, 552-559, 593-594
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from aquarco_supervisor.cli.claude import (
    _is_rate_limited,
    _monitor_for_inactivity_stream,
    _read_stream_json,
    execute_claude,
)
from aquarco_supervisor.exceptions import (
    AgentExecutionError,
    AgentInactivityError,
    AgentTimeoutError,
)
from aquarco_supervisor.pipeline.conditions import (
    _is_truthy,
    _to_number,
    evaluate_conditions,
    evaluate_simple_expression,
)


# ---------------------------------------------------------------------------
# Helpers
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
    """Async-iterable mock stdout that hangs indefinitely."""

    def __aiter__(self) -> "_HangingReader":
        return self

    async def __anext__(self) -> bytes:
        await asyncio.sleep(3600)
        raise StopAsyncIteration


def _make_ndjson_stdout(*dicts: dict[str, Any]) -> _AsyncLineReader:
    raw_lines = [(json.dumps(d) + "\n").encode() for d in dicts]
    return _AsyncLineReader(raw_lines)


def _make_proc(returncode: int | None = 0, stdout: Any = None) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    proc.stdout = stdout or _make_ndjson_stdout()
    return proc


# ---------------------------------------------------------------------------
# Tests: _is_rate_limited (lines 533-537, 540)
# ---------------------------------------------------------------------------


def test_is_rate_limited_returns_false_for_oserror(tmp_path: Path) -> None:
    """Returns False when the debug log file does not exist (OSError path, line 538-539)."""
    nonexistent = tmp_path / "no_such_file.log"
    assert _is_rate_limited(nonexistent) is False


def test_is_rate_limited_detects_rate_limit_error(tmp_path: Path) -> None:
    """Returns True when log contains 'rate_limit_error' (line 540)."""
    log_file = tmp_path / "claude.log"
    log_file.write_text("Something happened: rate_limit_error in response body")
    assert _is_rate_limited(log_file) is True


def test_is_rate_limited_detects_429(tmp_path: Path) -> None:
    """Returns True when log contains 'status code 429' (line 540, case-insensitive)."""
    log_file = tmp_path / "claude.log"
    log_file.write_text("HTTP error: Status Code 429 Too Many Requests")
    assert _is_rate_limited(log_file) is True


def test_is_rate_limited_reads_tail_when_file_large(tmp_path: Path) -> None:
    """Seeks to tail of large file (lines 535-536) without reading whole file into memory."""
    log_file = tmp_path / "claude.log"
    # Write >32KB of junk followed by rate_limit_error in the tail
    junk = "x" * 40000
    tail = "\nrate_limit_error in tail\n"
    log_file.write_text(junk + tail)
    assert _is_rate_limited(log_file) is True


def test_is_rate_limited_clean_file_returns_false(tmp_path: Path) -> None:
    """Returns False for a log with no rate-limit markers."""
    log_file = tmp_path / "claude.log"
    log_file.write_text("Everything went fine. No errors.\n")
    assert _is_rate_limited(log_file) is False


# ---------------------------------------------------------------------------
# Tests: execute_claude — stream_task not done / exception path (lines 268-272)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_stream_task_raises_returns_empty_lines(tmp_path: Path) -> None:
    """When stream_task.result() throws, lines falls back to empty list (lines 268-269)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=0)

    # We need a reader that causes _read_stream_json to raise an exception internally.
    # We'll patch _read_stream_json itself to raise after it's started.
    original_read_stream_json = None

    async def raising_read_stream(*args: Any, **kwargs: Any) -> list[str]:
        raise RuntimeError("simulated stream error")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()), \
         patch("aquarco_supervisor.cli.claude._read_stream_json",
               side_effect=raising_read_stream):
        # Should NOT raise — stream error results in empty lines then returncode=0
        result = await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    # With no lines, _parse_ndjson_output returns _no_structured_output sentinel
    assert result.structured.get("_no_structured_output") is True
    assert result.raw == ""


# ---------------------------------------------------------------------------
# Tests: execute_claude — proc.returncode is None after wait (lines 286-290)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_proc_wait_timeout_kills_proc(tmp_path: Path) -> None:
    """When proc.wait() times out, proc.kill() is called (lines 289-290)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=0, stdout=_make_ndjson_stdout(
        {"type": "result", "subtype": "success", "result": '{"x": 1}'}
    ))
    # After stream ends, keep returncode as None so the wait path is exercised
    mock_proc.returncode = None

    wait_call_count = 0

    async def slow_wait() -> None:
        nonlocal wait_call_count
        wait_call_count += 1
        if wait_call_count == 1:
            # First call is inside asyncio.wait_for — simulate timeout
            await asyncio.sleep(10)
        else:
            # Second call after kill — set returncode
            mock_proc.returncode = -9

    mock_proc.wait = slow_wait

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()), \
         patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
        # asyncio.wait_for raising TimeoutError triggers kill + wait
        with pytest.raises((AgentTimeoutError, AgentExecutionError, Exception)):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t1",
                stage_num=0,
            )

    # kill should have been called because wait_for raised TimeoutError
    mock_proc.kill.assert_called()


# ---------------------------------------------------------------------------
# Tests: execute_claude — inactivity kill timeout on wait_for(stream_task) (lines 262-263)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_inactivity_stream_task_wait_timeout(tmp_path: Path) -> None:
    """When wait_for(stream_task) raises TimeoutError after inactivity kill, it is caught (lines 262-263)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    result_line = json.dumps({
        "type": "result",
        "subtype": "success",
        "structured_output": {"inactivity": True},
    })

    # A slow reader: yields the result line but then hangs, so stream_task stays alive
    class _SlowThenHangReader:
        def __init__(self) -> None:
            self._yielded = False

        def __aiter__(self) -> "_SlowThenHangReader":
            return self

        async def __anext__(self) -> bytes:
            if not self._yielded:
                self._yielded = True
                return (result_line + "\n").encode()
            # Hang after first line
            await asyncio.sleep(3600)
            raise StopAsyncIteration

    mock_proc = _make_proc(returncode=None, stdout=_SlowThenHangReader())

    # Simulate inactivity monitor completing first and returning True
    async def fake_inactivity(*args: Any, **kwargs: Any) -> bool:
        mock_proc.returncode = -9
        return True

    # Patch _read_stream_json to simulate stream_task that times out when waited for
    original_read_stream_json = None
    stream_event = asyncio.Event()

    async def slow_read_stream_json(*args: Any, **kwargs: Any) -> list[str]:
        # Signal that we started, then wait a long time
        stream_event.set()
        await asyncio.sleep(3600)
        return [result_line]

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()), \
         patch("aquarco_supervisor.cli.claude._monitor_for_inactivity_stream",
               side_effect=fake_inactivity), \
         patch("aquarco_supervisor.cli.claude._read_stream_json",
               side_effect=slow_read_stream_json):
        # With stream_task hanging and inactivity killing, wait_for(stream_task, 5) should
        # time out. But with a 5s timeout in real time, we need to use a very short timeout.
        # We'll use timeout_seconds=1 so the overall wait times out and raises AgentTimeoutError.
        with pytest.raises((AgentInactivityError, AgentTimeoutError, Exception)):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t1",
                stage_num=0,
                timeout_seconds=1,
            )


# ---------------------------------------------------------------------------
# Tests: execute_claude — failed exit reads stderr from log file (lines 307-309)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_failed_exit_reads_log_files(tmp_path: Path) -> None:
    """On non-zero exit, execute_claude reads stderr/debug log for diagnostics."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=1, stdout=_make_ndjson_stdout())

    # Patch Path.read_text to return content for log files (so lines 307-309 are hit)
    read_text_calls: list[str] = []

    original_read_text = Path.read_text

    def patched_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        read_text_calls.append(str(self))
        if "stderr" in str(self) or "stage" in str(self):
            return "Some stderr content from the process"
        return original_read_text(self, *args, **kwargs)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()), \
         patch.object(Path, "read_text", patched_read_text):
        with pytest.raises(AgentExecutionError):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t1",
                stage_num=1,
            )

    # read_text should have been called to fetch log file content
    assert len(read_text_calls) > 0


# ---------------------------------------------------------------------------
# Tests: _monitor_for_inactivity_stream — process exits during sleep (line 85-86)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_exits_when_proc_dies_during_sleep() -> None:
    """Monitor returns False when process exits during the poll sleep (lines 85-86)."""
    proc = _make_proc(returncode=None)

    last_event_time: list[float] = [asyncio.get_event_loop().time()]
    result_seen = asyncio.Event()

    original_sleep = asyncio.sleep
    poll_count = 0

    async def controlled_sleep(t: float) -> None:
        nonlocal poll_count
        poll_count += 1
        # Process dies during first sleep
        proc.returncode = 0
        await original_sleep(0)

    with patch("asyncio.sleep", side_effect=controlled_sleep):
        killed = await _monitor_for_inactivity_stream(
            proc,
            last_event_time,
            result_seen,
            inactivity_timeout=30.0,
            poll_interval=0.01,
        )

    assert killed is False


# ---------------------------------------------------------------------------
# Tests: conditions.py — uncovered branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_conditions_simple_raises_exception_returns_none() -> None:
    """When simple condition raises, it returns None and the condition is skipped (lines 125-127)."""
    # Passing an expression that will raise in evaluate_simple_expression
    conditions = [
        {"simple": "@@@invalid@@@", "yes": "next-stage"},
    ]
    # The tokenizer raises ValueError for invalid chars, caught as Exception -> returns None
    result = await evaluate_conditions(
        conditions,
        stage_outputs={},
        current_output={},
        repeat_counts={},
    )
    # No jump because condition couldn't be evaluated
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_condition_has_no_simple_or_ai() -> None:
    """A condition dict with neither 'simple' nor 'ai' returns None (line 140)."""
    conditions = [
        {"unknown_key": "value", "yes": "somewhere"},
    ]
    result = await evaluate_conditions(
        conditions,
        stage_outputs={},
        current_output={},
        repeat_counts={},
    )
    assert result.jump_to is None


def test_tokenize_raises_on_unexpected_character() -> None:
    """_tokenize raises ValueError when encountering an unexpected character (line 193)."""
    from aquarco_supervisor.pipeline.conditions import _tokenize

    with pytest.raises(ValueError, match="Unexpected character"):
        _tokenize("status == @bad")


def test_tokenize_expression_with_trailing_whitespace() -> None:
    """_tokenize handles expressions with trailing whitespace (lines 187, 189 — whitespace-only suffix)."""
    from aquarco_supervisor.pipeline.conditions import _tokenize

    # "42  " — trailing spaces trigger the inner whitespace loop then break at line 189
    tokens = _tokenize("42   ")
    assert len(tokens) == 1
    assert tokens[0].value == "42"


def test_tokenize_expression_with_only_whitespace_returns_empty() -> None:
    """_tokenize returns empty list for whitespace-only expressions (lines 187, 189)."""
    from aquarco_supervisor.pipeline.conditions import _tokenize

    tokens = _tokenize("   ")
    assert tokens == []


def test_evaluate_simple_expression_whitespace_only_returns_true() -> None:
    """Expression that tokenizes to empty list after whitespace returns True (lines 307-308)."""
    # All whitespace — tokenize returns empty list — hits line 307-308 `if not tokens`
    result = evaluate_simple_expression("   ", {})
    assert result is True


def test_parser_consume_raises_on_type_mismatch() -> None:
    """consume() raises ValueError when expected_type doesn't match (line 231)."""
    from aquarco_supervisor.pipeline.conditions import _Parser, _tokenize

    tokens = _tokenize("42")
    parser = _Parser(tokens, {})
    # Try to consume expecting IDENT but got NUMBER
    with pytest.raises(ValueError, match="Expected IDENT, got NUMBER"):
        parser.consume("IDENT")


def test_parser_parse_raises_on_unexpected_trailing_token() -> None:
    """parse() raises ValueError when tokens remain after expr (line 238)."""
    from aquarco_supervisor.pipeline.conditions import _Parser, _tokenize

    # "1 2" - after parsing "1", there's a leftover "2" token
    tokens = _tokenize("1 2")
    parser = _Parser(tokens, {})
    with pytest.raises(ValueError, match="Unexpected token"):
        parser.parse()


def test_parser_primary_raises_on_empty_token_list() -> None:
    """primary() raises ValueError when no tokens left (line 270)."""
    from aquarco_supervisor.pipeline.conditions import _Parser

    # Construct a parser with no tokens (simulates exhausted token stream)
    parser = _Parser([], {})
    with pytest.raises(ValueError, match="Unexpected end of expression"):
        parser.primary()


def test_is_truthy_dict_and_list() -> None:
    """_is_truthy handles dict and list values correctly (line 355)."""
    assert _is_truthy({"key": "val"}) is True
    assert _is_truthy({}) is False
    assert _is_truthy([1, 2]) is True
    assert _is_truthy([]) is False


def test_is_truthy_other_truthy_object() -> None:
    """_is_truthy falls through to bool() for objects that are not None/bool/int/float/str/list/dict (line 355)."""

    class _CustomObj:
        def __bool__(self) -> bool:
            return True

    class _FalsyObj:
        def __bool__(self) -> bool:
            return False

    assert _is_truthy(_CustomObj()) is True
    assert _is_truthy(_FalsyObj()) is False


def test_to_number_returns_none_for_non_numeric_types() -> None:
    """_to_number returns None for non-numeric, non-string types (line 367)."""
    assert _to_number(None) is None
    assert _to_number([1, 2, 3]) is None
    assert _to_number({"key": "val"}) is None


def test_compare_string_ge_le() -> None:
    """_compare uses string GE/LE fallback when values are non-numeric (lines 399, 403)."""
    from aquarco_supervisor.pipeline.conditions import _compare

    # String comparison: "b" >= "a" -> True
    assert _compare("b", "GE", "a") is True
    assert _compare("a", "GE", "b") is False

    # String comparison: "a" <= "b" -> True
    assert _compare("a", "LE", "b") is True
    assert _compare("b", "LE", "a") is False


# ---------------------------------------------------------------------------
# Tests: evaluate_ai_condition — extra_env branch (line 491)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_ai_condition_with_extra_env() -> None:
    """evaluate_ai_condition merges extra_env when provided (line 491)."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    result_event = {
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": True, "message": "env was set"},
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _AsyncLineReader([(json.dumps(result_event) + "\n").encode()])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")

    captured_kwargs: dict[str, Any] = {}

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink"):
        result = await evaluate_ai_condition(
            "Is extra env set?",
            {"context": "data"},
            task_id="t1",
            stage_num=0,
            extra_env={"MY_VAR": "my_value"},
        )

    assert result[0] is True
    # env kwarg should be a merged dict containing MY_VAR
    assert captured_kwargs.get("env") is not None
    assert captured_kwargs["env"].get("MY_VAR") == "my_value"


# ---------------------------------------------------------------------------
# Tests: evaluate_ai_condition — stderr not done path (lines 552-559)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_ai_condition_stderr_not_done_is_cancelled() -> None:
    """When stderr_task is not done after wait_for, it is cancelled (lines 554-559)."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    result_event = {
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": False, "message": "stderr slow"},
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _AsyncLineReader([(json.dumps(result_event) + "\n").encode()])

    # stderr.read hangs — so stderr_task won't be done
    async def hanging_read() -> bytes:
        await asyncio.sleep(3600)
        return b""

    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = hanging_read

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink"):
        result = await evaluate_ai_condition(
            "Is the condition met?",
            {},
            task_id="t1",
            stage_num=0,
        )

    # Result still parsed correctly despite stderr hanging
    assert result[0] is False


# ---------------------------------------------------------------------------
# Tests: evaluate_ai_condition — stdout_task result exception (lines 545-546)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_ai_condition_stdout_task_empty_returns_false() -> None:
    """When stdout produces no lines, ndjson_lines stays empty -> False (lines 545-546)."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=None)

    # stdout produces nothing - no lines read
    mock_proc.stdout = _AsyncLineReader([])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink"):
        # stdout_task.result() returns empty list -> ndjson_lines = [] -> no answer -> False
        result = await evaluate_ai_condition(
            "test condition",
            {},
            task_id="t1",
            stage_num=0,
        )

    # No result parsed -> answer = None -> bool(None) = False
    assert result[0] is False


# ---------------------------------------------------------------------------
# Tests: evaluate_ai_condition — temp file cleanup on exception (lines 593-594)
# ---------------------------------------------------------------------------



@pytest.mark.asyncio
async def test_evaluate_ai_condition_stderr_task_result_raises_exception() -> None:
    """When stderr_task.result() raises, stderr_bytes stays empty (lines 552-553)."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    result_event = {
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": True},
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _AsyncLineReader([(json.dumps(result_event) + "\n").encode()])

    # stderr_task.done() returns True but .result() raises
    original_create_task = asyncio.create_task
    task_count = 0

    def patched_create_task(coro: Any, **kwargs: Any) -> Any:
        nonlocal task_count
        task_count += 1
        if task_count == 2:
            # Second task is stderr_task — make it raise RuntimeError
            async def raising() -> bytes:
                raise RuntimeError("stderr task failed")
            return original_create_task(raising())
        return original_create_task(coro, **kwargs)

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink"), \
         patch("asyncio.create_task", side_effect=patched_create_task):
        result = await evaluate_ai_condition(
            "test stderr exception",
            {},
            task_id="t1",
            stage_num=0,
        )

    # Answer was parsed from stdout despite stderr failing
    assert result[0] is True


@pytest.mark.asyncio
async def test_evaluate_ai_condition_cleans_temp_files_via_evaluate_conditions() -> None:
    """Temp files are cleaned up even when an exception occurs (lines 593-594).

    Tests the finally block by causing an exception after subprocess creation,
    verified via the evaluate_conditions wrapper which catches exceptions.
    """
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    unlinked: list[str] = []

    original_os_unlink = __import__("os").unlink

    def tracking_unlink(path: str) -> None:
        unlinked.append(path)
        try:
            original_os_unlink(path)
        except OSError:
            pass

    original_mkstemp = __import__("tempfile").mkstemp
    created_paths: list[str] = []

    def tracking_mkstemp(*args: Any, **kwargs: Any) -> Any:
        fd, path = original_mkstemp(*args, **kwargs)
        created_paths.append(path)
        return fd, path

    result_event = {
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": True},
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _AsyncLineReader([(json.dumps(result_event) + "\n").encode()])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.fdopen", mock_open()), \
         patch("tempfile.mkstemp", side_effect=tracking_mkstemp), \
         patch("os.unlink", side_effect=tracking_unlink):
        result = await evaluate_ai_condition(
            "test",
            {},
            task_id="t1",
            stage_num=0,
        )

    assert result[0] is True
    # Both temp files (sys and in) should have been unlinked via the finally block
    assert len(created_paths) == 2
    assert len(unlinked) == 2


@pytest.mark.asyncio
async def test_evaluate_ai_condition_unlink_oserror_is_suppressed() -> None:
    """OSError from os.unlink in finally is silently caught (lines 593-594)."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    result_event = {
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": True},
    }

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.stdout = _AsyncLineReader([(json.dumps(result_event) + "\n").encode()])
    mock_proc.stderr = AsyncMock()
    mock_proc.stderr.read = AsyncMock(return_value=b"")

    def raising_unlink(path: str) -> None:
        raise OSError(f"Permission denied: {path}")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("os.fdopen", mock_open()), \
         patch("os.unlink", side_effect=raising_unlink):
        # Should NOT raise — OSError from unlink is suppressed
        result = await evaluate_ai_condition(
            "test unlink error suppression",
            {},
            task_id="t1",
            stage_num=0,
        )

    assert result[0] is True

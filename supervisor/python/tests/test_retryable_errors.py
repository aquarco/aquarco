"""Tests for retryable error detection helpers and execute_claude() error routing.

Covers the acceptance criteria from the design:
  AC-6  through AC-12: _is_server_error_in_lines / _is_overloaded_in_lines
  AC-13 through AC-16: execute_claude() raises the correct exception type
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

from aquarco_supervisor.cli.claude import (
    _is_overloaded,
    _is_overloaded_in_lines,
    _is_rate_limited,
    _is_rate_limited_in_lines,
    _is_server_error,
    _is_server_error_in_lines,
    execute_claude,
)
from aquarco_supervisor.exceptions import (
    AgentExecutionError,
    OverloadedError,
    RateLimitError,
    ServerError,
)


# ---------------------------------------------------------------------------
# _is_server_error_in_lines (AC-6, AC-7, AC-8, AC-9)
# ---------------------------------------------------------------------------


def test_is_server_error_in_lines_api_error_type() -> None:
    """AC-6: detects 'api_error' JSON payload."""
    lines = ['{"error":{"type":"api_error","message":"Internal server error"}}']
    assert _is_server_error_in_lines(lines) is True


def test_is_server_error_in_lines_status_code_500() -> None:
    """AC-7: detects 'status code 500' text."""
    lines = ["status code 500 encountered"]
    assert _is_server_error_in_lines(lines) is True


def test_is_server_error_in_lines_success_result_returns_false() -> None:
    """AC-8: does NOT fire on a success result line."""
    lines = ['{"type":"result","subtype":"success"}']
    assert _is_server_error_in_lines(lines) is False


def test_is_server_error_in_lines_rate_limit_no_cross_contamination() -> None:
    """AC-9: 'rate_limit_error' does not trigger server-error detection."""
    lines = ['{"error":{"type":"rate_limit_error"}}']
    assert _is_server_error_in_lines(lines) is False


def test_is_server_error_in_lines_empty() -> None:
    assert _is_server_error_in_lines([]) is False


def test_is_server_error_in_lines_case_insensitive() -> None:
    """Detection is case-insensitive (lowercased internally).

    Note: bare unquoted 'api_error' text does NOT match; the token must appear as
    a JSON string value (with surrounding double-quotes) to avoid false positives
    when agent output text discusses API error handling.
    """
    assert _is_server_error_in_lines(['{"error":{"type":"API_ERROR"}}']) is True
    assert _is_server_error_in_lines(["Status Code 500"]) is True
    # Unquoted plain text must NOT produce a false positive
    assert _is_server_error_in_lines(["api_error discussed in prose"]) is False


# ---------------------------------------------------------------------------
# _is_overloaded_in_lines (AC-10, AC-11, AC-12)
# ---------------------------------------------------------------------------


def test_is_overloaded_in_lines_overloaded_error_type() -> None:
    """AC-10: detects 'overloaded_error' JSON payload."""
    lines = ['{"error":{"type":"overloaded_error","message":"Overloaded"}}']
    assert _is_overloaded_in_lines(lines) is True


def test_is_overloaded_in_lines_status_code_529() -> None:
    """AC-11: detects 'status code 529' text."""
    lines = ["status code 529"]
    assert _is_overloaded_in_lines(lines) is True


def test_is_overloaded_in_lines_plain_result_returns_false() -> None:
    """AC-12: does NOT fire on a plain result line."""
    lines = ['{"type":"result"}']
    assert _is_overloaded_in_lines(lines) is False


def test_is_overloaded_in_lines_empty() -> None:
    assert _is_overloaded_in_lines([]) is False


def test_is_overloaded_in_lines_api_error_no_cross_contamination() -> None:
    """'api_error' (500) does not trigger overloaded detection."""
    lines = ['{"error":{"type":"api_error"}}']
    assert _is_overloaded_in_lines(lines) is False


def test_is_overloaded_in_lines_case_insensitive() -> None:
    assert _is_overloaded_in_lines(["OVERLOADED_ERROR happened"]) is True
    assert _is_overloaded_in_lines(["Status Code 529"]) is True


# ---------------------------------------------------------------------------
# _is_rate_limited_in_lines (regression guards — AC-15 support)
# ---------------------------------------------------------------------------


def test_is_rate_limited_in_lines_detects_rate_limit_error() -> None:
    lines = ['{"error":{"type":"rate_limit_error"}}']
    assert _is_rate_limited_in_lines(lines) is True


def test_is_rate_limited_in_lines_detects_status_429() -> None:
    lines = ["status code 429"]
    assert _is_rate_limited_in_lines(lines) is True


def test_is_rate_limited_in_lines_does_not_fire_on_api_error() -> None:
    lines = ['{"error":{"type":"api_error"}}']
    assert _is_rate_limited_in_lines(lines) is False


# ---------------------------------------------------------------------------
# _is_server_error / _is_overloaded / _is_rate_limited (debug log readers)
# ---------------------------------------------------------------------------


def test_is_server_error_returns_false_when_log_missing(tmp_path: Any) -> None:
    assert _is_server_error(tmp_path / "nonexistent.log") is False


def test_is_overloaded_returns_false_when_log_missing(tmp_path: Any) -> None:
    assert _is_overloaded(tmp_path / "nonexistent.log") is False


def test_is_rate_limited_returns_false_when_log_missing(tmp_path: Any) -> None:
    assert _is_rate_limited(tmp_path / "nonexistent.log") is False


def test_is_server_error_detects_api_error_in_log(tmp_path: Any) -> None:
    log = tmp_path / "debug.log"
    # Must use JSON-quoted form to match (bare 'api_error' is a false-positive guard)
    log.write_text('{"error":{"type":"api_error","message":"Internal server error"}}')
    assert _is_server_error(log) is True


def test_is_server_error_unquoted_api_error_no_false_positive(tmp_path: Any) -> None:
    """Plain prose 'api_error' in a debug log must NOT trigger server-error detection."""
    log = tmp_path / "debug.log"
    log.write_text("Agent discussed api_error handling in its reasoning")
    assert _is_server_error(log) is False


def test_is_server_error_detects_status_500_in_log(tmp_path: Any) -> None:
    log = tmp_path / "debug.log"
    log.write_text("status code 500 encountered")
    assert _is_server_error(log) is True


def test_is_overloaded_detects_overloaded_error_in_log(tmp_path: Any) -> None:
    log = tmp_path / "debug.log"
    log.write_text("overloaded_error hit")
    assert _is_overloaded(log) is True


def test_is_overloaded_detects_status_529_in_log(tmp_path: Any) -> None:
    log = tmp_path / "debug.log"
    log.write_text("status code 529")
    assert _is_overloaded(log) is True


def test_is_server_error_no_false_positive_on_clean_log(tmp_path: Any) -> None:
    log = tmp_path / "debug.log"
    log.write_text("All good, session completed successfully.")
    assert _is_server_error(log) is False


def test_is_overloaded_no_false_positive_on_clean_log(tmp_path: Any) -> None:
    log = tmp_path / "debug.log"
    log.write_text("All good, session completed successfully.")
    assert _is_overloaded(log) is False


# ---------------------------------------------------------------------------
# execute_claude() exception routing (AC-13, AC-14, AC-15, AC-16)
# ---------------------------------------------------------------------------

# Helper: build a mock process that exits with given returncode and stdout lines
def _make_mock_proc(returncode: int, ndjson_lines: list[str]) -> MagicMock:
    """Build a mock asyncio.Process with preset returncode and NDJSON stdout."""

    class _AsyncLineReader:
        def __init__(self, lines: list[bytes]) -> None:
            self._data = list(lines)

        def __aiter__(self) -> "_AsyncLineReader":
            return self

        async def __anext__(self) -> bytes:
            if not self._data:
                raise StopAsyncIteration
            return self._data.pop(0)

    mock_proc = MagicMock()
    mock_proc.returncode = returncode
    encoded = [f"{line}\n".encode() for line in ndjson_lines]
    mock_proc.stdout = _AsyncLineReader(encoded)
    mock_proc.wait = AsyncMock(return_value=returncode)
    mock_proc.kill = MagicMock()
    return mock_proc


# Re-use the AsyncMock import
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_execute_claude_raises_server_error_on_api_error_stdout(tmp_path: Any) -> None:
    """AC-13: ServerError raised when stdout contains 'api_error' and returncode != 0."""
    prompt_file = tmp_path / "sys.md"
    prompt_file.write_text("agent prompt")

    ndjson = ['{"error":{"type":"api_error","message":"Internal"}}']
    mock_proc = _make_mock_proc(returncode=1, ndjson_lines=ndjson)

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        with pytest.raises(ServerError):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t-500",
                stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_raises_overloaded_error_on_overloaded_stdout(tmp_path: Any) -> None:
    """AC-14: OverloadedError raised when stdout contains 'overloaded_error' and returncode != 0."""
    prompt_file = tmp_path / "sys.md"
    prompt_file.write_text("agent prompt")

    ndjson = ['{"error":{"type":"overloaded_error","message":"Overloaded"}}']
    mock_proc = _make_mock_proc(returncode=1, ndjson_lines=ndjson)

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        with pytest.raises(OverloadedError):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t-529",
                stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_raises_rate_limit_error_regression(tmp_path: Any) -> None:
    """AC-15: RateLimitError still raised on 'rate_limit_error' stdout (regression guard)."""
    prompt_file = tmp_path / "sys.md"
    prompt_file.write_text("agent prompt")

    ndjson = ['{"error":{"type":"rate_limit_error","message":"Rate limited"}}']
    mock_proc = _make_mock_proc(returncode=1, ndjson_lines=ndjson)

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        with pytest.raises(RateLimitError):
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t-429",
                stage_num=0,
            )


@pytest.mark.asyncio
async def test_execute_claude_raises_agent_execution_error_on_unknown_failure(tmp_path: Any) -> None:
    """AC-16: generic AgentExecutionError raised when no recognized pattern and returncode != 0."""
    prompt_file = tmp_path / "sys.md"
    prompt_file.write_text("agent prompt")

    ndjson = ['{"type":"system","subtype":"init"}']
    mock_proc = _make_mock_proc(returncode=1, ndjson_lines=ndjson)

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open()):
        with pytest.raises(AgentExecutionError) as exc_info:
            await execute_claude(
                prompt_file=prompt_file,
                context={},
                work_dir=str(tmp_path),
                task_id="t-unknown",
                stage_num=0,
            )
        # Must NOT be one of the retryable subclasses
        assert not isinstance(exc_info.value, (RateLimitError, ServerError, OverloadedError))


# ---------------------------------------------------------------------------
# Ordering: rate-limit checked BEFORE server-error (no cross-contamination)
# ---------------------------------------------------------------------------


def test_server_error_does_not_match_rate_limit_patterns() -> None:
    """A line with only 'api_error' must not trigger rate-limit detection."""
    line = '{"error":{"type":"api_error"}}'
    assert _is_rate_limited_in_lines([line]) is False
    assert _is_server_error_in_lines([line]) is True


def test_overloaded_error_does_not_match_rate_limit_or_server_patterns() -> None:
    """A line with only 'overloaded_error' must not trigger rate-limit or server detection."""
    line = '{"error":{"type":"overloaded_error"}}'
    assert _is_rate_limited_in_lines([line]) is False
    assert _is_server_error_in_lines([line]) is False
    assert _is_overloaded_in_lines([line]) is True

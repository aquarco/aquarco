"""Additional tests to fill coverage gaps in cli/claude.py and pipeline/conditions.py.

Covers the specific uncovered branches identified by the coverage report.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from aquarco_supervisor.cli.claude import (
    _is_rate_limited,
    _tail_file,
    execute_claude,
)
from aquarco_supervisor.cli import claude as claude_mod
from aquarco_supervisor.exceptions import (
    AgentExecutionError,
    AgentTimeoutError,
)


@pytest.fixture(autouse=True)
def _patch_log_dir(tmp_path: Path) -> Any:
    """Redirect LOG_DIR to tmp_path so tests don't need /var/log/aquarco."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with patch.object(claude_mod, "LOG_DIR", log_dir):
        yield
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


def _make_proc(returncode: int | None = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


def _make_temp_file(path: Path) -> tuple[int, str]:
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600)
    return fd, str(path)


def _write_ndjson_file(path: Path, *dicts: dict[str, Any]) -> None:
    with open(path, "w") as f:
        for d in dicts:
            f.write(json.dumps(d) + "\n")


# ---------------------------------------------------------------------------
# Tests: _is_rate_limited (debug log file)
# ---------------------------------------------------------------------------


def test_is_rate_limited_returns_false_for_oserror(tmp_path: Path) -> None:
    nonexistent = tmp_path / "no_such_file.log"
    assert _is_rate_limited(nonexistent) is False


def test_is_rate_limited_detects_rate_limit_error(tmp_path: Path) -> None:
    log_file = tmp_path / "claude.log"
    log_file.write_text("Something happened: rate_limit_error in response body")
    assert _is_rate_limited(log_file) is True


def test_is_rate_limited_detects_429(tmp_path: Path) -> None:
    log_file = tmp_path / "claude.log"
    log_file.write_text("HTTP error: Status Code 429 Too Many Requests")
    assert _is_rate_limited(log_file) is True


def test_is_rate_limited_reads_tail_when_file_large(tmp_path: Path) -> None:
    log_file = tmp_path / "claude.log"
    junk = "x" * 40000
    tail = "\nrate_limit_error in tail\n"
    log_file.write_text(junk + tail)
    assert _is_rate_limited(log_file) is True


def test_is_rate_limited_clean_file_returns_false(tmp_path: Path) -> None:
    log_file = tmp_path / "claude.log"
    log_file.write_text("Everything went fine. No errors.\n")
    assert _is_rate_limited(log_file) is False


# ---------------------------------------------------------------------------
# Tests: execute_claude — non-zero exit reads log files for diagnostics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_claude_failed_exit_reads_log_files(tmp_path: Path) -> None:
    """On non-zero exit without result event, reads stderr/debug log for diagnostics."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test agent.")

    mock_proc = _make_proc(returncode=1)

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    read_text_calls: list[str] = []
    original_read_text = Path.read_text

    def patched_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        read_text_calls.append(str(self))
        if "stderr" in str(self) or "stage" in str(self):
            return "Some stderr content from the process"
        return original_read_text(self, *args, **kwargs)

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"), \
         patch.object(Path, "read_text", patched_read_text):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        with pytest.raises(AgentExecutionError):
            await execute_claude(
                prompt_file=prompt_file, context={},
                work_dir=str(tmp_path), task_id="t1", stage_num=1,
            )

    assert len(read_text_calls) > 0


# ---------------------------------------------------------------------------
# Tests: _tail_file — on_live_output exception is suppressed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_file_on_live_output_exception_suppressed(tmp_path: Path) -> None:
    """Exceptions from on_live_output callback are silently caught."""
    stdout_file = tmp_path / "stdout.ndjson"
    _write_ndjson_file(stdout_file, {"type": "result", "result": "ok"})

    async def bad_callback(line: str) -> None:
        raise RuntimeError("callback failed")

    proc = _make_proc(returncode=0)
    lines, _, result_seen = await _tail_file(
        stdout_file, proc,
        on_live_output=bad_callback,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert result_seen is True
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# Tests: _tail_file — empty file with exited process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_file_empty_file_exited_process(tmp_path: Path) -> None:
    """Returns empty lines when file is empty and process already exited."""
    stdout_file = tmp_path / "stdout.ndjson"
    stdout_file.write_text("")

    proc = _make_proc(returncode=0)
    lines, _, result_seen = await _tail_file(
        stdout_file, proc,
        timeout_seconds=5.0, task_id="t1", stage_num=0,
    )
    assert lines == []
    assert result_seen is False


# ---------------------------------------------------------------------------
# Tests: conditions.py — uncovered branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_conditions_simple_raises_exception_returns_none() -> None:
    """When simple condition raises, it returns None and the condition is skipped."""
    conditions = [
        {"simple": "@@@invalid@@@", "yes": "next-stage"},
    ]
    result = await evaluate_conditions(
        conditions,
        stage_outputs={},
        current_output={},
        repeat_counts={},
    )
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_condition_has_no_simple_or_ai() -> None:
    """A condition dict with neither 'simple' nor 'ai' returns None."""
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
    from aquarco_supervisor.pipeline.conditions import _tokenize

    with pytest.raises(ValueError, match="Unexpected character"):
        _tokenize("status == @bad")


def test_tokenize_expression_with_trailing_whitespace() -> None:
    from aquarco_supervisor.pipeline.conditions import _tokenize

    tokens = _tokenize("42   ")
    assert len(tokens) == 1
    assert tokens[0].value == "42"


def test_tokenize_expression_with_only_whitespace_returns_empty() -> None:
    from aquarco_supervisor.pipeline.conditions import _tokenize

    tokens = _tokenize("   ")
    assert tokens == []


def test_evaluate_simple_expression_whitespace_only_returns_true() -> None:
    result = evaluate_simple_expression("   ", {})
    assert result is True


def test_parser_consume_raises_on_type_mismatch() -> None:
    from aquarco_supervisor.pipeline.conditions import _Parser, _tokenize

    tokens = _tokenize("42")
    parser = _Parser(tokens, {})
    with pytest.raises(ValueError, match="Expected IDENT, got NUMBER"):
        parser.consume("IDENT")


def test_parser_parse_raises_on_unexpected_trailing_token() -> None:
    from aquarco_supervisor.pipeline.conditions import _Parser, _tokenize

    tokens = _tokenize("1 2")
    parser = _Parser(tokens, {})
    with pytest.raises(ValueError, match="Unexpected token"):
        parser.parse()


def test_parser_primary_raises_on_empty_token_list() -> None:
    from aquarco_supervisor.pipeline.conditions import _Parser

    parser = _Parser([], {})
    with pytest.raises(ValueError, match="Unexpected end of expression"):
        parser.primary()


def test_is_truthy_dict_and_list() -> None:
    assert _is_truthy({"key": "val"}) is True
    assert _is_truthy({}) is False
    assert _is_truthy([1, 2]) is True
    assert _is_truthy([]) is False


def test_is_truthy_other_truthy_object() -> None:
    class _CustomObj:
        def __bool__(self) -> bool:
            return True

    class _FalsyObj:
        def __bool__(self) -> bool:
            return False

    assert _is_truthy(_CustomObj()) is True
    assert _is_truthy(_FalsyObj()) is False


def test_to_number_returns_none_for_non_numeric_types() -> None:
    assert _to_number(None) is None
    assert _to_number([1, 2, 3]) is None
    assert _to_number({"key": "val"}) is None


def test_compare_string_ge_le() -> None:
    from aquarco_supervisor.pipeline.conditions import _compare

    assert _compare("b", "GE", "a") is True
    assert _compare("a", "GE", "b") is False
    assert _compare("a", "LE", "b") is True
    assert _compare("b", "LE", "a") is False


# ---------------------------------------------------------------------------
# Tests: evaluate_ai_condition — delegates to execute_claude
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_ai_condition_passes_extra_env() -> None:
    from aquarco_supervisor.cli.claude import ClaudeOutput
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    mock_output = ClaudeOutput(
        structured={"answer": True, "message": "env was set", "_cost_usd": 0.02},
        raw="",
    )

    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        AsyncMock(return_value=mock_output),
    ) as mock_exec:
        result = await evaluate_ai_condition(
            "Is extra env set?",
            {"context": "data"},
            task_id="t1",
            stage_num=0,
            extra_env={"MY_VAR": "my_value"},
        )

    assert result["answer"] is True
    assert result["_cost_usd"] == 0.02
    call_kwargs = mock_exec.call_args[1]
    assert call_kwargs["extra_env"] == {"MY_VAR": "my_value"}


@pytest.mark.asyncio
async def test_evaluate_ai_condition_passes_max_turns_and_timeout() -> None:
    from aquarco_supervisor.cli.claude import ClaudeOutput
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    mock_output = ClaudeOutput(
        structured={"answer": False, "message": "nope"},
        raw="",
    )

    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        AsyncMock(return_value=mock_output),
    ) as mock_exec:
        result = await evaluate_ai_condition(
            "test condition",
            {},
            task_id="t1",
            stage_num=0,
            max_turns=3,
            timeout_seconds=300,
        )

    assert result["answer"] is False
    call_kwargs = mock_exec.call_args[1]
    assert call_kwargs["max_turns"] == 3
    assert call_kwargs["timeout_seconds"] == 300


@pytest.mark.asyncio
async def test_evaluate_ai_condition_uses_prompt_file() -> None:
    """When prompt_file is given and exists, it is passed to execute_claude."""
    import tempfile
    from aquarco_supervisor.cli.claude import ClaudeOutput
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    mock_output = ClaudeOutput(
        structured={"answer": True, "message": "ok"},
        raw="",
    )

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("Custom prompt")
        prompt_path = Path(f.name)

    try:
        with patch(
            "aquarco_supervisor.pipeline.conditions.execute_claude",
            AsyncMock(return_value=mock_output),
        ) as mock_exec:
            result = await evaluate_ai_condition(
                "test",
                {},
                task_id="t1",
                stage_num=0,
                prompt_file=prompt_path,
            )

        assert result["answer"] is True
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["prompt_file"] == prompt_path
    finally:
        prompt_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_evaluate_ai_condition_fallback_prompt_cleaned_up() -> None:
    """When no prompt_file, a temp file is created and cleaned up."""
    from aquarco_supervisor.cli.claude import ClaudeOutput
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    captured_prompt_file: list[Path] = []
    mock_output = ClaudeOutput(
        structured={"answer": True, "message": "ok"},
        raw="",
    )

    async def capturing_execute(*args: Any, **kwargs: Any) -> ClaudeOutput:
        captured_prompt_file.append(kwargs["prompt_file"])
        return mock_output

    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        side_effect=capturing_execute,
    ):
        await evaluate_ai_condition(
            "test",
            {},
            task_id="t1",
            stage_num=0,
        )

    # Temp file should have been cleaned up
    assert len(captured_prompt_file) == 1
    assert not captured_prompt_file[0].exists()


@pytest.mark.asyncio
async def test_evaluate_ai_condition_includes_raw_output() -> None:
    from aquarco_supervisor.cli.claude import ClaudeOutput
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    raw = '{"type":"result","structured_output":{"answer":true}}'
    mock_output = ClaudeOutput(
        structured={"answer": True, "message": ""},
        raw=raw,
    )

    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        AsyncMock(return_value=mock_output),
    ):
        result = await evaluate_ai_condition(
            "test",
            {},
            task_id="t1",
            stage_num=0,
        )

    assert result["_raw_output"] == raw

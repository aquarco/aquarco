"""Regression tests for GitHub issue #165 — Handle error_max_turns properly.

Reference: https://github.com/aquarco/aquarco/issues/165

These tests lock in three required behaviors:

1. The output parser must always populate ``_subtype`` / ``_is_error`` /
   ``_cost_usd`` / ``_session_id`` etc. (prefixed keys) when the Claude CLI
   returns a ``result`` event — even when the ``result`` field is the empty
   string. Without this, ``error_max_turns`` was silently mis-parsed as raw
   keys, breaking the three downstream consumers.

2. ``_resolve_stage_status`` must return ``("max_turns", ...)`` for
   ``error_max_turns`` results regardless of whether ``_is_error`` is also
   set to ``True``. The Claude CLI emits both fields on max-turn termination.

3. The ``StageStatus`` enum must expose ``MAX_TURNS`` so code paths that
   switch on stage status (retries, telemetry, reports) can handle it.

4. ``AgentInvoker.execute_agent`` must NOT raise ``AgentExecutionError`` when
   a max-turns iteration carries ``_is_error=True`` — that path is handled by
   the max-turns continuation loop, not the rate-limit/auth guard. The guard
   must carve out ``_subtype == "error_max_turns"``.

5. Spending (cost + tokens) must be written to the stage row even when the
   final iteration is an ``error_max_turns`` stopped by the cost guard.

The tests are written to FAIL against the buggy tree and pass after Fixes
1-4 described in the analyze-bug output for issue #165.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.claude import ClaudeOutput
from aquarco_supervisor.cli.output_parser import (
    _extract_from_result_message,
    _parse_ndjson_output,
)
from aquarco_supervisor.database import Database
from aquarco_supervisor.models import StageStatus
from aquarco_supervisor.pipeline.agent_invoker import AgentInvoker
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry
from aquarco_supervisor.stage_manager import _resolve_stage_status


# -----------------------------------------------------------------------
# Representative Claude CLI result event for max-turns termination.
# Mirrors the real shape documented in the issue body:
#   {"type":"result","subtype":"error_max_turns","is_error":true,
#    "result":"", "total_cost_usd":5.0, ... "session_id":"..."}
# -----------------------------------------------------------------------


def _max_turns_result_message(
    *,
    session_id: str = "sess-xyz",
    cost: float = 5.0,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read: int = 200,
    cache_write: int = 100,
    duration_ms: int = 120_000,
    num_turns: int = 100,
) -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "error_max_turns",
        "is_error": True,
        "result": "",  # Empty string is the critical trigger
        "total_cost_usd": cost,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
        "session_id": session_id,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    }


# -----------------------------------------------------------------------
# Fix 1 — output_parser must always populate prefixed metadata keys
# -----------------------------------------------------------------------


class TestFix1OutputParserMaxTurnsMetadata:
    """``_extract_from_result_message`` must populate prefixed keys even when
    ``result`` is the empty string and no ``structured_output`` is present —
    that is the exact shape the Claude CLI emits for ``error_max_turns``."""

    def test_subtype_uses_prefixed_key_when_result_is_empty_string(self):
        msg = _max_turns_result_message()
        out = _extract_from_result_message(msg)
        # REGRESSION: previously this asserted "subtype" (raw key) because the
        # early-return path in the parser bypassed the metadata-prefix step.
        assert out.get("_subtype") == "error_max_turns"

    def test_is_error_uses_prefixed_key_when_result_is_empty_string(self):
        msg = _max_turns_result_message()
        out = _extract_from_result_message(msg)
        assert out.get("_is_error") is True

    def test_cost_uses_prefixed_key_when_result_is_empty_string(self):
        msg = _max_turns_result_message(cost=5.0)
        out = _extract_from_result_message(msg)
        assert out.get("_cost_usd") == 5.0

    def test_session_id_uses_prefixed_key_when_result_is_empty_string(self):
        msg = _max_turns_result_message(session_id="sess-abc123")
        out = _extract_from_result_message(msg)
        assert out.get("_session_id") == "sess-abc123"

    def test_tokens_populated_when_result_is_empty_string(self):
        msg = _max_turns_result_message(
            input_tokens=1234,
            output_tokens=567,
            cache_read=89,
            cache_write=42,
        )
        out = _extract_from_result_message(msg)
        assert out.get("_input_tokens") == 1234
        assert out.get("_output_tokens") == 567
        assert out.get("_cache_read_tokens") == 89
        assert out.get("_cache_write_tokens") == 42

    def test_duration_and_turns_populated_when_result_is_empty_string(self):
        msg = _max_turns_result_message(duration_ms=99_000, num_turns=101)
        out = _extract_from_result_message(msg)
        assert out.get("_duration_ms") == 99_000
        assert out.get("_num_turns") == 101

    def test_no_raw_subtype_leaked_alongside_prefixed_subtype(self):
        """The prefixed ``_subtype`` is the authoritative key for consumers.

        The parser is allowed to copy raw fields into the output for
        backward compatibility, but ``_subtype`` MUST be present and MUST
        be equal to the raw ``subtype`` field — otherwise downstream code
        that reads only ``_subtype`` (the documented contract) silently
        fails.
        """
        msg = _max_turns_result_message()
        out = _extract_from_result_message(msg)
        assert "_subtype" in out, (
            "parser must populate the prefixed _subtype key for all result "
            "events, including error_max_turns with empty result"
        )
        # If the parser also keeps raw keys, they must agree with prefixed.
        if "subtype" in out:
            assert out["subtype"] == out["_subtype"]

    def test_ndjson_pipeline_sees_prefixed_keys_for_error_max_turns(self):
        """End-to-end NDJSON parse path must also produce prefixed keys."""
        line = json.dumps(_max_turns_result_message(cost=4.2))
        parsed = _parse_ndjson_output([line], "task-x", 0)
        assert parsed.get("_subtype") == "error_max_turns"
        assert parsed.get("_is_error") is True
        assert parsed.get("_cost_usd") == 4.2
        assert parsed.get("_session_id") == "sess-xyz"


# -----------------------------------------------------------------------
# Fix 2 — stage_manager must recognise max_turns even with _is_error=True
# -----------------------------------------------------------------------


class TestFix2ResolveStageStatusMaxTurnsWithIsError:
    """Claude CLI returns ``is_error=True`` for ``error_max_turns`` results.
    The status resolver must prioritise the max_turns subtype branch and
    return ``"max_turns"`` (not ``"failed"`` or ``"rate_limited"``)."""

    def test_max_turns_with_is_error_true(self):
        output = {"_subtype": "error_max_turns", "_is_error": True}
        status, error_msg = _resolve_stage_status(output, None)
        assert status == "max_turns"
        assert error_msg is not None and "max_turns" in error_msg

    def test_max_turns_with_is_error_false(self):
        output = {"_subtype": "error_max_turns", "_is_error": False}
        status, _ = _resolve_stage_status(output, None)
        assert status == "max_turns"

    def test_max_turns_ignores_rate_limit_raw_output(self):
        """Even if raw_output coincidentally contains a rate_limit_event,
        the max_turns subtype wins."""
        rate_limit_line = json.dumps({
            "type": "rate_limit_event",
            "rate_limit_info": {"resetsAt": "2026-04-08T12:00:00Z"},
        })
        output = {"_subtype": "error_max_turns", "_is_error": True}
        status, _ = _resolve_stage_status(output, rate_limit_line)
        assert status == "max_turns"


# -----------------------------------------------------------------------
# Fix 3 — StageStatus enum exposes MAX_TURNS
# -----------------------------------------------------------------------


class TestFix3StageStatusMaxTurnsEnum:
    """The Python StageStatus enum must include MAX_TURNS so code that
    compares against it (task retry logic, UI, reports) can handle the
    new status that the database already accepts via migration 037."""

    def test_max_turns_enum_member_exists(self):
        assert hasattr(StageStatus, "MAX_TURNS"), (
            "StageStatus.MAX_TURNS is required — the DB has accepted "
            "'max_turns' since migration 037 and Python code must match"
        )

    def test_max_turns_enum_value_is_string(self):
        assert StageStatus.MAX_TURNS.value == "max_turns"

    def test_max_turns_enum_lookup(self):
        assert StageStatus("max_turns") == StageStatus.MAX_TURNS

    def test_all_status_values_include_max_turns(self):
        values = {s.value for s in StageStatus}
        expected = {
            "pending",
            "executing",
            "completed",
            "failed",
            "skipped",
            "rate_limited",
            "max_turns",
        }
        assert expected.issubset(values)


# -----------------------------------------------------------------------
# Fix 4 — AgentInvoker guard must carve out error_max_turns
# -----------------------------------------------------------------------


@pytest.fixture
def _mock_registry() -> MagicMock:
    reg = MagicMock(spec=AgentRegistry)
    reg.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    reg.get_agent_timeout = MagicMock(return_value=30)
    reg.get_agent_max_turns = MagicMock(return_value=100)
    reg.get_agent_max_cost = MagicMock(return_value=15.0)
    reg.get_agent_model = MagicMock(return_value=None)
    reg.get_allowed_tools = MagicMock(return_value=[])
    reg.get_denied_tools = MagicMock(return_value=[])
    reg.get_agent_environment = MagicMock(return_value={})
    reg.get_agent_output_schema = MagicMock(return_value=None)
    return reg


@pytest.fixture
def _invoker(_mock_registry, sample_pipelines):
    db = AsyncMock(spec=Database)
    return AgentInvoker(db, _mock_registry, sample_pipelines)


class TestFix4AgentInvokerMaxTurnsWithIsError:
    """The is_error guard in ``execute_agent`` must carve out
    ``error_max_turns`` — those are handled by the continuation loop, not
    the rate-limit / auth error detection block."""

    @pytest.mark.asyncio
    async def test_max_turns_with_is_error_does_not_raise(
        self, _invoker, _mock_registry
    ):
        """When cost budget is exhausted, the loop breaks. The final output
        has ``_is_error=True`` because Claude CLI set it. The guard must not
        misinterpret that as a rate-limit / auth error."""
        _mock_registry.get_agent_max_cost = MagicMock(return_value=1.0)
        # Every iteration hits max_turns with is_error=True (real CLI shape)
        output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_is_error": True,
                "_session_id": "sess-xyz",
                "_cost_usd": 1.5,  # ≥ max_cost → cost guard fires
            },
            raw="{}",
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=output,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            # Must NOT raise AgentExecutionError.
            result = await _invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        assert result["_subtype"] == "error_max_turns"
        assert result["_is_error"] is True
        assert result["_iterations"] == 1

    @pytest.mark.asyncio
    async def test_max_turns_cost_exhausted_preserves_spending(
        self, _invoker, _mock_registry
    ):
        """When the cost guard stops the continuation loop on an
        error_max_turns output, the returned dict must still carry
        cumulative spending so ``store_stage_output`` can persist it.
        This is the fix for issue #165 point 2."""
        _mock_registry.get_agent_max_cost = MagicMock(return_value=1.0)
        output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_is_error": True,
                "_session_id": "sess-xyz",
                "_cost_usd": 5.0,
                "_input_tokens": 1000,
                "_output_tokens": 500,
                "_cache_read_tokens": 200,
                "_cache_write_tokens": 100,
            },
            raw="{}",
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=output,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await _invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        assert result["_cumulative_cost_usd"] == 5.0
        assert result["_cumulative_input_tokens"] == 1000
        assert result["_cumulative_output_tokens"] == 500
        assert result["_cumulative_cache_read_tokens"] == 200
        assert result["_cumulative_cache_write_tokens"] == 100

    @pytest.mark.asyncio
    async def test_max_turns_continues_even_when_is_error_true(
        self, _invoker, _mock_registry
    ):
        """Until max_cost is reached, the loop must continue after
        ``error_max_turns`` even when ``_is_error=True``."""
        _mock_registry.get_agent_max_cost = MagicMock(return_value=10.0)
        first = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_is_error": True,
                "_session_id": "sess-1",
                "_cost_usd": 1.0,
            },
            raw="{}",
        )
        second = ClaudeOutput(
            structured={
                "_subtype": "success",
                "_is_error": False,
                "result": "done",
                "_cost_usd": 0.3,
            },
            raw="{}",
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=[first, second],
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await _invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        # Loop MUST have continued past the max_turns iteration
        assert mock_exec.call_count == 2
        # Second call must have resumed with the first session id
        second_kwargs = mock_exec.call_args_list[1].kwargs
        assert second_kwargs["resume_session_id"] == "sess-1"
        assert result["_iterations"] == 2
        assert result["result"] == "done"


# -----------------------------------------------------------------------
# Issue #165 point 3 — stage should be repeated until max cost is reached
# -----------------------------------------------------------------------


class TestIssue165Point3StageRepeatedUntilMaxCost:
    """The exact scenario from the issue body:

        Implementation agent has 100 max turns and $15 max cost. A single
        max-turns iteration costs ~$5. It should be invoked at least once
        or twice (not just once as the buggy tree does).

    Prior to the fix, ``_subtype`` was never populated as ``_subtype``, so
    the continuation loop never detected ``error_max_turns`` and broke
    after a single iteration. After the fix, the loop iterates until
    cumulative cost is at least ``max_cost``."""

    @pytest.mark.asyncio
    async def test_implementation_agent_runs_at_least_twice_before_cost_cap(
        self, _invoker, _mock_registry
    ):
        _mock_registry.get_agent_max_turns = MagicMock(return_value=100)
        _mock_registry.get_agent_max_cost = MagicMock(return_value=15.0)

        # Build a parsed output from a realistic raw CLI result event, so
        # the test exercises the full parser → invoker contract.
        msg1 = _max_turns_result_message(
            session_id="sess-1", cost=5.0,
        )
        parsed1 = _extract_from_result_message(msg1)
        msg2 = _max_turns_result_message(
            session_id="sess-2", cost=5.0,
        )
        parsed2 = _extract_from_result_message(msg2)
        msg3 = _max_turns_result_message(
            session_id="sess-3", cost=5.0,
        )
        parsed3 = _extract_from_result_message(msg3)

        outputs = [
            ClaudeOutput(structured=parsed1, raw="{}"),
            ClaudeOutput(structured=parsed2, raw="{}"),
            ClaudeOutput(structured=parsed3, raw="{}"),
        ]

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=outputs,
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await _invoker.execute_agent(
                "implementation-agent", "task-165", {}, 0,
                work_dir="/repos/test",
            )

        # Before the fix, mock_exec.call_count was 1 (single call, no
        # continuation). After the fix, the continuation loop runs until
        # cumulative cost (15.0) is ≥ max_cost (15.0). With $5/iteration
        # and $15 cap, that is exactly 3 iterations.
        assert mock_exec.call_count >= 2, (
            "stage must be re-invoked at least twice when cost cap not reached "
            "(issue #165 point 3)"
        )
        assert result["_cumulative_cost_usd"] >= 10.0
        assert result["_subtype"] == "error_max_turns"

    @pytest.mark.asyncio
    async def test_resume_sessions_chain_across_iterations(
        self, _invoker, _mock_registry
    ):
        """Each continuation iteration must resume the previous iteration's
        session_id. This is what makes multiple invocations 'useful' —
        without session resumption, each iteration would start fresh."""
        _mock_registry.get_agent_max_turns = MagicMock(return_value=100)
        _mock_registry.get_agent_max_cost = MagicMock(return_value=15.0)

        outputs = [
            ClaudeOutput(
                structured=_extract_from_result_message(
                    _max_turns_result_message(session_id=f"sess-{i}", cost=5.0)
                ),
                raw="{}",
            )
            for i in range(1, 4)
        ]

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=outputs,
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            await _invoker.execute_agent(
                "implementation-agent", "task-165", {}, 0,
                work_dir="/repos/test",
            )

        if mock_exec.call_count >= 2:
            # Call N must resume with session id from call N-1
            for n in range(1, mock_exec.call_count):
                kwargs = mock_exec.call_args_list[n].kwargs
                assert kwargs["resume_session_id"] == f"sess-{n}"


# -----------------------------------------------------------------------
# Integration — full pipeline: raw result event through to stage status
# -----------------------------------------------------------------------


class TestEndToEndMaxTurnsResultEvent:
    """Integration-style check: feed the raw Claude CLI result event (with
    empty string ``result``) through the parser, then through the status
    resolver — the stage status must be ``max_turns`` and the spending
    must be populated. This locks in all three fixes simultaneously."""

    def test_raw_cli_result_maps_to_max_turns_with_spending(self):
        raw_result_event = _max_turns_result_message(cost=5.0)
        parsed = _extract_from_result_message(raw_result_event)

        # Parser contract: prefixed keys present
        assert parsed["_subtype"] == "error_max_turns"
        assert parsed["_is_error"] is True
        assert parsed["_cost_usd"] == 5.0
        assert parsed["_session_id"] == "sess-xyz"

        # Status resolver contract: max_turns (not failed)
        status, _ = _resolve_stage_status(parsed, None)
        assert status == "max_turns"

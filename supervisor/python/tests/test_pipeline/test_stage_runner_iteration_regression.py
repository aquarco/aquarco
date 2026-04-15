"""Regression test: stage reached via linear fall-through must get a fresh iteration.

Bug: when fix-review-findings is revisited via current_idx += 1 (not via a named
jump), stage_iterations["fix-review-findings"] was never incremented.  The "on
revisit" block therefore called create_iteration_stage(..., iteration=1) again,
hit ON CONFLICT DO NOTHING, got new_id=None, and left stage_ids pointing at the
already-completed row.  execute_planned_stage then found that row via
get_latest_stage_run and triggered the completed_stage_guard — returning the old
output silently instead of running the agent.  The conditions still evaluated on
the stale output and jumped back to review, producing consecutive REVIEW stages
with no IMPLEMENT in between.

Fix (stage_runner.py "on revisit" block): always increment stage_iterations at
execution time, regardless of whether the stage was reached via a jump or via
linear fall-through.

Pipeline modelled here (pr-review-pipeline shape):

    review (idx 0)
        condition: severity == major_issues  -> no jump (falls through to idx 1)
                   severity != major_issues  -> "no": test (idx 2)
    fix-review-findings (idx 1)
        condition: "true"  -> "yes": review (idx 0)
    test (idx 2)
        no conditions

Expected execution with severity cycling major / major / minor:

    review (iter 1, severity=major)   -> falls through
    fix-review-findings (iter 1)      -> jumps to review
    review (iter 2, severity=major)   -> falls through   <- 2nd time via fallthrough
    fix-review-findings (iter 2)      -> jumps to review  <- MUST execute, not be guarded
    review (iter 3, severity=minor)   -> jumps to test
    test (iter 1)                     -> done
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.agent_invoker import AgentInvoker
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry
from aquarco_supervisor.pipeline.stage_runner import StageRunner
from aquarco_supervisor.stage_manager import StageManager
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PLANNED_STAGES = [
    {"category": "review",    "agents": ["review-agent"], "parallel": False},
    {"category": "implement", "agents": ["impl-agent"],   "parallel": False},
    {"category": "test",      "agents": ["test-agent"],   "parallel": False},
]

_STAGE_DEFS = [
    {
        "name": "review",
        "category": "review",
        "required": True,
        "conditions": [
            # TRUE (severity IS major_issues) -> no "yes" target -> fall through to idx 1
            # FALSE (severity is NOT major_issues) -> "no": test -> jump to idx 2
            {"simple": "severity == major_issues", "no": "test", "maxRepeats": 3},
        ],
    },
    {
        "name": "fix-review-findings",
        "category": "implement",
        "required": False,
        "conditions": [
            {"simple": "true", "yes": "review", "maxRepeats": 5},
        ],
    },
    {
        "name": "test",
        "category": "test",
        "required": False,
        "conditions": [],
    },
]


def _make_runner() -> tuple[StageRunner, AsyncMock, AsyncMock, list[tuple]]:
    """Build a StageRunner with instrumented mocks.

    Returns (runner, mock_sm, mock_invoker, create_iter_calls).
    create_iter_calls is populated by side effects on mock_sm.create_iteration_stage.
    """
    mock_db = AsyncMock(spec=Database)
    mock_db.execute = AsyncMock()

    mock_tq = AsyncMock(spec=TaskQueue)

    create_iter_calls: list[tuple] = []
    _id_counter = [100]

    async def _create_iteration_stage(task_id, stage_num, category, agent, iteration):
        stage_key = f"{stage_num}:{category}:{agent}"
        create_iter_calls.append((stage_num, category, agent, iteration))
        _id_counter[0] += 1
        return stage_key, _id_counter[0]

    mock_sm = AsyncMock(spec=StageManager)
    mock_sm.get_task_context = AsyncMock(return_value={})
    mock_sm.get_latest_stage_run = AsyncMock(return_value=None)
    mock_sm.create_iteration_stage = AsyncMock(side_effect=_create_iteration_stage)
    mock_sm.record_stage_executing = AsyncMock()
    mock_sm.store_stage_output = AsyncMock()
    mock_sm.update_checkpoint = AsyncMock()
    mock_sm.record_stage_skipped = AsyncMock()

    # Track execute_agent calls by agent name
    review_calls = [0]
    agent_call_log: list[str] = []

    async def _execute_agent(agent_name, task_id, context, stage_num, **kwargs):
        agent_call_log.append(agent_name)
        if agent_name == "review-agent":
            review_calls[0] += 1
            severity = "major_issues" if review_calls[0] < 3 else "minor_issues"
            return {
                "severity": severity,
                "recommendation": "request_changes",
                "findings": [],
                "summary": f"review call {review_calls[0]}",
            }
        if agent_name == "impl-agent":
            return {"summary": "fixed", "files_changed": [], "test_status": "passed"}
        if agent_name == "test-agent":
            return {
                "tests_added": 1, "tests_passed": 1, "tests_failed": 0,
                "coverage_percent": 90.0, "test_files": [],
            }
        return {}

    mock_invoker = AsyncMock(spec=AgentInvoker)
    mock_invoker.execute_agent = AsyncMock(side_effect=_execute_agent)

    mock_registry = MagicMock(spec=AgentRegistry)
    mock_registry.increment_agent_instances = AsyncMock()
    mock_registry.decrement_agent_instances = AsyncMock()
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_agent_max_turns = MagicMock(return_value=1)
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_prompt_file = MagicMock(return_value=None)
    mock_registry.get_agent_model = MagicMock(return_value=None)

    counter = {"val": 0}

    def _next_eo(tid):
        counter["val"] += 1
        return counter["val"]

    runner = StageRunner(
        mock_db, mock_tq, mock_sm, mock_registry, mock_invoker,
        MagicMock(side_effect=_next_eo),
    )
    # Expose for assertions
    runner._agent_call_log = agent_call_log  # type: ignore[attr-defined]

    return runner, mock_sm, mock_invoker, create_iter_calls


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------


class TestIterationFallthroughRegression:
    """fix-review-findings reached via linear fall-through must get a new iteration."""

    @pytest.mark.asyncio
    async def test_implement_executes_on_every_revisit(self):
        """execute_agent must be called for impl-agent on each visit to fix-review-findings."""
        runner, mock_sm, mock_invoker, _ = _make_runner()

        with (
            patch(
                "aquarco_supervisor.pipeline.executor._git_checkout",
                new_callable=AsyncMock,
            ),
            patch(
                "aquarco_supervisor.pipeline.executor._auto_commit",
                new_callable=AsyncMock,
            ),
        ):
            failed = await runner.execute_running_phase(
                "task-reg-1", _PLANNED_STAGES, _STAGE_DEFS,
                "/repos/test", "feature-branch",
            )

        assert not failed

        impl_calls = [
            c for c in runner._agent_call_log  # type: ignore[attr-defined]
            if c == "impl-agent"
        ]
        assert len(impl_calls) == 2, (
            f"Expected impl-agent to be called twice (once per visit to "
            f"fix-review-findings), got {len(impl_calls)}.  "
            f"Full call log: {runner._agent_call_log}"  # type: ignore[attr-defined]
        )

    @pytest.mark.asyncio
    async def test_create_iteration_stage_called_with_iteration_2_for_implement(self):
        """On the second visit to fix-review-findings, create_iteration_stage must
        use iteration=2, not iteration=1 (which would collide with the first visit)."""
        runner, mock_sm, mock_invoker, create_iter_calls = _make_runner()

        with (
            patch(
                "aquarco_supervisor.pipeline.executor._git_checkout",
                new_callable=AsyncMock,
            ),
            patch(
                "aquarco_supervisor.pipeline.executor._auto_commit",
                new_callable=AsyncMock,
            ),
        ):
            await runner.execute_running_phase(
                "task-reg-2", _PLANNED_STAGES, _STAGE_DEFS,
                "/repos/test", "feature-branch",
            )

        # Each revisited stage should have been created with a strictly increasing
        # iteration number — never with iteration=1 twice for the same stage.
        implement_iters = [
            iteration
            for (stage_num, category, agent, iteration) in create_iter_calls
            if category == "implement"
        ]
        assert implement_iters == [2], (
            f"Expected create_iteration_stage to be called once for implement "
            f"with iteration=2; got iterations={implement_iters}.  "
            f"All calls: {create_iter_calls}"
        )

    @pytest.mark.asyncio
    async def test_execution_order_is_review_impl_review_impl_review_test(self):
        """Full execution order matches the expected REVIEW/IMPLEMENT alternation."""
        runner, _, _, _ = _make_runner()

        with (
            patch(
                "aquarco_supervisor.pipeline.executor._git_checkout",
                new_callable=AsyncMock,
            ),
            patch(
                "aquarco_supervisor.pipeline.executor._auto_commit",
                new_callable=AsyncMock,
            ),
        ):
            await runner.execute_running_phase(
                "task-reg-3", _PLANNED_STAGES, _STAGE_DEFS,
                "/repos/test", "feature-branch",
            )

        expected = [
            "review-agent",   # review 1st — severity=major, falls through
            "impl-agent",     # fix-review-findings 1st — jumps to review
            "review-agent",   # review 2nd — severity=major, falls through
            "impl-agent",     # fix-review-findings 2nd — jumps to review  (BUG: was skipped)
            "review-agent",   # review 3rd — severity=minor, jumps to test
            "test-agent",     # test
        ]
        assert runner._agent_call_log == expected, (  # type: ignore[attr-defined]
            f"Unexpected execution order.\n"
            f"Expected: {expected}\n"
            f"Got:      {runner._agent_call_log}"  # type: ignore[attr-defined]
        )

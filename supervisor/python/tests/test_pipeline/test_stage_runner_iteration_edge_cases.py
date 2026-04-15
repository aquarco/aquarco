"""Edge-case tests for stage_iterations increment logic in StageRunner.

These tests complement test_stage_runner_iteration_regression.py by covering
additional scenarios around the fix that moved the iteration counter increment
from the jump site to the execution-time "on revisit" block.

Scenarios covered:
  1. Jump-targeted revisits still produce correct iteration numbers
  2. Three consecutive revisits to the same stage increment correctly (iter 2, 3, 4)
  3. Two independent stages both revisited get independent iteration counters
  4. First visit to a stage never calls create_iteration_stage (repeat_counts == 1)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_runner(
    execute_agent_side_effect,
) -> tuple[StageRunner, list[tuple], list[str]]:
    """Build a StageRunner with instrumented mocks.

    Returns (runner, create_iter_calls, agent_call_log).
    """
    mock_db = AsyncMock(spec=Database)
    mock_db.execute = AsyncMock()
    mock_tq = AsyncMock(spec=TaskQueue)

    create_iter_calls: list[tuple] = []
    _id_counter = [100]

    async def _create_iteration_stage(task_id, stage_num, category, agent, iteration):
        create_iter_calls.append((stage_num, category, agent, iteration))
        _id_counter[0] += 1
        return f"{stage_num}:{category}:{agent}", _id_counter[0]

    mock_sm = AsyncMock(spec=StageManager)
    mock_sm.get_task_context = AsyncMock(return_value={})
    mock_sm.get_latest_stage_run = AsyncMock(return_value=None)
    mock_sm.create_iteration_stage = AsyncMock(side_effect=_create_iteration_stage)
    mock_sm.record_stage_executing = AsyncMock()
    mock_sm.store_stage_output = AsyncMock()
    mock_sm.update_checkpoint = AsyncMock()
    mock_sm.record_stage_skipped = AsyncMock()

    agent_call_log: list[str] = []

    async def _wrapped_execute(agent_name, task_id, context, stage_num, **kwargs):
        agent_call_log.append(agent_name)
        return await execute_agent_side_effect(agent_name, task_id, context, stage_num, **kwargs)

    mock_invoker = AsyncMock(spec=AgentInvoker)
    mock_invoker.execute_agent = AsyncMock(side_effect=_wrapped_execute)

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
    return runner, create_iter_calls, agent_call_log


def _patch_git():
    """Context manager to patch git operations."""
    return (
        patch("aquarco_supervisor.pipeline.executor._git_checkout", new_callable=AsyncMock),
        patch("aquarco_supervisor.pipeline.executor._auto_commit", new_callable=AsyncMock),
    )


# ---------------------------------------------------------------------------
# Test 1: Jump-targeted revisits still get correct iteration numbers
# ---------------------------------------------------------------------------


class TestJumpTargetedRevisits:
    """When a stage is revisited via an explicit jump (not fall-through),
    the iteration counter must still increment correctly."""

    @pytest.mark.asyncio
    async def test_jump_revisit_increments_iteration(self):
        """Pipeline: A -> B, B condition jumps to A.
        A is visited twice: first time iter=1, second time iter=2.
        """
        call_count = {"a": 0}

        async def _execute(agent_name, task_id, context, stage_num, **kw):
            if agent_name == "agent-a":
                call_count["a"] += 1
                return {"status": "done", "round": call_count["a"]}
            # agent-b always returns something
            return {"status": "fixed"}

        planned = [
            {"category": "analyze", "agents": ["agent-a"], "parallel": False},
            {"category": "fix", "agents": ["agent-b"], "parallel": False},
        ]
        stage_defs = [
            {
                "name": "analyze",
                "category": "analyze",
                "required": True,
                "conditions": [],  # no conditions, falls through
            },
            {
                "name": "fix",
                "category": "fix",
                "required": False,
                "conditions": [
                    # Jump back to analyze on first visit, then stop
                    {"simple": "round == 1", "yes": "analyze", "maxRepeats": 3},
                ],
            },
        ]

        # agent-b's condition references "round" from stage_output, but
        # evaluate_conditions checks current_output. Since agent-b returns
        # {"status": "fixed"} which has no "round" key, the condition
        # "round == 1" will check agent-b output. We need to make agent-b
        # return round info. Let's fix:
        visit_b = {"count": 0}

        async def _execute_v2(agent_name, task_id, context, stage_num, **kw):
            if agent_name == "agent-a":
                call_count["a"] += 1
                return {"status": "done"}
            # agent-b: first visit triggers jump, second doesn't
            visit_b["count"] += 1
            if visit_b["count"] == 1:
                return {"round": 1}
            return {"round": 2}

        runner, create_iter_calls, agent_log = _make_runner(_execute_v2)

        p1, p2 = _patch_git()
        with p1, p2:
            failed = await runner.execute_running_phase(
                "task-jump-1", planned, stage_defs, "/tmp/repo", "main",
            )

        assert not failed
        # Expected: agent-a, agent-b (round=1, jumps to analyze), agent-a, agent-b (round=2, no jump)
        assert agent_log == ["agent-a", "agent-b", "agent-a", "agent-b"]

        # Check iteration numbers for analyze stage revisit
        analyze_iters = [
            iteration for (sn, cat, agent, iteration) in create_iter_calls
            if cat == "analyze"
        ]
        assert analyze_iters == [2], (
            f"Expected analyze to get iteration=2 on revisit via jump; got {analyze_iters}"
        )


# ---------------------------------------------------------------------------
# Test 2: Three+ revisits produce strictly increasing iterations
# ---------------------------------------------------------------------------


class TestMultipleRevisits:
    """A stage visited 4 times must get iterations 1, 2, 3, 4."""

    @pytest.mark.asyncio
    async def test_four_visits_produce_iterations_1_through_4(self):
        """Pipeline: loop-stage jumps back to itself 3 times, then falls through.

        loop-stage (idx 0) -> condition: count < 4 -> yes: loop-stage
        done-stage (idx 1) -> no conditions
        """
        visit_count = {"loop": 0}

        async def _execute(agent_name, task_id, context, stage_num, **kw):
            if agent_name == "loop-agent":
                visit_count["loop"] += 1
                return {"count": visit_count["loop"]}
            return {"done": True}

        planned = [
            {"category": "loop", "agents": ["loop-agent"], "parallel": False},
            {"category": "end", "agents": ["end-agent"], "parallel": False},
        ]
        stage_defs = [
            {
                "name": "loop-stage",
                "category": "loop",
                "required": True,
                "conditions": [
                    {"simple": "count < 4", "yes": "loop-stage", "maxRepeats": 10},
                ],
            },
            {
                "name": "done-stage",
                "category": "end",
                "required": False,
                "conditions": [],
            },
        ]

        runner, create_iter_calls, agent_log = _make_runner(_execute)

        p1, p2 = _patch_git()
        with p1, p2:
            failed = await runner.execute_running_phase(
                "task-multi-1", planned, stage_defs, "/tmp/repo", "main",
            )

        assert not failed
        # loop-agent called 4 times, end-agent called once
        assert agent_log.count("loop-agent") == 4
        assert agent_log.count("end-agent") == 1

        # create_iteration_stage is called for visits 2, 3, 4 (not visit 1)
        loop_iters = [
            iteration for (sn, cat, agent, iteration) in create_iter_calls
            if cat == "loop"
        ]
        assert loop_iters == [2, 3, 4], (
            f"Expected iterations [2, 3, 4] for 3 revisits; got {loop_iters}"
        )


# ---------------------------------------------------------------------------
# Test 3: Two independent stages both revisited get independent counters
# ---------------------------------------------------------------------------


class TestIndependentIterationCounters:
    """Two different stages revisited must have independent iteration counters."""

    @pytest.mark.asyncio
    async def test_independent_counters(self):
        """Pipeline: A -> B -> A (jump) -> B (fall-through).
        Both A and B get revisited once: each should have iter=2.
        """
        a_calls = {"n": 0}

        async def _execute(agent_name, task_id, context, stage_num, **kw):
            if agent_name == "agent-a":
                a_calls["n"] += 1
                return {"pass": a_calls["n"]}
            # agent-b: first time jump to A, second time fall through
            return {"result": "ok"}

        planned = [
            {"category": "step-a", "agents": ["agent-a"], "parallel": False},
            {"category": "step-b", "agents": ["agent-b"], "parallel": False},
        ]
        # B's condition: on first visit (pass==1 in stage_outputs from A),
        # jump back to A; on second, fall through.
        # We use a trick: agent-b output has no "pass" field so we rely
        # on the overall_output check. Actually conditions check current_output.
        # Let's make agent-b control its own jump:
        b_calls = {"n": 0}

        async def _execute_v2(agent_name, task_id, context, stage_num, **kw):
            if agent_name == "agent-a":
                a_calls["n"] += 1
                return {"result": "analyzed"}
            b_calls["n"] += 1
            return {"visit": b_calls["n"]}

        planned = [
            {"category": "step-a", "agents": ["agent-a"], "parallel": False},
            {"category": "step-b", "agents": ["agent-b"], "parallel": False},
        ]
        stage_defs = [
            {
                "name": "stage-a",
                "category": "step-a",
                "required": True,
                "conditions": [],
            },
            {
                "name": "stage-b",
                "category": "step-b",
                "required": True,
                "conditions": [
                    {"simple": "visit == 1", "yes": "stage-a", "maxRepeats": 3},
                ],
            },
        ]

        runner, create_iter_calls, agent_log = _make_runner(_execute_v2)

        p1, p2 = _patch_git()
        with p1, p2:
            failed = await runner.execute_running_phase(
                "task-indep-1", planned, stage_defs, "/tmp/repo", "main",
            )

        assert not failed
        # Flow: A(1) -> B(1, visit=1, jumps to A) -> A(2) -> B(2, visit=2, no jump)
        assert agent_log == ["agent-a", "agent-b", "agent-a", "agent-b"]

        a_iters = [
            iteration for (sn, cat, agent, iteration) in create_iter_calls
            if cat == "step-a"
        ]
        b_iters = [
            iteration for (sn, cat, agent, iteration) in create_iter_calls
            if cat == "step-b"
        ]
        assert a_iters == [2], f"stage-a revisit should have iteration=2; got {a_iters}"
        assert b_iters == [2], f"stage-b revisit should have iteration=2; got {b_iters}"


# ---------------------------------------------------------------------------
# Test 4: First visit never calls create_iteration_stage
# ---------------------------------------------------------------------------


class TestFirstVisitNoCreateIteration:
    """On the first visit to any stage, create_iteration_stage must NOT be called."""

    @pytest.mark.asyncio
    async def test_no_create_iteration_on_first_visit(self):
        """Simple linear pipeline: A -> B -> C. No revisits."""

        async def _execute(agent_name, task_id, context, stage_num, **kw):
            return {"status": "ok"}

        planned = [
            {"category": "cat-a", "agents": ["a"], "parallel": False},
            {"category": "cat-b", "agents": ["b"], "parallel": False},
            {"category": "cat-c", "agents": ["c"], "parallel": False},
        ]
        stage_defs = [
            {"name": "s-a", "category": "cat-a", "required": True, "conditions": []},
            {"name": "s-b", "category": "cat-b", "required": True, "conditions": []},
            {"name": "s-c", "category": "cat-c", "required": True, "conditions": []},
        ]

        runner, create_iter_calls, agent_log = _make_runner(_execute)

        p1, p2 = _patch_git()
        with p1, p2:
            failed = await runner.execute_running_phase(
                "task-linear-1", planned, stage_defs, "/tmp/repo", "main",
            )

        assert not failed
        assert agent_log == ["a", "b", "c"]
        assert create_iter_calls == [], (
            f"No create_iteration_stage calls expected for first visits; got {create_iter_calls}"
        )


# ---------------------------------------------------------------------------
# Test 5: maxRepeats guard stops infinite loops
# ---------------------------------------------------------------------------


class TestMaxRepeatsGuard:
    """maxRepeats must prevent runaway loops even when the condition keeps jumping."""

    @pytest.mark.asyncio
    async def test_max_repeats_stops_loop(self):
        """Stage always tries to jump to itself, but maxRepeats=2 limits it."""

        async def _execute(agent_name, task_id, context, stage_num, **kw):
            return {"always_true": 1}

        planned = [
            {"category": "loop", "agents": ["loop-agent"], "parallel": False},
            {"category": "end", "agents": ["end-agent"], "parallel": False},
        ]
        stage_defs = [
            {
                "name": "loop-stage",
                "category": "loop",
                "required": True,
                "conditions": [
                    {"simple": "always_true == 1", "yes": "loop-stage", "maxRepeats": 2},
                ],
            },
            {
                "name": "end-stage",
                "category": "end",
                "required": False,
                "conditions": [],
            },
        ]

        runner, create_iter_calls, agent_log = _make_runner(_execute)

        p1, p2 = _patch_git()
        with p1, p2:
            failed = await runner.execute_running_phase(
                "task-maxrep-1", planned, stage_defs, "/tmp/repo", "main",
            )

        assert not failed
        # maxRepeats=2 means the jump fires at most 2 times
        # (total visits = initial + 2 repeats = 3 at most)
        loop_count = agent_log.count("loop-agent")
        assert loop_count <= 3, (
            f"maxRepeats=2 should limit loop-agent to at most 3 visits; got {loop_count}"
        )
        # end-agent should still execute
        assert "end-agent" in agent_log


# ---------------------------------------------------------------------------
# Test 6: Iteration counter correct when stage has multiple agents
# ---------------------------------------------------------------------------


class TestMultiAgentStageIteration:
    """When a stage has multiple sequential agents, all share the same iteration."""

    @pytest.mark.asyncio
    async def test_multi_agent_same_iteration(self):
        """Stage with 2 agents revisited once: both get iteration=2.

        We track stage visits (not per-agent calls) so the condition can
        trigger correctly.  Both agents in the dual-stage share output via
        stage_output.update(), so the condition sees the last agent's output.
        """
        stage_visits = {"dual": 0}

        async def _execute(agent_name, task_id, context, stage_num, **kw):
            if agent_name == "agent-x":
                # First agent in the dual stage — increment visit counter
                stage_visits["dual"] += 1
                return {"visit": stage_visits["dual"]}
            if agent_name == "agent-y":
                # Second agent — keep the visit counter from agent-x
                return {"visit": stage_visits["dual"]}
            return {"done": True}

        planned = [
            {"category": "dual", "agents": ["agent-x", "agent-y"], "parallel": False},
            {"category": "end", "agents": ["end-agent"], "parallel": False},
        ]
        stage_defs = [
            {
                "name": "dual-stage",
                "category": "dual",
                "required": True,
                "conditions": [
                    # Jump back on first visit (visit==1), fall through on second
                    {"simple": "visit == 1", "yes": "dual-stage", "maxRepeats": 3},
                ],
            },
            {
                "name": "end-stage",
                "category": "end",
                "required": False,
                "conditions": [],
            },
        ]

        runner, create_iter_calls, agent_log = _make_runner(_execute)

        p1, p2 = _patch_git()
        with p1, p2:
            failed = await runner.execute_running_phase(
                "task-multi-agent-1", planned, stage_defs, "/tmp/repo", "main",
            )

        assert not failed
        # Flow: agent-x(1), agent-y(1) -> jump -> agent-x(2), agent-y(2) -> fall through -> end-agent
        assert agent_log == ["agent-x", "agent-y", "agent-x", "agent-y", "end-agent"]

        # On revisit, create_iteration_stage should be called for BOTH agents
        dual_calls = [
            (agent, iteration)
            for (sn, cat, agent, iteration) in create_iter_calls
            if cat == "dual"
        ]
        assert ("agent-x", 2) in dual_calls, f"agent-x should get iteration=2; calls={dual_calls}"
        assert ("agent-y", 2) in dual_calls, f"agent-y should get iteration=2; calls={dual_calls}"

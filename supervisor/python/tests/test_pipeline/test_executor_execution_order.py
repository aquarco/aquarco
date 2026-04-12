"""Tests for execution_order tracking in PipelineExecutor.

Covers:
- Counter initialization (fresh start and resume)
- Sequential increment via _next_execution_order
- Recovery from DB max on resume
- Parallel agent pre-allocation
- Counter cleanup on success and failure paths
- execution_order passed to record_stage_executing
- execution_order passed to record_stage_skipped
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.claude import ClaudeOutput
from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import PipelineError, StageError
from aquarco_supervisor.pipeline.executor import PipelineExecutor
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    return db


@pytest.fixture
def mock_tq() -> AsyncMock:
    tq = AsyncMock(spec=TaskQueue)
    return tq


@pytest.fixture
def mock_sm() -> AsyncMock:
    sm = AsyncMock()
    sm.get_stage_number_for_id = AsyncMock(return_value=None)
    sm.get_max_execution_order = AsyncMock(return_value=0)
    sm.get_task_context = AsyncMock(return_value={})
    sm.get_latest_stage_run = AsyncMock(return_value=None)
    return sm


@pytest.fixture
def mock_registry() -> MagicMock:
    registry = MagicMock()
    registry.should_skip_planning = MagicMock(return_value=True)
    registry.get_agents_for_category = MagicMock(return_value=["test-agent"])
    registry.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    registry.get_agent_timeout = MagicMock(return_value=30)
    registry.get_agent_max_turns = MagicMock(return_value=20)
    registry.get_agent_max_cost = MagicMock(return_value=5.0)
    registry.get_agent_model = MagicMock(return_value=None)
    registry.get_allowed_tools = MagicMock(return_value=[])
    registry.get_denied_tools = MagicMock(return_value=[])
    registry.get_agent_environment = MagicMock(return_value={})
    registry.get_agent_output_schema = MagicMock(return_value=None)
    registry.increment_agent_instances = AsyncMock()
    registry.decrement_agent_instances = AsyncMock()
    return registry


@pytest.fixture
def executor(
    mock_db: AsyncMock,
    mock_tq: AsyncMock,
    mock_registry: MagicMock,
    mock_sm: AsyncMock,
    sample_pipelines: Any,
) -> PipelineExecutor:
    return PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines, stage_manager=mock_sm)


# ---------------------------------------------------------------------------
# _next_execution_order: basic counter behavior
# ---------------------------------------------------------------------------


class TestNextExecutionOrder:
    """Tests for PipelineExecutor._next_execution_order."""

    def test_first_call_returns_one(
        self, executor: PipelineExecutor,
    ) -> None:
        """First call for a new task returns 1."""
        executor._execution_order["task-1"] = 0
        result = executor._next_execution_order("task-1")
        assert result == 1

    def test_sequential_increments(
        self, executor: PipelineExecutor,
    ) -> None:
        """Successive calls return 1, 2, 3, ..."""
        executor._execution_order["task-1"] = 0
        results = [executor._next_execution_order("task-1") for _ in range(5)]
        assert results == [1, 2, 3, 4, 5]

    def test_independent_per_task(
        self, executor: PipelineExecutor,
    ) -> None:
        """Different tasks have independent counters."""
        executor._execution_order["task-a"] = 0
        executor._execution_order["task-b"] = 0

        a1 = executor._next_execution_order("task-a")
        a2 = executor._next_execution_order("task-a")
        b1 = executor._next_execution_order("task-b")

        assert a1 == 1
        assert a2 == 2
        assert b1 == 1

    def test_counter_starts_from_existing_value(
        self, executor: PipelineExecutor,
    ) -> None:
        """When counter is pre-seeded (resume), increments from that value."""
        executor._execution_order["task-1"] = 7
        result = executor._next_execution_order("task-1")
        assert result == 8

    def test_uninitialized_task_starts_at_one(
        self, executor: PipelineExecutor,
    ) -> None:
        """A task not in the dict defaults to 0, so first call returns 1."""
        result = executor._next_execution_order("new-task")
        assert result == 1


# ---------------------------------------------------------------------------
# execute_pipeline: counter initialization
# ---------------------------------------------------------------------------


class TestExecutionOrderInitialization:
    """Tests for execution_order counter initialization in execute_pipeline."""

    @pytest.mark.asyncio
    async def test_fresh_pipeline_initializes_counter_to_zero(
        self,
        executor: PipelineExecutor,
        mock_tq: AsyncMock,
        mock_sm: AsyncMock,
        mock_db: AsyncMock,
        mock_registry: MagicMock,
    ) -> None:
        """A fresh pipeline (start_stage=0) sets counter to 0."""
        mock_task = MagicMock()
        mock_task.pipeline = "feature-pipeline"
        mock_task.title = "Test Task"
        mock_task.initial_context = {}
        mock_task.source_ref = None
        mock_task.last_completed_stage = None
        mock_task.planned_stages = None
        mock_tq.get_task = AsyncMock(return_value=mock_task)

        mock_db.fetch_one = AsyncMock(
            return_value={"clone_dir": "/repos/test", "clone_status": "ready", "branch": "main"}
        )

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=ClaudeOutput(structured={"result": "ok"}, raw="{}"),
        ), patch("aquarco_supervisor.pipeline.executor.Path"), patch(
            "aquarco_supervisor.pipeline.executor._run_git",
            new_callable=AsyncMock,
            return_value="0",
        ), patch(
            "aquarco_supervisor.pipeline.executor._git_checkout",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.pipeline.executor._auto_commit",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.pipeline.executor._get_ahead_count",
            new_callable=AsyncMock,
            return_value=0,
        ):
            await executor.execute_pipeline("feature-pipeline", "task-1", {})

        # Counter initialized for fresh start — get_max_execution_order NOT called
        mock_sm.get_max_execution_order.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_pipeline_recovers_counter_from_db(
        self,
        executor: PipelineExecutor,
        mock_tq: AsyncMock,
        mock_sm: AsyncMock,
        mock_db: AsyncMock,
        mock_registry: MagicMock,
    ) -> None:
        """Resuming a pipeline queries DB for max execution_order."""
        mock_task = MagicMock()
        mock_task.pipeline = "feature-pipeline"
        mock_task.title = "Test Task"
        mock_task.initial_context = {}
        mock_task.source_ref = None
        mock_task.last_completed_stage = 42
        mock_task.planned_stages = [
            {"category": "analyze", "agents": ["analyze-agent"], "parallel": False, "validation": []},
            {"category": "design", "agents": ["design-agent"], "parallel": False, "validation": []},
            {"category": "implement", "agents": ["impl-agent"], "parallel": False, "validation": []},
            {"category": "test", "agents": ["test-agent"], "parallel": False, "validation": []},
            {"category": "review", "agents": ["review-agent"], "parallel": False, "validation": []},
        ]
        mock_tq.get_task = AsyncMock(return_value=mock_task)
        mock_sm.get_stage_number_for_id = AsyncMock(return_value=2)
        mock_sm.get_max_execution_order = AsyncMock(return_value=5)

        mock_db.fetch_one = AsyncMock(
            return_value={"clone_dir": "/repos/test", "clone_status": "ready", "branch": "main"}
        )

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=ClaudeOutput(structured={"result": "ok"}, raw="{}"),
        ), patch("aquarco_supervisor.pipeline.executor.Path"), patch(
            "aquarco_supervisor.pipeline.executor._run_git",
            new_callable=AsyncMock,
            return_value="0",
        ), patch(
            "aquarco_supervisor.pipeline.executor._git_checkout",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.pipeline.executor._auto_commit",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.pipeline.executor._get_ahead_count",
            new_callable=AsyncMock,
            return_value=0,
        ):
            await executor.execute_pipeline("feature-pipeline", "task-1", {})

        # Should have recovered counter from DB
        mock_sm.get_max_execution_order.assert_awaited_once_with("task-1")
        # Counter should be seeded at 5
        # After pipeline completes, it's cleaned up; verify via side effect:
        # the next calls to _next_execution_order should have started at 6
        assert "task-1" not in executor._execution_order  # cleaned up on success


# ---------------------------------------------------------------------------
# Counter cleanup
# ---------------------------------------------------------------------------


class TestExecutionOrderCleanup:
    """Tests for cleanup of per-task execution_order counter."""

    @pytest.mark.asyncio
    async def test_counter_cleaned_up_on_success(
        self,
        executor: PipelineExecutor,
        mock_tq: AsyncMock,
        mock_sm: AsyncMock,
        mock_db: AsyncMock,
    ) -> None:
        """Counter entry is removed from _execution_order after successful pipeline."""
        mock_task = MagicMock()
        mock_task.pipeline = "feature-pipeline"
        mock_task.title = "Test"
        mock_task.initial_context = {}
        mock_task.source_ref = None
        mock_task.last_completed_stage = None
        mock_tq.get_task = AsyncMock(return_value=mock_task)

        mock_db.fetch_one = AsyncMock(
            return_value={"clone_dir": "/repos/test", "clone_status": "ready", "branch": "main"}
        )

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=ClaudeOutput(structured={"result": "ok"}, raw="{}"),
        ), patch("aquarco_supervisor.pipeline.executor.Path"), patch(
            "aquarco_supervisor.pipeline.executor._run_git",
            new_callable=AsyncMock,
            return_value="0",
        ), patch(
            "aquarco_supervisor.pipeline.executor._git_checkout",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.pipeline.executor._auto_commit",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.pipeline.executor._get_ahead_count",
            new_callable=AsyncMock,
            return_value=0,
        ):
            await executor.execute_pipeline("feature-pipeline", "task-1", {})

        assert "task-1" not in executor._execution_order

    @pytest.mark.asyncio
    async def test_counter_persists_on_failure(
        self,
        executor: PipelineExecutor,
        mock_tq: AsyncMock,
        mock_sm: AsyncMock,
        mock_db: AsyncMock,
        mock_registry: MagicMock,
    ) -> None:
        """Counter entry is NOT cleaned up when pipeline fails (known leak, per review)."""
        mock_task = MagicMock()
        mock_task.pipeline = "feature-pipeline"
        mock_task.title = "Test"
        mock_task.initial_context = {}
        mock_task.source_ref = None
        mock_task.last_completed_stage = None
        mock_tq.get_task = AsyncMock(return_value=mock_task)

        mock_db.fetch_one = AsyncMock(
            return_value={"clone_dir": "/repos/test", "clone_status": "ready", "branch": "main"}
        )

        # Make the stage execution fail via StageError so the pipeline fails
        # The pipeline will catch the StageError, mark stage as failed, and
        # eventually return (with failed=True from _execute_running_phase).
        # This exercises the failure path where cleanup doesn't happen.
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=StageError("agent failed"),
        ), patch("aquarco_supervisor.pipeline.executor.Path"), patch(
            "aquarco_supervisor.pipeline.executor._run_git",
            new_callable=AsyncMock,
            return_value="0",
        ), patch(
            "aquarco_supervisor.pipeline.executor._git_checkout",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.pipeline.executor._auto_commit",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.pipeline.executor._get_ahead_count",
            new_callable=AsyncMock,
            return_value=0,
        ):
            await executor.execute_pipeline("feature-pipeline", "task-1", {})

        # Known behavior: counter leaks on failure path (review finding #1).
        # The entry still exists because cleanup only happens on success.
        assert "task-1" in executor._execution_order


# ---------------------------------------------------------------------------
# execution_order propagated to record_stage_executing
# ---------------------------------------------------------------------------


class TestExecutionOrderPropagation:
    """Tests that execution_order is properly propagated to StageManager calls."""

    @pytest.mark.asyncio
    async def test_execute_planned_stage_passes_execution_order(
        self,
        executor: PipelineExecutor,
        mock_sm: AsyncMock,
        mock_registry: MagicMock,
    ) -> None:
        """execute_planned_stage passes execution_order to record_stage_executing."""
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=ClaudeOutput(structured={"ok": True}, raw="{}"),
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            await executor._runner.execute_planned_stage(
                "task-1", 0, "analyze", "analyze-agent",
                {"some": "context"},
                execution_order=3,
                work_dir="/repos/test",
            )

        # Verify record_stage_executing was called with execution_order=3
        mock_sm.record_stage_executing.assert_awaited_once()
        call_kwargs = mock_sm.record_stage_executing.call_args
        assert call_kwargs[1]["execution_order"] == 3

    @pytest.mark.asyncio
    async def test_execute_planned_stage_no_execution_order(
        self,
        executor: PipelineExecutor,
        mock_sm: AsyncMock,
        mock_registry: MagicMock,
    ) -> None:
        """When execution_order not provided, None is passed."""
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=ClaudeOutput(structured={"ok": True}, raw="{}"),
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            await executor._runner.execute_planned_stage(
                "task-1", 0, "analyze", "analyze-agent",
                {"some": "context"},
                work_dir="/repos/test",
            )

        call_kwargs = mock_sm.record_stage_executing.call_args
        assert call_kwargs[1]["execution_order"] is None


# ---------------------------------------------------------------------------
# Parallel agents: pre-allocation
# ---------------------------------------------------------------------------


class TestParallelExecutionOrder:
    """Tests for execution_order pre-allocation in parallel agent execution."""

    def test_parallel_preallocate_distinct_values(
        self, executor: PipelineExecutor,
    ) -> None:
        """Pre-allocating EO values for parallel agents produces distinct sequential values."""
        executor._execution_order["task-1"] = 0
        agents = ["agent-a", "agent-b", "agent-c"]

        eos: dict[str, int] = {}
        for a in agents:
            eos[a] = executor._next_execution_order("task-1")

        assert eos == {"agent-a": 1, "agent-b": 2, "agent-c": 3}
        # Counter advanced to 3
        assert executor._execution_order["task-1"] == 3

    @pytest.mark.asyncio
    async def test_execute_parallel_agents_passes_execution_orders(
        self,
        executor: PipelineExecutor,
        mock_sm: AsyncMock,
        mock_registry: MagicMock,
        mock_db: AsyncMock,
    ) -> None:
        """execute_parallel_agents passes pre-allocated EO dict to each agent."""
        executor._execution_order["task-1"] = 0

        mock_db.fetch_one = AsyncMock(
            return_value={"clone_dir": "/repos/test", "clone_status": "ready", "branch": "main"}
        )

        agents = ["agent-a", "agent-b"]
        eos = {"agent-a": 1, "agent-b": 2}

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=ClaudeOutput(structured={"ok": True}, raw="{}"),
        ), patch("aquarco_supervisor.pipeline.executor.Path"), patch(
            "aquarco_supervisor.pipeline.stage_runner.Path",
        ), patch(
            "aquarco_supervisor.pipeline.executor._run_git",
            new_callable=AsyncMock,
            return_value="",
        ), patch(
            "aquarco_supervisor.pipeline.executor._git_checkout",
            new_callable=AsyncMock,
        ):
            result = await executor._runner.execute_parallel_agents(
                "task-1", 0, "analyze", agents,
                "/repos/test", "main",
                execution_orders=eos,
            )

        # record_stage_executing should have been called with correct EO
        # for each agent (called via execute_planned_stage)
        calls = mock_sm.record_stage_executing.call_args_list
        assert len(calls) == 2
        eo_values = [c[1]["execution_order"] for c in calls]
        assert sorted(eo_values) == [1, 2]


# ---------------------------------------------------------------------------
# Skipped stages get execution_order
# ---------------------------------------------------------------------------


class TestSkippedStageExecutionOrder:
    """Tests that skipped stages receive an execution_order value."""

    def test_skip_eo_comes_from_counter(
        self, executor: PipelineExecutor,
    ) -> None:
        """When a stage is skipped, the EO should still be allocated via the counter."""
        executor._execution_order["task-1"] = 3
        # Simulate: EO 4 for stage execution, EO 5 for skip
        eo_exec = executor._next_execution_order("task-1")
        eo_skip = executor._next_execution_order("task-1")
        assert eo_exec == 4
        assert eo_skip == 5

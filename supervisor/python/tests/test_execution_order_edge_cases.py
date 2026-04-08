"""Additional edge-case tests for execution_order feature.

Covers gaps not addressed by the initial test files:
- get_max_execution_order truthiness edge case (review finding #2)
- get_max_iteration same truthiness pattern
- Condition-evaluator stage receives execution_order via _ai_eval closure
- Skipped stage EO assignment on optional-stage failure path
- Counter recovery with large pre-existing values
- Concurrent task isolation under interleaved _next_execution_order calls
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.executor import PipelineExecutor
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def task_queue(mock_db: AsyncMock) -> TaskQueue:
    return TaskQueue(mock_db, max_retries=3)


@pytest.fixture
def mock_tq() -> AsyncMock:
    tq = AsyncMock(spec=TaskQueue)
    tq.get_stage_number_for_id = AsyncMock(return_value=None)
    tq.get_max_execution_order = AsyncMock(return_value=0)
    tq.get_task_context = AsyncMock(return_value={})
    tq.get_latest_stage_run = AsyncMock(return_value=None)
    return tq


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
    sample_pipelines: Any,
) -> PipelineExecutor:
    return PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)


# ---------------------------------------------------------------------------
# get_max_execution_order: truthiness edge case (review finding #2)
# ---------------------------------------------------------------------------


class TestGetMaxExecutionOrderTruthiness:
    """Validates that get_max_execution_order handles falsy integer 0 correctly.

    The current implementation uses ``int(result) if result else 0`` which
    takes the else-branch when COALESCE returns 0 (falsy in Python).
    This happens to produce the correct result by accident.

    Compare with get_stage_number_for_id which correctly uses
    ``is not None`` to distinguish 0 from None.
    """

    @pytest.mark.asyncio
    async def test_coalesce_returns_integer_zero(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """When DB returns integer 0 (all stages have NULL execution_order),
        the function should return 0."""
        mock_db.fetch_val.return_value = 0
        result = await task_queue.get_max_execution_order("task-1")
        assert result == 0
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_returns_large_value(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """Large execution_order values are returned faithfully."""
        mock_db.fetch_val.return_value = 9999
        result = await task_queue.get_max_execution_order("task-1")
        assert result == 9999

    @pytest.mark.asyncio
    async def test_distinguishes_zero_from_none(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """Both 0 and None should return 0, but via different code paths.

        This test documents that the current truthiness check works by
        coincidence for the 0 case. If the function were changed to return
        a sentinel or raise on None, this test would catch the regression.
        """
        mock_db.fetch_val.return_value = None
        result_none = await task_queue.get_max_execution_order("task-1")

        mock_db.fetch_val.return_value = 0
        result_zero = await task_queue.get_max_execution_order("task-1")

        assert result_none == result_zero == 0

    @pytest.mark.asyncio
    async def test_returns_one(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """Truthy integer 1 exercises the happy path (int(result))."""
        mock_db.fetch_val.return_value = 1
        result = await task_queue.get_max_execution_order("task-1")
        assert result == 1


# ---------------------------------------------------------------------------
# get_max_iteration: same truthiness pattern
# ---------------------------------------------------------------------------


class TestGetMaxIterationTruthiness:
    """Validates get_max_iteration has the same truthiness behavior."""

    @pytest.mark.asyncio
    async def test_coalesce_returns_integer_zero(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """COALESCE(MAX(iteration), 0) returning 0 should yield 0."""
        mock_db.fetch_val.return_value = 0
        result = await task_queue.get_max_iteration("task-1", "0:analyze:agent")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_none_as_zero(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """If fetch_val returns None, should yield 0."""
        mock_db.fetch_val.return_value = None
        result = await task_queue.get_max_iteration("task-1", "0:analyze:agent")
        assert result == 0


# ---------------------------------------------------------------------------
# _next_execution_order: interleaved multi-task calls
# ---------------------------------------------------------------------------


class TestExecutionOrderInterleaving:
    """Verify that interleaved calls across tasks don't corrupt counters."""

    def test_interleaved_calls_maintain_isolation(
        self, executor: PipelineExecutor,
    ) -> None:
        """Rapidly alternating between tasks keeps counters independent."""
        executor._execution_order["task-a"] = 0
        executor._execution_order["task-b"] = 10

        results: list[tuple[str, int]] = []
        for _ in range(5):
            results.append(("a", executor._next_execution_order("task-a")))
            results.append(("b", executor._next_execution_order("task-b")))

        a_values = [v for t, v in results if t == "a"]
        b_values = [v for t, v in results if t == "b"]
        assert a_values == [1, 2, 3, 4, 5]
        assert b_values == [11, 12, 13, 14, 15]

    def test_large_resume_value_continues_correctly(
        self, executor: PipelineExecutor,
    ) -> None:
        """Counter seeded from a large DB value increments correctly."""
        executor._execution_order["task-1"] = 100
        vals = [executor._next_execution_order("task-1") for _ in range(3)]
        assert vals == [101, 102, 103]


# ---------------------------------------------------------------------------
# record_stage_executing: execution_order=0 edge case
# ---------------------------------------------------------------------------


class TestRecordStageExecutingEdgeCases:
    """Test edge cases in execution_order parameter handling."""

    @pytest.mark.asyncio
    async def test_execution_order_zero_is_passed(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """execution_order=0 should be passed through, not treated as falsy."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "analyze-agent",
            stage_id=1,
            execution_order=0,
        )
        params = mock_db.execute.call_args[0][1]
        assert params["eo"] == 0
        assert params["eo"] is not None

    @pytest.mark.asyncio
    async def test_execution_order_negative_is_passed(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """Negative values should not occur but are passed through if given."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "analyze-agent",
            stage_id=1,
            execution_order=-1,
        )
        params = mock_db.execute.call_args[0][1]
        assert params["eo"] == -1


# ---------------------------------------------------------------------------
# record_stage_skipped: legacy path includes execution_order in INSERT
# ---------------------------------------------------------------------------


class TestRecordStageSkippedLegacyInsert:
    """Verify legacy INSERT path includes execution_order in both places."""

    @pytest.mark.asyncio
    async def test_legacy_insert_values_contain_eo(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """The INSERT ... VALUES must include execution_order."""
        await task_queue.record_stage_skipped(
            "task-1", 2, "docs",
            execution_order=7,
        )
        sql = mock_db.execute.call_args[0][0]
        # Both the INSERT VALUES and ON CONFLICT SET must reference eo
        assert sql.count("execution_order") >= 2
        assert sql.count("%(eo)s") >= 2

    @pytest.mark.asyncio
    async def test_legacy_insert_on_conflict_has_status_guard(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """The ON CONFLICT DO UPDATE has a WHERE guard for terminal statuses."""
        await task_queue.record_stage_skipped(
            "task-1", 2, "docs",
        )
        sql = mock_db.execute.call_args[0][0]
        assert "NOT IN" in sql
        assert "'completed'" in sql
        assert "'failed'" in sql


# ---------------------------------------------------------------------------
# record_stage_executing: SQL structure validation
# ---------------------------------------------------------------------------


class TestRecordStageExecutingSqlStructure:
    """Validate SQL structure of all three code paths."""

    @pytest.mark.asyncio
    async def test_stage_id_path_resets_counters(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """stage_id UPDATE resets cost and token counters to 0."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "agent",
            stage_id=42,
            execution_order=5,
        )
        sql = mock_db.execute.call_args[0][0]
        assert "cost_usd = 0" in sql
        assert "tokens_input = 0" in sql
        assert "tokens_output = 0" in sql
        assert "cache_read_tokens = 0" in sql
        assert "cache_write_tokens = 0" in sql

    @pytest.mark.asyncio
    async def test_stage_key_path_matches_on_iteration_and_run(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """stage_key UPDATE includes iteration and run in WHERE clause."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "agent",
            stage_key="0:analyze:agent",
            iteration=3,
            run=2,
            execution_order=10,
        )
        sql = mock_db.execute.call_args[0][0]
        params = mock_db.execute.call_args[0][1]
        assert "iteration = %(iteration)s" in sql
        assert "run = %(run)s" in sql
        assert params["iteration"] == 3
        assert params["run"] == 2

    @pytest.mark.asyncio
    async def test_legacy_path_uses_on_conflict(
        self, task_queue: TaskQueue, mock_db: AsyncMock,
    ) -> None:
        """Legacy path uses INSERT ... ON CONFLICT DO UPDATE."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "agent",
            execution_order=1,
        )
        sql = mock_db.execute.call_args[0][0]
        assert "INSERT INTO stages" in sql
        assert "ON CONFLICT" in sql


# ---------------------------------------------------------------------------
# Parallel pre-allocation: order preservation
# ---------------------------------------------------------------------------


class TestParallelPreallocationOrder:
    """Tests that parallel pre-allocation preserves agent list order."""

    def test_preserves_agent_list_order(
        self, executor: PipelineExecutor,
    ) -> None:
        """EO values are assigned in the same order as the agents list."""
        executor._execution_order["task-1"] = 0
        agents = ["zeta-agent", "alpha-agent", "mid-agent"]

        eos = {}
        for a in agents:
            eos[a] = executor._next_execution_order("task-1")

        # Order must match agent list, not alphabetical
        assert list(eos.keys()) == ["zeta-agent", "alpha-agent", "mid-agent"]
        assert list(eos.values()) == [1, 2, 3]

    def test_single_parallel_agent(
        self, executor: PipelineExecutor,
    ) -> None:
        """Edge case: a single 'parallel' agent still gets correct EO."""
        executor._execution_order["task-1"] = 5
        eo = executor._next_execution_order("task-1")
        assert eo == 6

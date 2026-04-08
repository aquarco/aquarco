"""Comprehensive tests for GitHub #111 -- completed stage rerun guard.

Covers edge cases and integration scenarios beyond the unit tests in
test_executor_conditions.py and test_task_queue_iteration.py:

  AC1: Completed stages are NEVER re-executed (primary guard in executor)
  AC2: SQL defense-in-depth guards prevent status corruption at the DB layer
  AC3: get_stage_structured_output handles all driver return types gracefully
  AC4: Existing retry/resume flows (failed, rate_limited, pending) are unbroken
  AC5: The completed guard works correctly at iteration > 1 (condition loops)
  AC6: store_stage_output lacks the SQL guard (defense-in-depth gap documented)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.executor import PipelineExecutor
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor() -> PipelineExecutor:
    """Create a PipelineExecutor with mocked dependencies."""
    mock_db = AsyncMock(spec=Database)
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = MagicMock()
    executor = PipelineExecutor.__new__(PipelineExecutor)
    executor._db = mock_db
    executor._tq = mock_tq
    executor._registry = mock_registry
    executor._pipelines = []
    executor._execution_order = {}
    return executor


def _make_task_queue() -> tuple[TaskQueue, AsyncMock]:
    """Create a TaskQueue with a mocked database."""
    mock_db = AsyncMock(spec=Database)
    tq = TaskQueue(mock_db, max_retries=3)
    return tq, mock_db


# ===========================================================================
# AC1 — Completed stages are NEVER re-executed
# ===========================================================================


class TestCompletedStageGuardEdgeCases:
    """Edge cases for the completed-stage guard in _execute_planned_stage."""

    @pytest.mark.asyncio
    async def test_completed_stage_at_iteration_2_returns_existing_output(self) -> None:
        """Guard fires correctly when iteration > 1 (condition loop revisit)."""
        executor = _make_executor()

        executor._tq.get_latest_stage_run = AsyncMock(return_value={
            "id": 100, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._tq.get_stage_structured_output = AsyncMock(return_value={
            "summary": "iteration-2 completed",
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-loop", 1, "implement", "impl-agent",
            {"context": "loop"}, iteration=2,
        )

        assert stage_id == 100
        assert output["summary"] == "iteration-2 completed"
        executor._tq.record_stage_executing.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_stage_at_high_iteration_still_blocked(self) -> None:
        """Guard works for iteration=10 (deeply nested condition loop)."""
        executor = _make_executor()

        executor._tq.get_latest_stage_run = AsyncMock(return_value={
            "id": 200, "status": "completed", "run": 3,
            "error_message": None, "session_id": "sess-old",
        })
        executor._tq.get_stage_structured_output = AsyncMock(return_value={
            "_subtype": "success", "findings": [],
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-deep", 0, "review", "review-agent",
            {"depth": 10}, iteration=10,
        )

        assert stage_id == 200
        assert output["_subtype"] == "success"
        executor._tq.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_stage_with_explicit_stage_id_still_blocked(self) -> None:
        """Even when caller provides a stage_id, the guard still fires if status is completed."""
        executor = _make_executor()

        executor._tq.get_latest_stage_run = AsyncMock(return_value={
            "id": 300, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._tq.get_stage_structured_output = AsyncMock(return_value={"done": True})

        output, stage_id = await executor._execute_planned_stage(
            "task-x", 2, "test", "test-agent",
            {"ctx": "val"}, iteration=1, stage_id=300,
        )

        assert stage_id == 300
        assert output == {"done": True}
        executor._tq.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_guard_emits_warning_log(self) -> None:
        """The guard emits a structlog warning with expected fields."""
        executor = _make_executor()

        executor._tq.get_latest_stage_run = AsyncMock(return_value={
            "id": 42, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._tq.get_stage_structured_output = AsyncMock(return_value={})

        with patch("aquarco_supervisor.pipeline.executor.log") as mock_log:
            await executor._execute_planned_stage(
                "task-log", 0, "analyze", "analyze-agent",
                {}, iteration=1,
            )

            mock_log.warning.assert_called_once()
            call_args = mock_log.warning.call_args
            assert call_args[0][0] == "completed_stage_guard"
            assert call_args[1]["task_id"] == "task-log"
            assert call_args[1]["stage_key"] == "0:analyze:analyze-agent"
            assert call_args[1]["iteration"] == 1
            assert call_args[1]["stage_id"] == 42

    @pytest.mark.asyncio
    async def test_no_latest_run_proceeds_normally(self) -> None:
        """When get_latest_stage_run returns None, execution proceeds (first run)."""
        executor = _make_executor()

        executor._tq.get_latest_stage_run = AsyncMock(return_value=None)
        executor._tq.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._tq.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-new", 0, "analyze", "analyze-agent",
            {"first": "run"}, iteration=1,
        )

        # Should have proceeded to execute
        executor._tq.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once_with("analyze-agent")


# ===========================================================================
# AC4 — Existing retry/resume flows unbroken
# ===========================================================================


class TestRetryFlowsPreserved:
    """Ensure the completed guard does NOT block failed/rate_limited retries."""

    @pytest.mark.asyncio
    async def test_rate_limited_stage_creates_rerun(self) -> None:
        """A rate_limited stage creates a retry run, not blocked by the guard."""
        executor = _make_executor()

        executor._tq.get_latest_stage_run = AsyncMock(return_value={
            "id": 55, "status": "rate_limited", "run": 1,
            "error_message": "Rate limit hit", "session_id": "sess-rl",
        })
        executor._tq.create_rerun_stage = AsyncMock(return_value=56)
        executor._tq.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._tq.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-rl", 0, "analyze", "analyze-agent",
            {"ctx": "retry"}, iteration=1,
        )

        # create_rerun_stage called with run=2
        executor._tq.create_rerun_stage.assert_called_once()
        args = executor._tq.create_rerun_stage.call_args[0]
        assert args[6] == 2  # run=2
        # Execution proceeded
        executor._tq.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_stage_at_iteration_gt_1_still_retries(self) -> None:
        """A failed stage at iteration > 1 still creates a retry (not confused with completed)."""
        executor = _make_executor()

        executor._tq.get_latest_stage_run = AsyncMock(return_value={
            "id": 80, "status": "failed", "run": 2,
            "error_message": "timeout", "session_id": "sess-fail",
        })
        executor._tq.create_rerun_stage = AsyncMock(return_value=81)
        executor._tq.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._tq.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-fail-iter", 1, "implement", "impl-agent",
            {"ctx": "iter2-fail"}, iteration=2,
        )

        # Should retry with run=3
        executor._tq.create_rerun_stage.assert_called_once()
        args = executor._tq.create_rerun_stage.call_args[0]
        assert args[6] == 3  # run=3 (previous run was 2)
        executor._tq.record_stage_executing.assert_called_once()

    @pytest.mark.asyncio
    async def test_pending_stage_at_iteration_gt_1_still_executes(self) -> None:
        """A pending stage at iteration > 1 reuses the row and executes normally."""
        executor = _make_executor()

        executor._tq.get_latest_stage_run = AsyncMock(return_value={
            "id": 90, "status": "pending", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._tq.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._tq.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-pend", 0, "review", "review-agent",
            {"ctx": "pending-iter2"}, iteration=2, stage_id=90,
        )

        # Should proceed to execute, using the existing row
        executor._tq.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()


# ===========================================================================
# AC3 — get_stage_structured_output handles all types
# ===========================================================================


class TestGetStageStructuredOutputEdgeCases:
    """Edge cases for the get_stage_structured_output helper."""

    @pytest.mark.asyncio
    async def test_invalid_json_string_returns_none(self) -> None:
        """Malformed JSON string from the database returns None."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value="not valid json {{{")

        result = await tq.get_stage_structured_output(42)
        assert result is None

    @pytest.mark.asyncio
    async def test_integer_value_returns_none(self) -> None:
        """Non-dict, non-string value (integer) returns None via TypeError."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=12345)

        result = await tq.get_stage_structured_output(99)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_dict_returned_as_is(self) -> None:
        """An empty dict is a valid output and should be returned."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value={})

        result = await tq.get_stage_structured_output(1)
        assert result == {}

    @pytest.mark.asyncio
    async def test_nested_dict_returned_as_is(self) -> None:
        """Complex nested dict from psycopg auto-decoded JSONB returned intact."""
        tq, mock_db = _make_task_queue()
        expected = {
            "summary": "Analysis complete",
            "files_changed": ["a.py", "b.py"],
            "nested": {"key": [1, 2, 3]},
        }
        mock_db.fetch_val = AsyncMock(return_value=expected)

        result = await tq.get_stage_structured_output(50)
        assert result == expected

    @pytest.mark.asyncio
    async def test_json_string_with_unicode_parsed(self) -> None:
        """JSON string with unicode characters is parsed correctly."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(
            return_value='{"summary": "Analy\\u00e9 compl\\u00e8te"}'
        )

        result = await tq.get_stage_structured_output(7)
        assert result is not None
        assert result["summary"] == "Analy\u00e9 compl\u00e8te"

    @pytest.mark.asyncio
    async def test_empty_string_returns_none(self) -> None:
        """Empty string from the database is invalid JSON and returns None."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value="")

        result = await tq.get_stage_structured_output(3)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_value_returns_none(self) -> None:
        """A list is not a dict, so it should hit the json.loads path and return a list...
        Actually let's check: if raw is a list, isinstance(raw, dict) is False,
        so it falls through to json.loads(raw) which will raise TypeError.
        """
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=[1, 2, 3])

        result = await tq.get_stage_structured_output(5)
        # A list is not a dict, so json.loads([1,2,3]) -> TypeError -> None
        assert result is None


# ===========================================================================
# AC2 — SQL defense-in-depth guards in record_stage_executing
# ===========================================================================


class TestRecordStageExecutingSQLGuards:
    """Verify all three SQL paths in record_stage_executing protect against
    overwriting completed stages."""

    @pytest.mark.asyncio
    async def test_stage_id_path_sql_has_completed_guard(self) -> None:
        """stage_id UPDATE path includes AND status != 'completed'."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-1", 0, "implement", "impl-agent",
            stage_id=10, stage_key="0:implement:impl-agent",
        )
        sql = mock_db.execute.call_args[0][0]
        assert "status != 'completed'" in sql
        assert "WHERE id = %(id)s" in sql

    @pytest.mark.asyncio
    async def test_stage_key_path_sql_has_completed_guard(self) -> None:
        """stage_key UPDATE path includes AND status != 'completed'."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-1", 0, "implement", "impl-agent",
            stage_key="0:implement:impl-agent",
        )
        sql = mock_db.execute.call_args[0][0]
        assert "status != 'completed'" in sql
        assert "stage_key = %(stage_key)s" in sql

    @pytest.mark.asyncio
    async def test_legacy_path_sql_has_completed_guard(self) -> None:
        """Legacy ON CONFLICT path includes WHERE stages.status != 'completed'."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-1", 0, "implement", "impl-agent",
        )
        sql = mock_db.execute.call_args[0][0]
        assert "status != 'completed'" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_stage_id_path_passes_correct_params(self) -> None:
        """stage_id path passes correct parameters including execution_order."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-2", 1, "review", "review-agent",
            stage_id=42, stage_key="1:review:review-agent",
            iteration=2, run=3,
            input_context={"key": "value"},
            execution_order=5,
        )
        params = mock_db.execute.call_args[0][1]
        assert params["id"] == 42
        assert params["agent"] == "review-agent"
        assert params["eo"] == 5
        assert json.loads(params["input"]) == {"key": "value"}


# ===========================================================================
# AC6 — store_stage_output defense-in-depth gap (documentation)
# ===========================================================================


class TestStoreStageOutputDefenseGap:
    """Document the defense-in-depth gap flagged in code review.

    store_stage_output does NOT have AND status != 'completed' guards.
    The primary guard in _execute_planned_stage prevents this code path
    from being reached for completed stages, so this is not a bug — but
    these tests document the gap for future hardening.
    """

    @pytest.mark.asyncio
    async def test_store_stage_output_stage_id_path_no_completed_guard(self) -> None:
        """Document: stage_id UPDATE in store_stage_output lacks status guard.

        This test verifies that the gap exists (so future hardening can flip it).
        """
        tq, mock_db = _make_task_queue()
        await tq.store_stage_output(
            "task-1", 0, "implement", "impl-agent",
            {"_subtype": "success", "_is_error": False},
            stage_id=10,
        )
        sql = mock_db.execute.call_args[0][0]
        # Verify the gap: no completed guard in store_stage_output
        assert "status != 'completed'" not in sql

    @pytest.mark.asyncio
    async def test_store_stage_output_stage_key_path_no_completed_guard(self) -> None:
        """Document: stage_key UPDATE in store_stage_output lacks status guard."""
        tq, mock_db = _make_task_queue()
        await tq.store_stage_output(
            "task-1", 0, "implement", "impl-agent",
            {"_subtype": "success", "_is_error": False},
            stage_key="0:implement:impl-agent",
            iteration=1, run=1,
        )
        sql = mock_db.execute.call_args[0][0]
        assert "status != 'completed'" not in sql

    @pytest.mark.asyncio
    async def test_store_stage_output_legacy_path_no_completed_guard(self) -> None:
        """Document: legacy ON CONFLICT path in store_stage_output lacks status guard."""
        tq, mock_db = _make_task_queue()
        await tq.store_stage_output(
            "task-1", 0, "implement", "impl-agent",
            {"_subtype": "success", "_is_error": False},
        )
        sql = mock_db.execute.call_args[0][0]
        # The legacy path has ON CONFLICT but no status guard
        assert "ON CONFLICT" in sql
        # No completed guard on the DO UPDATE SET part
        # (unlike record_stage_executing which has WHERE stages.status != 'completed')
        on_conflict_idx = sql.index("ON CONFLICT")
        sql_after_conflict = sql[on_conflict_idx:]
        assert "status != 'completed'" not in sql_after_conflict


# ===========================================================================
# AC5 — Integration: condition loop revisiting a completed stage
# ===========================================================================


class TestConditionLoopIntegration:
    """Integration-style tests simulating condition-driven loops
    where a completed stage is revisited."""

    @pytest.mark.asyncio
    async def test_second_visit_to_completed_stage_returns_cached_output(self) -> None:
        """Simulates: stage 0 (analyze) completes, condition jumps back from
        stage 1 (implement) to stage 0. The guard must return cached output
        without calling the agent again.

        This mirrors the exact bug scenario from GitHub #111.
        """
        executor = _make_executor()

        # --- First call: stage 0 has never run ---
        executor._tq.get_latest_stage_run = AsyncMock(return_value=None)
        executor._tq.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        first_output = {"_subtype": "success", "_is_error": False, "summary": "analyzed"}
        executor._execute_agent = AsyncMock(return_value=first_output)
        executor._tq.store_stage_output = AsyncMock()

        out1, sid1 = await executor._execute_planned_stage(
            "task-111", 0, "analyze", "analyze-agent",
            {"initial": "context"}, iteration=1,
        )
        assert out1["summary"] == "analyzed"
        assert executor._execute_agent.call_count == 1

        # --- Second call: stage 0 is now completed, revisited by condition jump ---
        executor._tq.get_latest_stage_run = AsyncMock(return_value={
            "id": sid1 or 1, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._tq.get_stage_structured_output = AsyncMock(return_value={
            "summary": "analyzed",
        })

        out2, sid2 = await executor._execute_planned_stage(
            "task-111", 0, "analyze", "analyze-agent",
            {"revisit": "context"}, iteration=1,
        )

        # Guard should have fired: same output returned, no agent call
        assert out2["summary"] == "analyzed"
        # Agent should NOT have been called a second time
        assert executor._execute_agent.call_count == 1
        executor._tq.record_stage_executing.assert_called_once()  # only first call

    @pytest.mark.asyncio
    async def test_new_iteration_of_stage_executes_normally(self) -> None:
        """When a condition loop creates a NEW iteration (iteration=2) for a stage,
        the new iteration should execute normally even though iteration=1 is completed.

        This is the correct behavior: each iteration is independent.
        """
        executor = _make_executor()

        # iteration=2 has never been run (get_latest_stage_run returns None for it)
        executor._tq.get_latest_stage_run = AsyncMock(return_value=None)
        executor._tq.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False, "summary": "re-analyzed",
        })
        executor._tq.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-iter", 0, "analyze", "analyze-agent",
            {"iter": 2}, iteration=2,
        )

        # Should execute normally for the new iteration
        assert output["summary"] == "re-analyzed"
        executor._tq.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()


# ===========================================================================
# get_latest_stage_run — query correctness
# ===========================================================================


class TestGetLatestStageRun:
    """Verify get_latest_stage_run queries with correct parameters."""

    @pytest.mark.asyncio
    async def test_queries_with_correct_params(self) -> None:
        """SQL parameters include task_id, stage_key, and iteration."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_one = AsyncMock(return_value=None)

        await tq.get_latest_stage_run("task-42", "1:review:review-agent", 3)

        params = mock_db.fetch_one.call_args[0][1]
        assert params["task_id"] == "task-42"
        assert params["stage_key"] == "1:review:review-agent"
        assert params["iteration"] == 3

    @pytest.mark.asyncio
    async def test_orders_by_run_desc(self) -> None:
        """Query orders by run DESC and limits to 1."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_one = AsyncMock(return_value=None)

        await tq.get_latest_stage_run("task-1", "0:analyze:agent")

        sql = mock_db.fetch_one.call_args[0][0]
        assert "ORDER BY run DESC" in sql
        assert "LIMIT 1" in sql

    @pytest.mark.asyncio
    async def test_selects_required_fields(self) -> None:
        """Query selects id, status, run, error_message, session_id."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_one = AsyncMock(return_value=None)

        await tq.get_latest_stage_run("task-1", "0:analyze:agent")

        sql = mock_db.fetch_one.call_args[0][0]
        for field in ("id", "status", "run", "error_message", "session_id"):
            assert field in sql

    @pytest.mark.asyncio
    async def test_default_iteration_is_1(self) -> None:
        """When iteration is not provided, it defaults to 1."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_one = AsyncMock(return_value=None)

        await tq.get_latest_stage_run("task-1", "0:analyze:agent")

        params = mock_db.fetch_one.call_args[0][1]
        assert params["iteration"] == 1

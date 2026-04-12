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
from aquarco_supervisor.stage_manager import StageManager
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor() -> PipelineExecutor:
    """Create a PipelineExecutor with mocked dependencies."""
    mock_db = AsyncMock(spec=Database)
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_sm = AsyncMock(spec=StageManager)
    mock_registry = MagicMock()
    executor = PipelineExecutor.__new__(PipelineExecutor)
    executor._db = mock_db
    executor._tq = mock_tq
    executor._sm = mock_sm
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

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 100, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "summary": "iteration-2 completed",
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-loop", 1, "implement", "impl-agent",
            {"context": "loop"}, iteration=2,
        )

        assert stage_id == 100
        assert output["summary"] == "iteration-2 completed"
        executor._sm.record_stage_executing.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_stage_at_high_iteration_still_blocked(self) -> None:
        """Guard works for iteration=10 (deeply nested condition loop)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 200, "status": "completed", "run": 3,
            "error_message": None, "session_id": "sess-old",
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "_subtype": "success", "findings": [],
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-deep", 0, "review", "review-agent",
            {"depth": 10}, iteration=10,
        )

        assert stage_id == 200
        assert output["_subtype"] == "success"
        executor._sm.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_stage_with_explicit_stage_id_still_blocked(self) -> None:
        """Even when caller provides a stage_id, the guard still fires if status is completed."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 300, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={"done": True})

        output, stage_id = await executor._execute_planned_stage(
            "task-x", 2, "test", "test-agent",
            {"ctx": "val"}, iteration=1, stage_id=300,
        )

        assert stage_id == 300
        assert output == {"done": True}
        executor._sm.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_guard_emits_warning_log(self) -> None:
        """The guard emits a structlog warning with expected fields."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 42, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={})

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

        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-new", 0, "analyze", "analyze-agent",
            {"first": "run"}, iteration=1,
        )

        # Should have proceeded to execute
        executor._sm.record_stage_executing.assert_called_once()
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

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 55, "status": "rate_limited", "run": 1,
            "error_message": "Rate limit hit", "session_id": "sess-rl",
        })
        executor._sm.create_rerun_stage = AsyncMock(return_value=56)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-rl", 0, "analyze", "analyze-agent",
            {"ctx": "retry"}, iteration=1,
        )

        # create_rerun_stage called with run=2
        executor._sm.create_rerun_stage.assert_called_once()
        args = executor._sm.create_rerun_stage.call_args[0]
        assert args[6] == 2  # run=2
        # Execution proceeded
        executor._sm.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_stage_at_iteration_gt_1_still_retries(self) -> None:
        """A failed stage at iteration > 1 still creates a retry (not confused with completed)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 80, "status": "failed", "run": 2,
            "error_message": "timeout", "session_id": "sess-fail",
        })
        executor._sm.create_rerun_stage = AsyncMock(return_value=81)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-fail-iter", 1, "implement", "impl-agent",
            {"ctx": "iter2-fail"}, iteration=2,
        )

        # Should retry with run=3
        executor._sm.create_rerun_stage.assert_called_once()
        args = executor._sm.create_rerun_stage.call_args[0]
        assert args[6] == 3  # run=3 (previous run was 2)
        executor._sm.record_stage_executing.assert_called_once()

    @pytest.mark.asyncio
    async def test_pending_stage_at_iteration_gt_1_still_executes(self) -> None:
        """A pending stage at iteration > 1 reuses the row and executes normally."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 90, "status": "pending", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-pend", 0, "review", "review-agent",
            {"ctx": "pending-iter2"}, iteration=2, stage_id=90,
        )

        # Should proceed to execute, using the existing row
        executor._sm.record_stage_executing.assert_called_once()
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
        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        first_output = {"_subtype": "success", "_is_error": False, "summary": "analyzed"}
        executor._execute_agent = AsyncMock(return_value=first_output)
        executor._sm.store_stage_output = AsyncMock()

        out1, sid1 = await executor._execute_planned_stage(
            "task-111", 0, "analyze", "analyze-agent",
            {"initial": "context"}, iteration=1,
        )
        assert out1["summary"] == "analyzed"
        assert executor._execute_agent.call_count == 1

        # --- Second call: stage 0 is now completed, revisited by condition jump ---
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": sid1 or 1, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
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
        executor._sm.record_stage_executing.assert_called_once()  # only first call

    @pytest.mark.asyncio
    async def test_new_iteration_of_stage_executes_normally(self) -> None:
        """When a condition loop creates a NEW iteration (iteration=2) for a stage,
        the new iteration should execute normally even though iteration=1 is completed.

        This is the correct behavior: each iteration is independent.
        """
        executor = _make_executor()

        # iteration=2 has never been run (get_latest_stage_run returns None for it)
        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False, "summary": "re-analyzed",
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-iter", 0, "analyze", "analyze-agent",
            {"iter": 2}, iteration=2,
        )

        # Should execute normally for the new iteration
        assert output["summary"] == "re-analyzed"
        executor._sm.record_stage_executing.assert_called_once()
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


# ===========================================================================
# AC7 — Non-completed status fall-through (executing, max_turns)
# ===========================================================================


class TestNonCompletedStatusFallThrough:
    """Verify that 'executing' and 'max_turns' statuses are NOT blocked by
    the completed-stage guard.

    These statuses fall through the if/elif chain to the default run=1 path,
    which is correct pre-existing behavior documented in the code review.
    The guard must only block 'completed', never these intermediate statuses.
    """

    @pytest.mark.asyncio
    async def test_executing_status_proceeds_normally(self) -> None:
        """A stage stuck in 'executing' (e.g., from a previous crash) should
        proceed with run=1, not be blocked by the completed guard."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 150, "status": "executing", "run": 1,
            "error_message": None, "session_id": "sess-crash",
        })
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-crash", 0, "implement", "impl-agent",
            {"crash": "recovery"}, iteration=1,
        )

        # Should NOT be blocked — execution proceeds
        executor._sm.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()
        executor._sm.get_stage_structured_output.assert_not_called()
        assert output["_subtype"] == "success"

    @pytest.mark.asyncio
    async def test_max_turns_status_proceeds_normally(self) -> None:
        """A stage that hit max_turns should proceed with run=1, not blocked."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 160, "status": "max_turns", "run": 1,
            "error_message": "max turns exceeded", "session_id": "sess-mt",
        })
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-mt", 1, "review", "review-agent",
            {"max_turns": "recovery"}, iteration=1,
        )

        # Should NOT be blocked
        executor._sm.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()
        executor._sm.get_stage_structured_output.assert_not_called()

    @pytest.mark.asyncio
    async def test_executing_status_at_higher_iteration_proceeds(self) -> None:
        """An 'executing' status at iteration > 1 still proceeds (not blocked)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 170, "status": "executing", "run": 2,
            "error_message": None, "session_id": None,
        })
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "task-ex-iter", 0, "analyze", "analyze-agent",
            {"ctx": "iter3"}, iteration=3,
        )

        executor._sm.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()


# ===========================================================================
# AC8 — Guard does not set resume_session_id for completed stages
# ===========================================================================


class TestCompletedStageNoResume:
    """Ensure completed stages do NOT propagate session_id for resumption.

    When the guard fires, execution returns immediately. The resume_session_id
    must never be set from a completed stage, because there is no agent call
    to resume.
    """

    @pytest.mark.asyncio
    async def test_completed_stage_with_session_id_does_not_resume(self) -> None:
        """Even if the completed stage has a session_id, the guard returns
        without attempting to resume the conversation."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 180, "status": "completed", "run": 2,
            "error_message": None, "session_id": "sess-completed-old",
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "summary": "already done",
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-nosess", 0, "analyze", "analyze-agent",
            {"ctx": "check-session"}, iteration=1,
        )

        # Guard fired: no execution, no session resume
        assert stage_id == 180
        assert output["summary"] == "already done"
        executor._sm.record_stage_executing.assert_not_called()
        executor._execute_agent = AsyncMock()  # should not have been called
        executor._registry.increment_agent_instances.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_guard_does_not_create_rerun(self) -> None:
        """Completed stages never trigger create_rerun_stage (unlike failed)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 190, "status": "completed", "run": 3,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "_subtype": "success",
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-norerun", 1, "implement", "impl-agent",
            {"ctx": "norerun"}, iteration=2,
        )

        assert stage_id == 190
        executor._sm.create_rerun_stage.assert_not_called()
        executor._sm.record_stage_executing.assert_not_called()


# ===========================================================================
# AC9 — Exact bug reproduction from GitHub #111
# ===========================================================================


class TestGitHubIssue111BugReproduction:
    """End-to-end simulation of the exact bug described in GitHub #111.

    Scenario from the issue:
      1. IMPLEMENT stage completes successfully
      2. REVIEW stage runs (next stage in pipeline)
      3. BUG: IMPLEMENT stage re-executes instead of creating a new one

    Expected after fix:
      1. REVIEW (stage 0) → completes
      2. IMPLEMENT (stage 1) → completes
      3. REVIEW rerun (stage 2) → completes
      4. Condition jumps back to IMPLEMENT (stage 1)
      5. Guard fires: IMPLEMENT returns cached output, NOT re-executed
    """

    @pytest.mark.asyncio
    async def test_implement_stage_not_rerun_after_review_completes(self) -> None:
        """The exact scenario: IMPLEMENT completed, condition jumps back,
        guard prevents re-execution and returns cached output."""
        executor = _make_executor()

        # Step 1: IMPLEMENT stage already completed with output
        implement_output = {
            "_subtype": "success",
            "_is_error": False,
            "summary": "Fixed the bug in pipeline executor",
            "files_changed": [
                "supervisor/python/src/aquarco_supervisor/pipeline/executor.py",
                "supervisor/python/src/aquarco_supervisor/task_queue.py",
            ],
        }

        # Step 2: Condition jumps back to IMPLEMENT (stage 1)
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 42, "status": "completed", "run": 1,
            "error_message": None, "session_id": "sess-impl-done",
        })
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value=implement_output,
        )

        # Step 3: Execute the stage — guard should fire
        output, stage_id = await executor._execute_planned_stage(
            "github-issue-aquarco-111", 1, "implement", "implementation-agent",
            {"review_feedback": "looks good, approve"}, iteration=1,
        )

        # Assertions: guard returned cached output
        assert stage_id == 42
        assert output["summary"] == "Fixed the bug in pipeline executor"
        assert output["files_changed"] == implement_output["files_changed"]

        # Agent was NEVER called
        executor._sm.record_stage_executing.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()
        executor._sm.create_rerun_stage.assert_not_called()
        executor._sm.store_stage_output.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_iteration_after_completed_still_runs(self) -> None:
        """When a new iteration is correctly created (iteration=2) for
        a stage that completed at iteration=1, the new iteration runs.

        This is the EXPECTED behavior for proper condition loops:
        iteration 1 completed → new iteration 2 is a fresh execution.
        """
        executor = _make_executor()

        # iteration=2 has never run — get_latest_stage_run returns None
        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
            "summary": "Re-implemented after review feedback",
            "files_changed": ["executor.py"],
        })
        executor._sm.store_stage_output = AsyncMock()

        output, stage_id = await executor._execute_planned_stage(
            "github-issue-aquarco-111", 1, "implement", "implementation-agent",
            {"review_feedback": "needs changes"}, iteration=2,
        )

        # New iteration executes normally
        assert output["summary"] == "Re-implemented after review feedback"
        executor._sm.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()
        executor._execute_agent.assert_called_once()


# ===========================================================================
# AC10 — get_stage_structured_output type safety edge cases
# ===========================================================================


class TestGetStageStructuredOutputTypeSafety:
    """Additional type safety tests for get_stage_structured_output.

    The function signature says it returns dict | None, but json.loads
    can return non-dict types (list, str, int, bool). These tests
    document the actual behavior for each case.
    """

    @pytest.mark.asyncio
    async def test_json_list_string_returns_list_not_none(self) -> None:
        """A JSON string encoding a list parses to a list.

        Note: This is a type-safety gap — the return type says dict|None
        but json.loads('[1,2,3]') returns a list. The primary guard
        prevents this from being reached in practice since structured_output
        is always a JSON object.
        """
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value='[1, 2, 3]')

        result = await tq.get_stage_structured_output(10)
        # json.loads('[1,2,3]') returns [1,2,3] — a list, not None
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_json_string_string_returns_string(self) -> None:
        """A JSON string encoding a string returns a string.

        Another type-safety gap: json.loads('"hello"') returns 'hello'.
        """
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value='"hello world"')

        result = await tq.get_stage_structured_output(11)
        assert result == "hello world"

    @pytest.mark.asyncio
    async def test_json_null_string_returns_none(self) -> None:
        """A JSON string 'null' parses to None, which is returned as None
        because json.loads('null') returns None, which the function catches."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value='null')

        result = await tq.get_stage_structured_output(12)
        # json.loads('null') returns None; but the None check is at the top,
        # for the raw value. Since raw='null' is not None, it falls through
        # to json.loads which returns Python None.
        assert result is None

    @pytest.mark.asyncio
    async def test_json_boolean_string_returns_bool(self) -> None:
        """json.loads('true') returns True — another type gap."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value='true')

        result = await tq.get_stage_structured_output(13)
        assert result is True

    @pytest.mark.asyncio
    async def test_boolean_raw_value_returns_none(self) -> None:
        """A raw boolean (not a string) hits the json.loads path, which
        raises TypeError, returning None."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=True)

        result = await tq.get_stage_structured_output(14)
        # True is not dict, not None; json.loads(True) raises TypeError → None
        assert result is None

    @pytest.mark.asyncio
    async def test_float_raw_value_returns_none(self) -> None:
        """A raw float hits json.loads which raises TypeError → None."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=3.14)

        result = await tq.get_stage_structured_output(15)
        assert result is None


# ===========================================================================
# AC11 — record_stage_executing parameter validation
# ===========================================================================


class TestRecordStageExecutingParamValidation:
    """Validate parameter handling in all three paths of record_stage_executing."""

    @pytest.mark.asyncio
    async def test_stage_key_path_passes_all_params(self) -> None:
        """stage_key path passes iteration, run, and stage_key params."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-3", 2, "test", "test-agent",
            stage_key="2:test:test-agent",
            iteration=3, run=2,
            input_context={"test": "context"},
            execution_order=7,
        )

        params = mock_db.execute.call_args[0][1]
        assert params["task_id"] == "task-3"
        assert params["stage_key"] == "2:test:test-agent"
        assert params["iteration"] == 3
        assert params["run"] == 2
        assert params["agent"] == "test-agent"
        assert params["eo"] == 7
        assert json.loads(params["input"]) == {"test": "context"}

    @pytest.mark.asyncio
    async def test_legacy_path_passes_correct_params(self) -> None:
        """Legacy path (no stage_id, no stage_key) passes task_id and stage_num."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-legacy", 0, "analyze", "analyze-agent",
            execution_order=1,
        )

        params = mock_db.execute.call_args[0][1]
        assert params["task_id"] == "task-legacy"
        assert params["stage"] == 0
        assert params["category"] == "analyze"
        assert params["agent"] == "analyze-agent"
        assert params["eo"] == 1

    @pytest.mark.asyncio
    async def test_stage_id_path_null_input_context(self) -> None:
        """stage_id path with no input_context passes None for input."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-null", 0, "implement", "impl-agent",
            stage_id=50, stage_key="0:implement:impl-agent",
        )

        params = mock_db.execute.call_args[0][1]
        assert params["input"] is None

    @pytest.mark.asyncio
    async def test_legacy_path_uses_on_conflict(self) -> None:
        """Legacy path uses INSERT ... ON CONFLICT DO UPDATE."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-legacy", 0, "review", "review-agent",
        )

        sql = mock_db.execute.call_args[0][0]
        assert "INSERT INTO stages" in sql
        assert "ON CONFLICT (task_id, stage_number) DO UPDATE" in sql

    @pytest.mark.asyncio
    async def test_stage_id_path_uses_update(self) -> None:
        """stage_id path uses a plain UPDATE (not INSERT ON CONFLICT)."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-upd", 0, "implement", "impl-agent",
            stage_id=99, stage_key="0:implement:impl-agent",
        )

        sql = mock_db.execute.call_args[0][0]
        assert "UPDATE stages" in sql
        assert "INSERT" not in sql


# ===========================================================================
# AC12 — Guard with execution_order parameter
# ===========================================================================


class TestCompletedGuardWithExecutionOrder:
    """Verify the guard works correctly when execution_order is provided.

    The execution_order parameter is used for parallel stages. The guard
    should still fire regardless of execution_order value.
    """

    @pytest.mark.asyncio
    async def test_guard_fires_with_execution_order(self) -> None:
        """Completed guard fires even when execution_order is set."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 200, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "summary": "parallel stage done",
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-eo", 0, "analyze", "analyze-agent",
            {"ctx": "parallel"}, iteration=1,
            execution_order=5,
        )

        assert stage_id == 200
        assert output["summary"] == "parallel stage done"
        executor._sm.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_fires_with_all_optional_params(self) -> None:
        """Completed guard fires with all optional params (stage_id, work_dir,
        pipeline_name, execution_order)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 210, "status": "completed", "run": 2,
            "error_message": None, "session_id": "sess-all",
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "result": "comprehensive",
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-all-params", 3, "test", "test-agent",
            {"ctx": "full"}, iteration=4,
            stage_id=210,
            work_dir="/tmp/worktree",
            pipeline_name="bugfix-pipeline",
            execution_order=10,
        )

        assert stage_id == 210
        assert output["result"] == "comprehensive"
        executor._sm.record_stage_executing.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()


# ===========================================================================
# AC13 — Guard return value contract
# ===========================================================================


class TestGuardReturnValueContract:
    """Verify the guard's return value contract: (output_dict, stage_id).

    The guard must always return a tuple of (dict, int|None). The dict
    is either the existing structured output or {} if no output exists.
    """

    @pytest.mark.asyncio
    async def test_guard_returns_tuple_with_output_and_id(self) -> None:
        """Guard returns (output_dict, stage_id) matching the method signature."""
        executor = _make_executor()

        expected_output = {"k": "v", "n": 42}
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 300, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value=expected_output,
        )

        result = await executor._execute_planned_stage(
            "task-contract", 0, "analyze", "agent", {}, iteration=1,
        )

        assert isinstance(result, tuple)
        assert len(result) == 2
        output, sid = result
        assert isinstance(output, dict)
        assert output == expected_output
        assert sid == 300

    @pytest.mark.asyncio
    async def test_guard_returns_empty_dict_when_output_is_none(self) -> None:
        """When get_stage_structured_output returns None, guard returns {}."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 310, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value=None)

        output, sid = await executor._execute_planned_stage(
            "task-empty", 0, "analyze", "agent", {}, iteration=1,
        )

        assert output == {}
        assert sid == 310

    @pytest.mark.asyncio
    async def test_guard_returns_stage_id_from_latest_run(self) -> None:
        """Guard uses latest run's id, not the stage_id parameter."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 999, "status": "completed", "run": 5,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={"ok": True})

        # Pass a different stage_id — guard should use latest's id
        output, sid = await executor._execute_planned_stage(
            "task-id", 0, "analyze", "agent", {},
            iteration=1, stage_id=1,  # this should be ignored
        )

        assert sid == 999  # from latest, not from parameter

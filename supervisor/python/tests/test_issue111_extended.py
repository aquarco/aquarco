"""Extended tests for GitHub #111 -- completed stage rerun guard.

Covers scenarios NOT covered by test_issue111_completed_stage_guard.py:

  1. Parallel execution path: guard fires via _execute_parallel_agents
  2. Error resilience: guard behavior when get_stage_structured_output raises
  3. record_stage_failed defense-in-depth gap (documents lack of status guard)
  4. Multiple runs at same iteration: guard blocks even after prior failures
  5. get_latest_stage_run edge cases with default iteration and missing fields
  6. Concurrent guard invocation (two coroutines, same stage)
  7. Guard with falsy-but-not-None output (empty string key, zero values)
  8. Stage key construction correctness in the guard path
  9. Parallel path hardcoded iteration=1 documentation
"""

from __future__ import annotations

import asyncio
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
# 1. Parallel execution path — completed guard fires via _execute_planned_stage
# ===========================================================================


class TestParallelExecutionCompletedGuard:
    """Verify the completed guard fires when _execute_parallel_agents delegates
    to _execute_planned_stage with a completed stage.

    The _execute_parallel_agents method calls _execute_planned_stage with
    hardcoded iteration=1. If that stage has already completed, the guard
    must still fire and return the cached output.
    """

    @pytest.mark.asyncio
    async def test_parallel_path_completed_stage_returns_cached_output(self) -> None:
        """When _execute_planned_stage is called by the parallel path with
        iteration=1 and the stage is already completed, the guard returns
        existing output without re-executing."""
        executor = _make_executor()

        # Setup: stage already completed at iteration=1
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 500, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "summary": "parallel agent done",
            "files_changed": ["x.py"],
        })

        # Simulate what _execute_parallel_agents does: call _execute_planned_stage
        # with iteration=1 (hardcoded in the parallel path)
        output, stage_id = await executor._execute_planned_stage(
            "task-parallel", 2, "analyze", "analyze-agent-1",
            {"accumulated": "context"}, iteration=1,
            stage_id=500,
        )

        assert stage_id == 500
        assert output["summary"] == "parallel agent done"
        executor._sm.record_stage_executing.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()

    @pytest.mark.asyncio
    async def test_parallel_path_hardcoded_iteration_1_documented(self) -> None:
        """Document: _execute_parallel_agents hardcodes iteration=1.

        This means if a parallel stage is revisited via a condition jump,
        the completed guard will catch it at iteration=1 and return the
        original output, masking the fact that the iteration should be 2+.
        This is a known pre-existing issue documented in the code review.
        """
        executor = _make_executor()

        # Stage was completed at iteration=1, run=1
        completed_output = {"_subtype": "success", "from": "iteration-1"}
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 510, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value=completed_output,
        )

        # Parallel path calls with iteration=1 even on a revisit
        output, sid = await executor._execute_planned_stage(
            "task-parallel-revisit", 3, "test", "test-agent",
            {"ctx": "revisit"}, iteration=1,
        )

        # The guard correctly returns the old output
        assert output == completed_output
        assert sid == 510
        # But this masks the fact that this should have been iteration=2
        executor._sm.record_stage_executing.assert_not_called()


# ===========================================================================
# 2. Error resilience — guard behavior when get_stage_structured_output raises
# ===========================================================================


class TestGuardErrorResilience:
    """Verify behavior when get_stage_structured_output encounters errors.

    The guard calls get_stage_structured_output to retrieve existing output.
    If this call fails, the guard should still not re-execute the stage.
    """

    @pytest.mark.asyncio
    async def test_guard_with_db_error_in_get_output_propagates_exception(self) -> None:
        """If get_stage_structured_output raises a DB error, the exception
        propagates (the guard does not swallow it)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 600, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(
            side_effect=Exception("DB connection lost"),
        )

        with pytest.raises(Exception, match="DB connection lost"):
            await executor._execute_planned_stage(
                "task-err", 0, "analyze", "agent", {}, iteration=1,
            )

        # Critical: even though get_output failed, the agent was never called
        executor._sm.record_stage_executing.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_with_get_latest_stage_run_error_propagates(self) -> None:
        """If get_latest_stage_run raises, the error propagates naturally."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(
            side_effect=Exception("Connection timeout"),
        )

        with pytest.raises(Exception, match="Connection timeout"):
            await executor._execute_planned_stage(
                "task-err2", 0, "analyze", "agent", {}, iteration=1,
            )

        executor._sm.record_stage_executing.assert_not_called()


# ===========================================================================
# 3. record_stage_failed defense-in-depth gap
# ===========================================================================


class TestRecordStageFailedDefenseGap:
    """Document: record_stage_failed does NOT have status != 'completed' guards.

    Similar to the store_stage_output gap documented in AC6, record_stage_failed
    also lacks the SQL guard. The primary guard in _execute_planned_stage prevents
    this path from being reached for completed stages, but these tests document
    the gap for future hardening.
    """

    @pytest.mark.asyncio
    async def test_record_stage_failed_stage_id_path_no_completed_guard(self) -> None:
        """stage_id UPDATE in record_stage_failed lacks status guard."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_failed(
            "task-1", 0, "some error",
            stage_id=10,
        )
        sql = mock_db.execute.call_args[0][0]
        assert "status != 'completed'" not in sql

    @pytest.mark.asyncio
    async def test_record_stage_failed_stage_key_path_no_completed_guard(self) -> None:
        """stage_key UPDATE in record_stage_failed lacks status guard."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_failed(
            "task-1", 0, "another error",
            stage_key="0:implement:impl-agent",
            iteration=1, run=1,
        )
        sql = mock_db.execute.call_args[0][0]
        assert "status != 'completed'" not in sql

    @pytest.mark.asyncio
    async def test_record_stage_failed_legacy_path_no_completed_guard(self) -> None:
        """Legacy UPDATE in record_stage_failed lacks status guard."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_failed(
            "task-1", 0, "legacy error",
        )
        sql = mock_db.execute.call_args[0][0]
        assert "status != 'completed'" not in sql

    @pytest.mark.asyncio
    async def test_record_stage_failed_preserves_session_id(self) -> None:
        """record_stage_failed correctly passes session_id to SQL params."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_failed(
            "task-1", 0, "timeout",
            stage_id=20, session_id="sess-123",
        )
        params = mock_db.execute.call_args[0][1]
        assert params["session_id"] == "sess-123"
        assert params["error"] == "timeout"


# ===========================================================================
# 4. Multiple runs at same iteration — guard blocks after prior failures
# ===========================================================================


class TestMultipleRunsCompletedGuard:
    """Verify the guard works correctly when a stage has multiple runs.

    Scenario: run=1 failed, run=2 succeeded (completed).
    A third attempt at the same iteration should be blocked by the guard.
    """

    @pytest.mark.asyncio
    async def test_completed_at_run_2_blocks_run_3(self) -> None:
        """When the latest run is completed (run=2 after run=1 failed),
        the guard blocks execution of a hypothetical run=3."""
        executor = _make_executor()

        # Latest run is completed at run=2
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 700, "status": "completed", "run": 2,
            "error_message": None, "session_id": "sess-run2",
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "summary": "succeeded on retry",
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-multirun", 0, "implement", "impl-agent",
            {"attempt": 3}, iteration=1,
        )

        assert stage_id == 700
        assert output["summary"] == "succeeded on retry"
        executor._sm.record_stage_executing.assert_not_called()
        executor._sm.create_rerun_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_at_high_run_number_still_blocked(self) -> None:
        """Guard fires for stages completed at run=10 (many retries)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 710, "status": "completed", "run": 10,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "after_many_retries": True,
        })

        output, stage_id = await executor._execute_planned_stage(
            "task-manyretries", 1, "test", "test-agent",
            {}, iteration=1,
        )

        assert stage_id == 710
        assert output["after_many_retries"] is True
        executor._sm.record_stage_executing.assert_not_called()


# ===========================================================================
# 5. get_latest_stage_run edge cases
# ===========================================================================


class TestGetLatestStageRunExtended:
    """Extended edge cases for get_latest_stage_run."""

    @pytest.mark.asyncio
    async def test_returns_complete_row_structure(self) -> None:
        """Verify all expected fields are present in the returned dict."""
        tq, mock_db = _make_task_queue()
        expected = {
            "id": 42, "status": "completed", "run": 3,
            "error_message": "retried twice", "session_id": "sess-42",
        }
        mock_db.fetch_one = AsyncMock(return_value=expected)

        result = await tq.get_latest_stage_run("task-1", "0:analyze:agent", 2)

        assert result is not None
        assert result["id"] == 42
        assert result["status"] == "completed"
        assert result["run"] == 3
        assert result["error_message"] == "retried twice"
        assert result["session_id"] == "sess-42"

    @pytest.mark.asyncio
    async def test_different_iterations_are_isolated(self) -> None:
        """get_latest_stage_run queries with the specific iteration parameter.
        This ensures iteration=1 and iteration=2 don't interfere."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_one = AsyncMock(return_value=None)

        await tq.get_latest_stage_run("task-1", "0:impl:agent", iteration=2)

        params = mock_db.fetch_one.call_args[0][1]
        assert params["iteration"] == 2

    @pytest.mark.asyncio
    async def test_sql_filters_by_all_three_keys(self) -> None:
        """SQL WHERE clause includes task_id, stage_key, AND iteration."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_one = AsyncMock(return_value=None)

        await tq.get_latest_stage_run("task-42", "1:review:agent", 3)

        sql = mock_db.fetch_one.call_args[0][0]
        assert "task_id = %(task_id)s" in sql
        assert "stage_key = %(stage_key)s" in sql
        assert "iteration = %(iteration)s" in sql


# ===========================================================================
# 6. Concurrent guard invocation
# ===========================================================================


class TestConcurrentGuardInvocation:
    """Verify guard behavior when two coroutines invoke _execute_planned_stage
    for the same completed stage simultaneously.

    Both should return the cached output without re-executing.
    """

    @pytest.mark.asyncio
    async def test_two_concurrent_calls_both_return_cached_output(self) -> None:
        """Two concurrent calls to _execute_planned_stage on the same completed
        stage should both return the cached output without any agent invocation."""
        executor = _make_executor()

        cached_output = {"summary": "already done", "concurrent": True}
        call_count = 0

        async def mock_get_latest(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return {
                "id": 800, "status": "completed", "run": 1,
                "error_message": None, "session_id": None,
            }

        executor._sm.get_latest_stage_run = AsyncMock(side_effect=mock_get_latest)
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value=cached_output,
        )

        # Launch two concurrent calls
        results = await asyncio.gather(
            executor._execute_planned_stage(
                "task-concurrent", 0, "analyze", "agent",
                {"call": 1}, iteration=1,
            ),
            executor._execute_planned_stage(
                "task-concurrent", 0, "analyze", "agent",
                {"call": 2}, iteration=1,
            ),
        )

        # Both should return the cached output
        for output, sid in results:
            assert sid == 800
            assert output == cached_output

        # Agent never called (both hit the guard)
        executor._sm.record_stage_executing.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()

        # get_latest_stage_run was called twice (once per coroutine)
        assert call_count == 2


# ===========================================================================
# 7. Guard with falsy-but-not-None output
# ===========================================================================


class TestGuardWithFalsyOutput:
    """Verify the guard's `existing or {}` fallback handles falsy values.

    The guard does: `return existing or {}, latest.get("id")`
    This means falsy non-None values (empty dict, 0, False, "") will
    all be replaced with {}. These tests document that behavior.
    """

    @pytest.mark.asyncio
    async def test_guard_with_empty_dict_returns_empty_dict(self) -> None:
        """Empty dict from get_stage_structured_output is falsy, so
        `existing or {}` returns {} (same as empty dict)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 900, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={})

        output, sid = await executor._execute_planned_stage(
            "task-empty-output", 0, "analyze", "agent", {}, iteration=1,
        )

        assert output == {}
        assert sid == 900
        executor._sm.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_with_none_returns_empty_dict(self) -> None:
        """None from get_stage_structured_output returns {} via fallback."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 910, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value=None)

        output, sid = await executor._execute_planned_stage(
            "task-none-output", 0, "analyze", "agent", {}, iteration=1,
        )

        assert output == {}
        assert sid == 910

    @pytest.mark.asyncio
    async def test_guard_with_populated_output_returns_it(self) -> None:
        """Non-empty dict is truthy and returned as-is."""
        executor = _make_executor()

        expected = {"key": "value", "nested": {"a": 1}}
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 920, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value=expected,
        )

        output, sid = await executor._execute_planned_stage(
            "task-populated", 0, "analyze", "agent", {}, iteration=1,
        )

        assert output == expected
        assert sid == 920

    @pytest.mark.asyncio
    async def test_guard_with_zero_values_in_output_returns_them(self) -> None:
        """Dict with zero/false values is still truthy (non-empty dict)."""
        executor = _make_executor()

        expected = {"count": 0, "enabled": False, "name": ""}
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 930, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value=expected,
        )

        output, sid = await executor._execute_planned_stage(
            "task-zeroval", 0, "analyze", "agent", {}, iteration=1,
        )

        assert output == expected
        assert output["count"] == 0
        assert output["enabled"] is False
        assert output["name"] == ""


# ===========================================================================
# 8. Stage key construction correctness
# ===========================================================================


class TestStageKeyConstruction:
    """Verify that stage_key is constructed correctly in the guard path.

    The guard uses stage_key = f"{stage_num}:{category}:{agent_name}"
    and passes it to get_latest_stage_run. This must match what was
    stored when the stage originally completed.
    """

    @pytest.mark.asyncio
    async def test_guard_uses_correct_stage_key_format(self) -> None:
        """The stage_key passed to get_latest_stage_run matches the format."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1000, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={"ok": True})

        await executor._execute_planned_stage(
            "task-key", 3, "implement", "implementation-agent",
            {}, iteration=2,
        )

        # Verify get_latest_stage_run was called with correct stage_key
        call_args = executor._sm.get_latest_stage_run.call_args
        assert call_args[0][0] == "task-key"
        assert call_args[0][1] == "3:implement:implementation-agent"
        assert call_args[0][2] == 2  # iteration

    @pytest.mark.asyncio
    async def test_stage_key_with_special_characters_in_agent_name(self) -> None:
        """Agent names with hyphens and dots produce valid stage keys."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1010, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={"ok": True})

        await executor._execute_planned_stage(
            "task-1", 0, "analyze", "analyze-agent-v2.1",
            {}, iteration=1,
        )

        stage_key = executor._sm.get_latest_stage_run.call_args[0][1]
        assert stage_key == "0:analyze:analyze-agent-v2.1"


# ===========================================================================
# 9. Guard interaction with execution_order tracking
# ===========================================================================


class TestGuardExecutionOrderTracking:
    """Verify the guard's interaction with the _execution_order dict.

    When the guard fires (completed stage), execution_order should NOT
    be incremented since no actual execution takes place.
    """

    @pytest.mark.asyncio
    async def test_execution_order_not_advanced_for_completed_stage(self) -> None:
        """The guard returns before reaching the record_stage_executing call,
        so execution_order is never passed to the DB."""
        executor = _make_executor()
        executor._execution_order = {"task-eo": 5}

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1100, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={"done": True})

        output, sid = await executor._execute_planned_stage(
            "task-eo", 0, "analyze", "agent",
            {}, iteration=1, execution_order=6,
        )

        # Guard fired: execution_order was never used
        executor._sm.record_stage_executing.assert_not_called()
        # The _execution_order dict should not have been modified by the guard
        assert executor._execution_order == {"task-eo": 5}


# ===========================================================================
# 10. get_stage_structured_output SQL correctness
# ===========================================================================


class TestGetStageStructuredOutputSQL:
    """Verify the SQL and parameter correctness of get_stage_structured_output."""

    @pytest.mark.asyncio
    async def test_queries_by_primary_key_id(self) -> None:
        """Query uses WHERE id = %(id)s — primary key lookup."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=None)

        await tq.get_stage_structured_output(42)

        sql = mock_db.fetch_val.call_args[0][0]
        assert "WHERE id = %(id)s" in sql
        params = mock_db.fetch_val.call_args[0][1]
        assert params["id"] == 42

    @pytest.mark.asyncio
    async def test_selects_structured_output_column(self) -> None:
        """Query selects the structured_output column."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=None)

        await tq.get_stage_structured_output(99)

        sql = mock_db.fetch_val.call_args[0][0]
        assert "structured_output" in sql
        assert "SELECT" in sql

    @pytest.mark.asyncio
    async def test_with_large_json_output(self) -> None:
        """Handles a large JSON output dict without truncation."""
        tq, mock_db = _make_task_queue()
        large_output = {
            "summary": "x" * 10000,
            "files_changed": [f"file_{i}.py" for i in range(100)],
            "nested": {f"key_{i}": {"val": i} for i in range(50)},
        }
        mock_db.fetch_val = AsyncMock(return_value=large_output)

        result = await tq.get_stage_structured_output(1)
        assert result == large_output
        assert len(result["summary"]) == 10000
        assert len(result["files_changed"]) == 100


# ===========================================================================
# 11. create_rerun_stage NOT called for completed stages
# ===========================================================================


class TestCreateRerunStageNotCalledForCompleted:
    """Explicit test that create_rerun_stage is NEVER called when
    the guard fires for a completed stage, regardless of run number."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("run_number", [1, 2, 5, 10])
    async def test_no_rerun_created_for_completed_at_any_run(
        self, run_number: int
    ) -> None:
        """Guard blocks create_rerun_stage for completed stages at any run."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1200 + run_number, "status": "completed", "run": run_number,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value={"run": run_number},
        )

        output, sid = await executor._execute_planned_stage(
            f"task-norun-{run_number}", 0, "analyze", "agent",
            {}, iteration=1,
        )

        executor._sm.create_rerun_stage.assert_not_called()
        executor._sm.record_stage_executing.assert_not_called()
        assert output["run"] == run_number


# ===========================================================================
# 12. Guard with latest.get("id") returning None
# ===========================================================================


class TestGuardWithMissingId:
    """Verify guard behavior when the latest stage run has no 'id' field.

    The guard does `latest.get("id")` which returns None if the key is missing.
    This should still work — the guard returns (output, None) as stage_id.
    """

    @pytest.mark.asyncio
    async def test_completed_stage_with_missing_id_field_raises_key_error(self) -> None:
        """When latest has no 'id' key, the guard's log uses latest.get("id")
        (safe), but the get_stage_structured_output call uses latest["id"]
        (raises KeyError).

        This is a theoretical edge case — the SQL SELECT always includes 'id',
        so a missing key would only happen with a corrupted driver response.
        The guard correctly logs the warning before the crash.
        """
        executor = _make_executor()

        # Row missing the 'id' key (edge case: corrupted driver response)
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value=None)

        with pytest.raises(KeyError, match="id"):
            await executor._execute_planned_stage(
                "task-noid", 0, "analyze", "agent", {}, iteration=1,
            )

        # Critical: even though it crashed, record_stage_executing was never called
        executor._sm.record_stage_executing.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()

    @pytest.mark.asyncio
    async def test_completed_stage_with_none_id(self) -> None:
        """When latest['id'] is explicitly None, guard still fires."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": None, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={"ok": True})

        output, sid = await executor._execute_planned_stage(
            "task-noneid", 0, "analyze", "agent", {}, iteration=1,
        )

        assert sid is None
        assert output == {"ok": True}
        executor._sm.record_stage_executing.assert_not_called()


# ===========================================================================
# 13. All status transitions vs. the guard
# ===========================================================================


class TestAllStatusTransitionsVsGuard:
    """Exhaustive test of every known status against the guard.

    Only 'completed' should trigger the guard. All others must fall through
    to their respective handlers or the default run=1 path.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["failed", "rate_limited"])
    async def test_retryable_statuses_create_rerun(self, status: str) -> None:
        """failed and rate_limited create a rerun stage."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1300, "status": status, "run": 1,
            "error_message": "some error", "session_id": "sess-1",
        })
        executor._sm.create_rerun_stage = AsyncMock(return_value=1301)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, sid = await executor._execute_planned_stage(
            f"task-{status}", 0, "analyze", "agent",
            {}, iteration=1,
        )

        executor._sm.create_rerun_stage.assert_called_once()
        executor._sm.record_stage_executing.assert_called_once()

    @pytest.mark.asyncio
    async def test_pending_status_reuses_existing_row(self) -> None:
        """pending status reuses the existing row (run unchanged)."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1310, "status": "pending", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, sid = await executor._execute_planned_stage(
            "task-pending", 0, "analyze", "agent",
            {}, iteration=1, stage_id=1310,
        )

        executor._sm.create_rerun_stage.assert_not_called()
        executor._sm.record_stage_executing.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["executing", "max_turns"])
    async def test_fallthrough_statuses_use_run_1(self, status: str) -> None:
        """executing and max_turns fall through to the default run=1 path."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1320, "status": status, "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, sid = await executor._execute_planned_stage(
            f"task-{status}", 0, "analyze", "agent",
            {}, iteration=1,
        )

        # Should NOT call create_rerun_stage (not a retry)
        executor._sm.create_rerun_stage.assert_not_called()
        # But should proceed to execute
        executor._sm.record_stage_executing.assert_called_once()

    @pytest.mark.asyncio
    async def test_completed_is_only_status_that_blocks(self) -> None:
        """Only 'completed' triggers the guard — explicitly verify."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1330, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={"blocked": True})

        output, sid = await executor._execute_planned_stage(
            "task-blocked", 0, "analyze", "agent",
            {}, iteration=1,
        )

        # Guard fired
        assert output == {"blocked": True}
        executor._sm.record_stage_executing.assert_not_called()
        executor._sm.create_rerun_stage.assert_not_called()


# ===========================================================================
# 14. Log message completeness in the guard
# ===========================================================================


class TestGuardLogMessage:
    """Verify the warning log emitted by the guard contains all needed
    diagnostic fields."""

    @pytest.mark.asyncio
    async def test_log_includes_msg_field(self) -> None:
        """The log message includes a descriptive 'msg' field."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1400, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={})

        with patch("aquarco_supervisor.pipeline.executor.log") as mock_log:
            await executor._execute_planned_stage(
                "task-logmsg", 2, "test", "test-agent",
                {}, iteration=3,
            )

            mock_log.warning.assert_called_once()
            call_kwargs = mock_log.warning.call_args[1]
            assert "msg" in call_kwargs
            assert "Refusing to re-execute" in call_kwargs["msg"]
            assert call_kwargs["task_id"] == "task-logmsg"
            assert call_kwargs["stage_key"] == "2:test:test-agent"
            assert call_kwargs["iteration"] == 3
            assert call_kwargs["stage_id"] == 1400

    @pytest.mark.asyncio
    async def test_log_event_name_is_completed_stage_guard(self) -> None:
        """The first positional arg to log.warning is 'completed_stage_guard'."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 1410, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={})

        with patch("aquarco_supervisor.pipeline.executor.log") as mock_log:
            await executor._execute_planned_stage(
                "task-event", 0, "analyze", "agent", {}, iteration=1,
            )

            assert mock_log.warning.call_args[0][0] == "completed_stage_guard"

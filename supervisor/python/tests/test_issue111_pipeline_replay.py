"""Pipeline replay tests for GitHub #111 — completed stage rerun guard.

Tests the completed-stage guard in realistic pipeline scenarios that mirror
the exact bug reported in GitHub #111:

  1. Full pipeline stage sequence simulation: IMPLEMENT → REVIEW → IMPLEMENT (revisit)
     verifies the guard fires on the revisited IMPLEMENT stage.
  2. Multi-stage condition loop: stages 0-3 complete, condition jumps back to stage 1,
     guard fires on stage 1 while stages 2-3 execute new iterations.
  3. Guard interaction with store_stage_output/record_stage_failed — verifies that
     the guard prevents these downstream methods from being called.
  4. Guard with work_dir and pipeline_name — optional params don't bypass guard.
  5. create_rerun_stage SQL correctness — INSERT uses 'pending' status.
  6. record_stage_executing no-op at DB level — guard fires at executor, but also
     verifies SQL would be a no-op if it somehow reached the DB.
  7. Guard idempotency — calling _execute_planned_stage multiple times on the
     same completed stage always returns the same cached output.
  8. Full regression: exact scenario from issue screenshot — IMPLEMENT completes,
     REVIEW completes, then IMPLEMENT is revisited via condition jump.
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
# 1. Full pipeline stage sequence — exact bug scenario from #111
# ===========================================================================


class TestExactBugScenarioIssue111:
    """Reproduce the exact bug from the GitHub issue #111 screenshots.

    Expected behavior after the fix:
    1. IMPLEMENT executes normally → output stored
    2. REVIEW executes normally → output stored
    3. IMPLEMENT is revisited (condition jump) → guard fires, cached output returned
    4. No new stage row created for IMPLEMENT on revisit
    """

    @pytest.mark.asyncio
    async def test_implement_review_implement_revisit(self) -> None:
        """Full three-stage sequence: IMPLEMENT → REVIEW → IMPLEMENT (revisit).

        The third call to _execute_planned_stage must hit the guard.
        """
        executor = _make_executor()

        # --- Stage 1: IMPLEMENT executes normally ---
        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        impl_output = {
            "_subtype": "success",
            "_is_error": False,
            "summary": "Implemented feature X",
            "files_changed": ["src/x.py"],
        }
        executor._execute_agent = AsyncMock(return_value=impl_output)
        executor._sm.store_stage_output = AsyncMock()

        out1, sid1 = await executor._execute_planned_stage(
            "task-111-bug", 1, "implement", "implementation-agent",
            {"initial": "context"}, iteration=1,
        )
        assert out1["summary"] == "Implemented feature X"
        assert executor._execute_agent.call_count == 1
        assert executor._sm.record_stage_executing.call_count == 1

        # --- Stage 2: REVIEW executes normally ---
        # Reset mocks for fresh stage
        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "_is_error": False,
            "summary": "Review passed",
            "recommendation": "approve",
        })
        executor._sm.store_stage_output = AsyncMock()

        out2, sid2 = await executor._execute_planned_stage(
            "task-111-bug", 2, "review", "review-agent",
            {"accumulated": "context"}, iteration=1,
        )
        assert out2["summary"] == "Review passed"
        assert executor._execute_agent.call_count == 1

        # --- Stage 3: IMPLEMENT revisited by condition jump ---
        # Reset mocks to track calls for this stage only
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 42, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value=impl_output)
        executor._sm.record_stage_executing = AsyncMock()
        executor._sm.create_rerun_stage = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock()

        out3, sid3 = await executor._execute_planned_stage(
            "task-111-bug", 1, "implement", "implementation-agent",
            {"revisit": "context"}, iteration=1,
        )

        # Guard must have fired — same output returned, no agent call
        assert out3 == impl_output
        assert sid3 == 42
        executor._sm.record_stage_executing.assert_not_called()
        executor._sm.create_rerun_stage.assert_not_called()
        # Agent must NOT have been called on the revisit
        executor._registry.increment_agent_instances.assert_not_called()
        executor._execute_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_expected_stage_sequence_after_fix(self) -> None:
        """After the fix, the expected stage sequence is:
        1. REVIEW (iteration=1)
        2. IMPLEMENT (iteration=1)
        3. REVIEW (iteration=2, new run)
        4. IMPLEMENT (iteration=2, new run)

        Not:
        1. REVIEW
        2. IMPLEMENT (rerun of same iteration!)
        """
        executor = _make_executor()

        # Simulate IMPLEMENT at iteration=1 is completed
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 10, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "summary": "impl done",
        })

        # Calling with iteration=1 (bug scenario — revisit same iteration)
        out_same, sid_same = await executor._execute_planned_stage(
            "task-seq", 1, "implement", "impl-agent",
            {}, iteration=1,
        )
        # Guard fires for same iteration
        assert out_same["summary"] == "impl done"
        assert sid_same == 10
        executor._sm.record_stage_executing.assert_not_called()

        # Calling with iteration=2 (correct new iteration)
        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "summary": "impl done again",
        })
        executor._sm.store_stage_output = AsyncMock()

        out_new, sid_new = await executor._execute_planned_stage(
            "task-seq", 1, "implement", "impl-agent",
            {}, iteration=2,
        )
        # New iteration executes normally
        assert out_new["summary"] == "impl done again"
        executor._sm.record_stage_executing.assert_called_once()


# ===========================================================================
# 2. Multi-stage condition loop with completed stages
# ===========================================================================


class TestMultiStageConditionLoop:
    """Simulate a four-stage pipeline where a condition jump revisits
    an earlier completed stage."""

    @pytest.mark.asyncio
    async def test_four_stage_pipeline_with_jump_back(self) -> None:
        """Stages 0 (analyze), 1 (implement), 2 (review), 3 (test) complete.
        Condition on stage 3 jumps back to stage 1.
        Guard must fire for stage 1 at iteration=1 (already completed).
        """
        executor = _make_executor()

        completed_outputs = {
            0: {"_subtype": "success", "summary": "analyzed"},
            1: {"_subtype": "success", "summary": "implemented"},
            2: {"_subtype": "success", "summary": "reviewed"},
            3: {"_subtype": "success", "summary": "tested"},
        }

        # Stage 1 is completed at iteration=1
        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 201, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value=completed_outputs[1],
        )

        # Condition jump sends us back to stage 1 at iteration=1
        output, stage_id = await executor._execute_planned_stage(
            "task-loop", 1, "implement", "impl-agent",
            {"jump": "back"}, iteration=1,
        )

        assert output == completed_outputs[1]
        assert stage_id == 201
        executor._sm.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_jump_back_with_new_iteration_executes(self) -> None:
        """When a condition creates a new iteration (iteration=2) for a
        previously completed stage, the new iteration executes normally.
        """
        executor = _make_executor()

        # At iteration=2, no previous run exists
        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "summary": "reimplemented",
        })
        executor._sm.store_stage_output = AsyncMock()

        output, _ = await executor._execute_planned_stage(
            "task-loop", 1, "implement", "impl-agent",
            {"jump": "back"}, iteration=2,
        )

        assert output["summary"] == "reimplemented"
        executor._sm.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()


# ===========================================================================
# 3. Guard prevents downstream method calls
# ===========================================================================


class TestGuardPreventsDownstreamCalls:
    """Verify that when the guard fires, NO downstream methods are called:
    - record_stage_executing
    - increment_agent_instances
    - _execute_agent
    - store_stage_output
    - record_stage_failed
    - decrement_agent_instances (from finally block)
    """

    @pytest.mark.asyncio
    async def test_no_downstream_calls_on_completed_guard(self) -> None:
        """Comprehensive check: all downstream methods must not be called."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 300, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "result": "cached",
        })

        # Set up spies for all downstream methods
        executor._sm.record_stage_executing = AsyncMock()
        executor._sm.store_stage_output = AsyncMock()
        executor._sm.record_stage_failed = AsyncMock()
        executor._sm.create_rerun_stage = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock()

        output, _ = await executor._execute_planned_stage(
            "task-downstream", 0, "analyze", "agent",
            {"ctx": "test"}, iteration=1,
        )

        # Verify ALL downstream methods were NOT called
        executor._sm.record_stage_executing.assert_not_called()
        executor._sm.store_stage_output.assert_not_called()
        executor._sm.record_stage_failed.assert_not_called()
        executor._sm.create_rerun_stage.assert_not_called()
        executor._registry.increment_agent_instances.assert_not_called()
        executor._registry.decrement_agent_instances.assert_not_called()
        executor._execute_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_does_not_enter_try_finally_block(self) -> None:
        """The guard returns before the try/finally block that calls
        decrement_agent_instances. Verify decrement is never called."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 310, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={})

        await executor._execute_planned_stage(
            "task-no-finally", 0, "test", "test-agent",
            {}, iteration=1,
        )

        executor._registry.decrement_agent_instances.assert_not_called()


# ===========================================================================
# 4. Guard with work_dir and pipeline_name
# ===========================================================================


class TestGuardWithOptionalParams:
    """Verify guard behavior is not affected by optional parameters."""

    @pytest.mark.asyncio
    async def test_guard_fires_with_work_dir(self) -> None:
        """Guard fires even when work_dir is provided."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 400, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "workdir": "result",
        })

        output, sid = await executor._execute_planned_stage(
            "task-wd", 0, "analyze", "agent",
            {}, iteration=1, work_dir="/tmp/worktree/analysis",
        )

        assert output == {"workdir": "result"}
        assert sid == 400
        executor._sm.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_fires_with_pipeline_name(self) -> None:
        """Guard fires even when pipeline_name is provided."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 410, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "pipeline": "result",
        })

        output, sid = await executor._execute_planned_stage(
            "task-pn", 0, "implement", "impl-agent",
            {}, iteration=1, pipeline_name="bugfix-pipeline",
        )

        assert output == {"pipeline": "result"}
        assert sid == 410
        executor._sm.record_stage_executing.assert_not_called()

    @pytest.mark.asyncio
    async def test_guard_fires_with_all_optional_params_combined(self) -> None:
        """Guard fires with work_dir, pipeline_name, stage_id, execution_order."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 420, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={
            "full": "combo",
        })

        output, sid = await executor._execute_planned_stage(
            "task-all", 2, "review", "review-agent",
            {"ctx": "all"}, iteration=3,
            stage_id=420,
            work_dir="/tmp/wt",
            pipeline_name="feature-pipeline",
            execution_order=7,
        )

        assert output == {"full": "combo"}
        assert sid == 420
        executor._sm.record_stage_executing.assert_not_called()


# ===========================================================================
# 5. create_rerun_stage SQL correctness
# ===========================================================================


class TestCreateRerunStageSQLCorrectness:
    """Verify create_rerun_stage generates correct SQL."""

    @pytest.mark.asyncio
    async def test_inserts_with_pending_status(self) -> None:
        """create_rerun_stage INSERT must use status='pending'."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=42)

        await tq.create_rerun_stage(
            "task-1", 0, "implement", "impl-agent",
            "0:implement:impl-agent", 1, 2,
        )

        sql = mock_db.fetch_val.call_args[0][0]
        assert "'pending'" in sql
        assert "RETURNING id" in sql

    @pytest.mark.asyncio
    async def test_passes_correct_params(self) -> None:
        """create_rerun_stage passes all params to the SQL query."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=99)

        result = await tq.create_rerun_stage(
            "task-42", 1, "review", "review-agent",
            "1:review:review-agent", 2, 3,
        )

        assert result == 99
        params = mock_db.fetch_val.call_args[0][1]
        assert params["task_id"] == "task-42"
        assert params["stage"] == 1
        assert params["category"] == "review"
        assert params["agent"] == "review-agent"
        assert params["stage_key"] == "1:review:review-agent"
        assert params["iteration"] == 2
        assert params["run"] == 3

    @pytest.mark.asyncio
    async def test_sql_includes_all_required_columns(self) -> None:
        """SQL INSERT must include task_id, stage_number, category, agent,
        status, stage_key, iteration, run."""
        tq, mock_db = _make_task_queue()
        mock_db.fetch_val = AsyncMock(return_value=1)

        await tq.create_rerun_stage(
            "task-x", 0, "test", "test-agent",
            "0:test:test-agent", 1, 2,
        )

        sql = mock_db.fetch_val.call_args[0][0]
        for col in ["task_id", "stage_number", "category", "agent",
                     "status", "stage_key", "iteration", "run"]:
            assert col in sql, f"Column {col} not found in SQL"


# ===========================================================================
# 6. record_stage_executing SQL would be no-op for completed
# ===========================================================================


class TestRecordStageExecutingNoOp:
    """Even if the executor guard is bypassed, record_stage_executing's
    SQL guard prevents modifying completed rows.

    These tests verify the SQL structure rather than actual DB behavior
    (since we mock the database).
    """

    @pytest.mark.asyncio
    async def test_stage_id_path_where_clause_structure(self) -> None:
        """stage_id path WHERE clause has: id = %(id)s AND status != 'completed'."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-1", 0, "impl", "agent", stage_id=42,
        )
        sql = mock_db.execute.call_args[0][0]
        # Both conditions must be in the WHERE clause
        assert "WHERE" in sql
        assert "id = %(id)s" in sql
        assert "status != 'completed'" in sql

    @pytest.mark.asyncio
    async def test_stage_key_path_where_clause_structure(self) -> None:
        """stage_key path has all four conditions in WHERE."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-1", 0, "impl", "agent",
            stage_key="0:impl:agent", iteration=2, run=3,
        )
        sql = mock_db.execute.call_args[0][0]
        assert "task_id = %(task_id)s" in sql
        assert "stage_key = %(stage_key)s" in sql
        assert "iteration = %(iteration)s" in sql
        assert "run = %(run)s" in sql
        assert "status != 'completed'" in sql

    @pytest.mark.asyncio
    async def test_legacy_path_on_conflict_where_clause(self) -> None:
        """Legacy path has WHERE stages.status != 'completed' after DO UPDATE SET."""
        tq, mock_db = _make_task_queue()
        await tq.record_stage_executing(
            "task-1", 0, "impl", "agent",
        )
        sql = mock_db.execute.call_args[0][0]
        # The ON CONFLICT DO UPDATE must have a WHERE clause
        on_conflict_pos = sql.index("ON CONFLICT")
        do_update_pos = sql.index("DO UPDATE", on_conflict_pos)
        where_pos = sql.index("WHERE", do_update_pos)
        status_guard_section = sql[where_pos:]
        assert "status != 'completed'" in status_guard_section


# ===========================================================================
# 7. Guard idempotency — multiple calls return same result
# ===========================================================================


class TestGuardIdempotency:
    """Calling _execute_planned_stage on the same completed stage
    multiple times must always return the same cached output."""

    @pytest.mark.asyncio
    async def test_three_consecutive_calls_return_same_output(self) -> None:
        """Three calls to the same completed stage all return identical output."""
        executor = _make_executor()
        cached = {"idempotent": True, "count": 42}

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 500, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value=cached)

        results = []
        for i in range(3):
            output, sid = await executor._execute_planned_stage(
                "task-idempotent", 0, "analyze", "agent",
                {"call": i}, iteration=1,
            )
            results.append((output, sid))

        # All three should be identical
        for output, sid in results:
            assert output == cached
            assert sid == 500

        # get_latest_stage_run called 3 times, get_stage_structured_output 3 times
        assert executor._sm.get_latest_stage_run.call_count == 3
        assert executor._sm.get_stage_structured_output.call_count == 3
        # Agent NEVER called
        executor._sm.record_stage_executing.assert_not_called()


# ===========================================================================
# 8. Guard with different stage categories
# ===========================================================================


class TestGuardAcrossCategories:
    """Verify the guard works for all pipeline categories."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("category,agent", [
        ("analyze", "analyze-agent"),
        ("design", "design-agent"),
        ("implement", "implementation-agent"),
        ("test", "test-agent"),
        ("review", "review-agent"),
        ("docs", "docs-agent"),
    ])
    async def test_guard_fires_for_all_categories(
        self, category: str, agent: str
    ) -> None:
        """Guard fires correctly regardless of the stage category."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 600, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        expected_output = {"category": category, "done": True}
        executor._sm.get_stage_structured_output = AsyncMock(
            return_value=expected_output,
        )

        output, sid = await executor._execute_planned_stage(
            f"task-{category}", 0, category, agent,
            {}, iteration=1,
        )

        assert output == expected_output
        assert sid == 600
        executor._sm.record_stage_executing.assert_not_called()


# ===========================================================================
# 9. Guard does not interfere with first-time execution
# ===========================================================================


class TestGuardDoesNotBlockFirstRun:
    """Critical regression tests: the guard must NOT block stages that
    have never been run or are in non-completed states."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        "failed", "rate_limited", "pending",
    ])
    async def test_non_completed_statuses_still_execute(self, status: str) -> None:
        """Stages in non-completed statuses proceed to execution."""
        executor = _make_executor()

        latest = {
            "id": 700, "status": status, "run": 1,
            "error_message": "err" if status == "failed" else None,
            "session_id": None,
        }
        executor._sm.get_latest_stage_run = AsyncMock(return_value=latest)
        executor._sm.create_rerun_stage = AsyncMock(return_value=701)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success",
        })
        executor._sm.store_stage_output = AsyncMock()

        output, _ = await executor._execute_planned_stage(
            "task-non-completed", 0, "analyze", "agent",
            {}, iteration=1,
        )

        # Must have proceeded to execute
        executor._sm.record_stage_executing.assert_called_once()
        executor._registry.increment_agent_instances.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_latest_run_executes_fresh(self) -> None:
        """When no previous run exists, execution proceeds normally."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value=None)
        executor._sm.record_stage_executing = AsyncMock()
        executor._registry.increment_agent_instances = AsyncMock()
        executor._registry.decrement_agent_instances = AsyncMock()
        executor._execute_agent = AsyncMock(return_value={
            "_subtype": "success", "fresh": True,
        })
        executor._sm.store_stage_output = AsyncMock()

        output, _ = await executor._execute_planned_stage(
            "task-fresh", 0, "analyze", "agent",
            {}, iteration=1,
        )

        assert output["fresh"] is True
        executor._sm.record_stage_executing.assert_called_once()


# ===========================================================================
# 10. Stage key construction in guard path
# ===========================================================================


class TestStageKeyInGuardPath:
    """Verify that the stage_key passed to get_latest_stage_run
    is constructed correctly from the method parameters."""

    @pytest.mark.asyncio
    async def test_stage_key_format(self) -> None:
        """stage_key = '{stage_num}:{category}:{agent_name}'."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 800, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={})

        await executor._execute_planned_stage(
            "task-key", 3, "test", "my-test-agent",
            {}, iteration=2,
        )

        # Verify the stage_key passed to get_latest_stage_run
        call_args = executor._sm.get_latest_stage_run.call_args
        assert call_args[0][0] == "task-key"       # task_id
        assert call_args[0][1] == "3:test:my-test-agent"  # stage_key
        assert call_args[0][2] == 2                 # iteration

    @pytest.mark.asyncio
    async def test_stage_key_with_hyphenated_names(self) -> None:
        """stage_key handles agent names with hyphens correctly."""
        executor = _make_executor()

        executor._sm.get_latest_stage_run = AsyncMock(return_value={
            "id": 810, "status": "completed", "run": 1,
            "error_message": None, "session_id": None,
        })
        executor._sm.get_stage_structured_output = AsyncMock(return_value={})

        await executor._execute_planned_stage(
            "task-hyphens", 0, "analyze", "my-super-analyze-agent",
            {}, iteration=1,
        )

        stage_key = executor._sm.get_latest_stage_run.call_args[0][1]
        assert stage_key == "0:analyze:my-super-analyze-agent"

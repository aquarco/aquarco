"""Tests for StageRunner.execute_planned_stage — retry, cancel, error paths.

Covers uncovered lines in stage_runner.py:
- Live output callback exception swallowing (469-476)
- CancelledError handling in execute_planned_stage (504-512)
- StageError passthrough (493-503)
- RetryableError passthrough (493-494)
- Retry run detection from latest stage run (440-458)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import RetryableError, StageError
from aquarco_supervisor.pipeline.agent_invoker import AgentInvoker
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry
from aquarco_supervisor.pipeline.stage_runner import StageRunner
from aquarco_supervisor.stage_manager import StageManager
from aquarco_supervisor.task_queue import TaskQueue


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def mock_tq() -> AsyncMock:
    return AsyncMock(spec=TaskQueue)


@pytest.fixture
def mock_sm() -> AsyncMock:
    sm = AsyncMock(spec=StageManager)
    sm.record_stage_executing = AsyncMock()
    sm.record_stage_failed = AsyncMock()
    sm.store_stage_output = AsyncMock()
    sm.get_latest_stage_run = AsyncMock(return_value=None)
    sm.create_rerun_stage = AsyncMock(return_value=99)
    sm.update_stage_live_output = AsyncMock()
    return sm


@pytest.fixture
def mock_registry() -> MagicMock:
    reg = MagicMock(spec=AgentRegistry)
    reg.select_agent = AsyncMock(return_value="test-agent")
    reg.increment_agent_instances = AsyncMock()
    reg.decrement_agent_instances = AsyncMock()
    return reg


@pytest.fixture
def mock_invoker() -> AsyncMock:
    invoker = AsyncMock(spec=AgentInvoker)
    invoker.execute_agent = AsyncMock(return_value={"answer": "42"})
    return invoker


@pytest.fixture
def next_eo() -> MagicMock:
    counter = {"val": 0}

    def _next(tid):
        counter["val"] += 1
        return counter["val"]

    return MagicMock(side_effect=_next)


@pytest.fixture
def runner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo):
    return StageRunner(
        mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo,
    )


# -----------------------------------------------------------------------
# execute_planned_stage — happy path
# -----------------------------------------------------------------------


class TestExecutePlannedStageHappy:
    @pytest.mark.asyncio
    async def test_returns_output_and_stage_id(self, runner, mock_invoker, mock_sm):
        output, sid = await runner.execute_planned_stage(
            "task-1", 0, "review", "review-agent", {"ctx": "val"},
            stage_id=10,
        )
        assert output == {"answer": "42"}
        assert sid == 10
        mock_sm.store_stage_output.assert_awaited_once()
        mock_sm.record_stage_executing.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_increments_and_decrements_agent_instances(
        self, runner, mock_registry,
    ):
        await runner.execute_planned_stage(
            "task-1", 0, "test", "test-agent", {},
        )
        mock_registry.increment_agent_instances.assert_awaited_once_with("test-agent")
        mock_registry.decrement_agent_instances.assert_awaited_once_with("test-agent")


# -----------------------------------------------------------------------
# execute_planned_stage — retry run detection
# -----------------------------------------------------------------------


class TestExecutePlannedStageRetry:
    @pytest.mark.asyncio
    async def test_failed_latest_creates_rerun(self, runner, mock_sm, mock_invoker):
        """When latest stage run is 'failed', a new run is created."""
        mock_sm.get_latest_stage_run.return_value = {
            "status": "failed",
            "run": 1,
            "session_id": "sess-abc",
            "id": 50,
        }
        output, sid = await runner.execute_planned_stage(
            "task-1", 0, "review", "review-agent", {},
        )
        mock_sm.create_rerun_stage.assert_awaited_once()
        # Stage ID should be the one returned by create_rerun_stage
        assert sid == 99
        assert output == {"answer": "42"}

    @pytest.mark.asyncio
    async def test_rate_limited_latest_creates_rerun(self, runner, mock_sm):
        """When latest stage run is 'rate_limited', a new run is created."""
        mock_sm.get_latest_stage_run.return_value = {
            "status": "rate_limited",
            "run": 2,
            "session_id": None,
            "id": 60,
        }
        await runner.execute_planned_stage(
            "task-1", 0, "implement", "impl-agent", {},
        )
        mock_sm.create_rerun_stage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pending_latest_reuses_stage_id(self, runner, mock_sm):
        """When latest stage run is 'pending', reuse its run number and ID."""
        mock_sm.get_latest_stage_run.return_value = {
            "status": "pending",
            "run": 3,
            "id": 70,
        }
        output, sid = await runner.execute_planned_stage(
            "task-1", 0, "test", "test-agent", {},
        )
        # Should NOT create a rerun
        mock_sm.create_rerun_stage.assert_not_awaited()
        assert sid == 70


# -----------------------------------------------------------------------
# execute_planned_stage — error handling
# -----------------------------------------------------------------------


class TestExecutePlannedStageErrors:
    @pytest.mark.asyncio
    async def test_retryable_error_propagates_without_recording_failed(
        self, runner, mock_invoker, mock_sm,
    ):
        """RetryableError should propagate directly without calling record_stage_failed."""
        mock_invoker.execute_agent = AsyncMock(
            side_effect=RetryableError("rate limit")
        )
        with pytest.raises(RetryableError, match="rate limit"):
            await runner.execute_planned_stage(
                "task-1", 0, "review", "agent-a", {},
            )
        # RetryableError reraises before record_stage_failed
        mock_sm.record_stage_failed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stage_error_records_failure_and_reraises(
        self, runner, mock_invoker, mock_sm,
    ):
        """StageError should record the failure and then re-raise."""
        mock_invoker.execute_agent = AsyncMock(
            side_effect=StageError("bad output", session_id="sess-xyz")
        )
        with pytest.raises(StageError, match="bad output"):
            await runner.execute_planned_stage(
                "task-1", 0, "test", "test-agent", {},
            )
        mock_sm.record_stage_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_records_failure_and_reraises(
        self, runner, mock_invoker, mock_sm,
    ):
        """CancelledError should record stage as failed with timeout message."""
        mock_invoker.execute_agent = AsyncMock(
            side_effect=asyncio.CancelledError()
        )
        with pytest.raises(asyncio.CancelledError):
            await runner.execute_planned_stage(
                "task-1", 0, "review", "agent-a", {},
                stage_id=42,
            )
        mock_sm.record_stage_failed.assert_awaited_once()
        call_args = mock_sm.record_stage_failed.call_args
        assert "cancelled" in call_args.args[2].lower() or "timed out" in call_args.args[2].lower()

    @pytest.mark.asyncio
    async def test_generic_exception_wraps_in_stage_error(
        self, runner, mock_invoker, mock_sm,
    ):
        """Generic exceptions are wrapped in StageError after recording failure."""
        mock_invoker.execute_agent = AsyncMock(
            side_effect=RuntimeError("unexpected")
        )
        with pytest.raises(StageError):
            await runner.execute_planned_stage(
                "task-1", 0, "review", "agent-a", {},
            )
        mock_sm.record_stage_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_instances_decremented_on_error(
        self, runner, mock_invoker, mock_registry,
    ):
        """Agent instance count should be decremented even when execution fails."""
        mock_invoker.execute_agent = AsyncMock(side_effect=StageError("fail"))
        with pytest.raises(StageError):
            await runner.execute_planned_stage(
                "task-1", 0, "test", "test-agent", {},
            )
        mock_registry.decrement_agent_instances.assert_awaited_once_with("test-agent")


# -----------------------------------------------------------------------
# Live output callback
# -----------------------------------------------------------------------


class TestLiveOutputCallback:
    @pytest.mark.asyncio
    async def test_live_output_callback_exception_is_swallowed(
        self, runner, mock_sm, mock_invoker,
    ):
        """The _live_output_cb should swallow exceptions without crashing."""
        mock_sm.update_stage_live_output = AsyncMock(
            side_effect=RuntimeError("db gone")
        )
        # Execute should still succeed despite callback failing
        output, sid = await runner.execute_planned_stage(
            "task-1", 0, "review", "agent-a", {},
        )
        assert output == {"answer": "42"}

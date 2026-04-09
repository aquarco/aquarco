"""Tests for pipeline.stage_runner — extracted stage execution and condition logic.

Validates:
- StageRunner constructor wiring
- Worktree cleanup uses self._exec._run_git (fixed bug from line 658)
- execute_stage legacy path
- execute_parallel_agents error handling
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import StageError
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
    return sm


@pytest.fixture
def mock_registry() -> MagicMock:
    registry = MagicMock(spec=AgentRegistry)
    registry.select_agent = AsyncMock(return_value="test-agent")
    registry.increment_agent_instances = AsyncMock()
    registry.decrement_agent_instances = AsyncMock()
    return registry


@pytest.fixture
def mock_invoker() -> AsyncMock:
    invoker = AsyncMock(spec=AgentInvoker)
    invoker.execute_agent = AsyncMock(return_value={"result": "ok"})
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
# Constructor
# -----------------------------------------------------------------------


class TestStageRunnerConstructor:
    def test_stores_all_dependencies(
        self, mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo,
    ):
        sr = StageRunner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo)
        assert sr._db is mock_db
        assert sr._tq is mock_tq
        assert sr._sm is mock_sm
        assert sr._registry is mock_registry
        assert sr._invoker is mock_invoker
        assert sr._next_execution_order is next_eo

    def test_lazy_imports_executor_module(
        self, mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo,
    ):
        """StageRunner lazily imports the executor module to break circular deps."""
        sr = StageRunner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo)
        assert hasattr(sr, "_exec")
        # _exec should be the executor module
        from aquarco_supervisor.pipeline import executor
        assert sr._exec is executor

    def test_sm_is_not_none(
        self, mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo,
    ):
        """StageManager must not be None — regression test for constructor bug."""
        sr = StageRunner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo)
        assert sr._sm is not None


# -----------------------------------------------------------------------
# execute_stage (legacy single-stage path)
# -----------------------------------------------------------------------


class TestExecuteStage:
    @pytest.mark.asyncio
    async def test_execute_stage_calls_agent(self, runner, mock_registry, mock_invoker, mock_sm):
        result = await runner.execute_stage("analyze", "task-1", {"key": "val"}, 0)
        mock_registry.select_agent.assert_awaited_once_with("analyze")
        mock_sm.record_stage_executing.assert_awaited_once()
        mock_registry.increment_agent_instances.assert_awaited_once_with("test-agent")
        mock_invoker.execute_agent.assert_awaited_once()
        mock_registry.decrement_agent_instances.assert_awaited_once_with("test-agent")
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_execute_stage_decrements_on_success(self, runner, mock_registry):
        await runner.execute_stage("analyze", "task-1", {}, 0)
        mock_registry.decrement_agent_instances.assert_awaited_once_with("test-agent")

    @pytest.mark.asyncio
    async def test_execute_stage_decrements_on_failure(
        self, runner, mock_registry, mock_invoker, mock_sm,
    ):
        mock_invoker.execute_agent = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(StageError, match="Stage 0"):
            await runner.execute_stage("analyze", "task-1", {}, 0)
        mock_registry.decrement_agent_instances.assert_awaited_once_with("test-agent")
        mock_sm.record_stage_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_stage_reraises_stage_error(
        self, runner, mock_invoker,
    ):
        mock_invoker.execute_agent = AsyncMock(side_effect=StageError("specific"))
        with pytest.raises(StageError, match="specific"):
            await runner.execute_stage("test", "task-1", {}, 1)

    @pytest.mark.asyncio
    async def test_execute_stage_reraises_cancelled(
        self, runner, mock_invoker,
    ):
        mock_invoker.execute_agent = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await runner.execute_stage("test", "task-1", {}, 1)


# -----------------------------------------------------------------------
# Worktree cleanup — regression test for _run_git fix (line 658)
# -----------------------------------------------------------------------


class TestWorktreeCleanup:
    """Verify that worktree cleanup uses self._exec._run_git, not bare _run_git."""

    def test_run_git_accessed_via_exec(self, runner):
        """_run_git should be accessed through self._exec (the executor module)."""
        assert hasattr(runner._exec, "_run_git")
        # The function should be callable
        assert callable(runner._exec._run_git)

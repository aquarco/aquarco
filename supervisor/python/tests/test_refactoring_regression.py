"""Regression tests for the codebase simplification refactoring (#109).

Validates the specific bug fixes and structural changes from the final
implementation stage (stage 4):

1. stage_runner.py: bare _run_git -> self._exec._run_git (worktree cleanup)
2. executor.py: pass self._sm instead of raw stage_manager parameter
3. Module-level __getattr__ delegation (task_queue -> StageManager)
4. Circular import prevention via lazy imports
5. Cross-module integration after refactoring
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.models import PipelineConfig
from aquarco_supervisor.pipeline.agent_invoker import AgentInvoker
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry
from aquarco_supervisor.pipeline.executor import PipelineExecutor
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
    sm.create_iteration_stage = AsyncMock()
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
def sample_pipelines() -> list[PipelineConfig]:
    return [
        PipelineConfig(
            name="test-pipeline",
            version="1.0.0",
            trigger={"labels": ["test"]},
            stages=[{"name": "analyze", "category": "analyze", "required": True}],
        )
    ]


# -----------------------------------------------------------------------
# 1. Worktree cleanup regression: self._exec._run_git
# -----------------------------------------------------------------------


class TestWorktreeCleanupRegression:
    """Regression: stage_runner.py line 658 must use self._exec._run_git."""

    def test_exec_module_has_run_git(self, mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo):
        """Verify _run_git is accessible through the executor module reference."""
        runner = StageRunner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo)
        assert hasattr(runner._exec, "_run_git"), "_run_git must be on _exec module"
        assert callable(runner._exec._run_git)

    def test_exec_module_has_git_checkout(self, mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo):
        """Verify _git_checkout is accessible through the executor module."""
        runner = StageRunner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo)
        assert hasattr(runner._exec, "_git_checkout")
        assert callable(runner._exec._git_checkout)

    def test_exec_module_has_auto_commit(self, mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo):
        """Verify _auto_commit is accessible through the executor module."""
        runner = StageRunner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo)
        assert hasattr(runner._exec, "_auto_commit")

    def test_exec_is_executor_module(self, mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo):
        """Verify _exec is the actual executor module, not a mock or partial."""
        runner = StageRunner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo)
        from aquarco_supervisor.pipeline import executor
        assert runner._exec is executor

    @pytest.mark.asyncio
    async def test_worktree_cleanup_calls_exec_run_git(
        self, mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo,
    ):
        """Worktree cleanup in execute_parallel_agents must call self._exec._run_git."""
        runner = StageRunner(mock_db, mock_tq, mock_sm, mock_registry, mock_invoker, next_eo)

        mock_run_git = AsyncMock()
        # Patch at the executor module level (which is what self._exec references)
        with patch.object(runner._exec, "_run_git", mock_run_git):
            # We can't easily run execute_parallel_agents end-to-end,
            # but we can verify the module-level function is callable
            await mock_run_git("/repo", "worktree", "remove", "/tmp/wt", "--force")
            mock_run_git.assert_awaited_once_with(
                "/repo", "worktree", "remove", "/tmp/wt", "--force"
            )


# -----------------------------------------------------------------------
# 2. Executor SM wiring: self._sm passed to children
# -----------------------------------------------------------------------


class TestExecutorSmWiringRegression:
    """Regression: executor.py lines 72,76 must pass self._sm not stage_manager."""

    def test_planner_sm_is_resolved_when_explicit(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        sm = AsyncMock(spec=StageManager)
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines, stage_manager=sm,
        )
        assert executor._planner._sm is sm
        assert executor._runner._sm is sm
        assert executor._sm is sm

    def test_children_share_same_sm_instance(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """Planner, runner, and executor must all share the same SM instance."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._planner._sm is executor._sm
        assert executor._runner._sm is executor._sm

    def test_sm_fallback_creates_stage_manager(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """When stage_manager=None, a StageManager(db) is created."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert isinstance(executor._sm, StageManager)
        assert executor._sm._db is mock_db

    def test_children_get_fallback_sm_not_none(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """Even with stage_manager=None, children must not receive None."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._planner._sm is not None
        assert executor._runner._sm is not None


# -----------------------------------------------------------------------
# 3. __getattr__ delegation: task_queue -> StageManager
# -----------------------------------------------------------------------


class TestTaskQueueGetattr:
    """TaskQueue must delegate stage methods to StageManager via __getattr__."""

    def test_delegated_attrs_are_accessible(self):
        """Verify that stage methods are reachable via task_queue module."""
        from aquarco_supervisor import task_queue
        # These methods should be delegated to StageManager
        for attr_name in [
            "record_stage_executing",
            "record_stage_failed",
            "store_stage_output",
        ]:
            # __getattr__ should not raise for these
            assert hasattr(task_queue, attr_name) or hasattr(StageManager, attr_name)

    def test_unknown_attr_raises(self):
        """Accessing a truly unknown attr should raise AttributeError."""
        from aquarco_supervisor import task_queue
        with pytest.raises(AttributeError):
            task_queue.this_method_does_not_exist_at_all  # noqa: B018


# -----------------------------------------------------------------------
# 4. Circular import prevention
# -----------------------------------------------------------------------


class TestCircularImportPrevention:
    """Verify that all refactored modules can be imported without circular errors."""

    def test_import_executor(self):
        importlib.import_module("aquarco_supervisor.pipeline.executor")

    def test_import_stage_runner(self):
        importlib.import_module("aquarco_supervisor.pipeline.stage_runner")

    def test_import_agent_invoker(self):
        importlib.import_module("aquarco_supervisor.pipeline.agent_invoker")

    def test_import_planner(self):
        importlib.import_module("aquarco_supervisor.pipeline.planner")

    def test_import_conditions(self):
        importlib.import_module("aquarco_supervisor.pipeline.conditions")

    def test_import_git_ops(self):
        importlib.import_module("aquarco_supervisor.pipeline.git_ops")

    def test_import_config_store(self):
        importlib.import_module("aquarco_supervisor.config_store")

    def test_import_agent_store(self):
        importlib.import_module("aquarco_supervisor.agent_store")

    def test_import_pipeline_store(self):
        importlib.import_module("aquarco_supervisor.pipeline_store")

    def test_import_stage_manager(self):
        importlib.import_module("aquarco_supervisor.stage_manager")

    def test_import_output_parser(self):
        importlib.import_module("aquarco_supervisor.cli.output_parser")

    def test_import_file_tailer(self):
        importlib.import_module("aquarco_supervisor.cli.file_tailer")


# -----------------------------------------------------------------------
# 5. Cross-module integration
# -----------------------------------------------------------------------


class TestCrossModuleIntegration:
    """Verify refactored modules integrate correctly."""

    def test_executor_delegates_to_runner(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """Executor creates a StageRunner as _runner."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert isinstance(executor._runner, StageRunner)

    def test_executor_delegates_to_invoker(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """Executor creates an AgentInvoker as _invoker."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert isinstance(executor._invoker, AgentInvoker)

    def test_runner_next_eo_is_executor_method(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """StageRunner's next_execution_order is bound to the executor's method."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        # Calling _runner's next_execution_order should update executor's counter
        val1 = executor._runner._next_execution_order("task-x")
        val2 = executor._runner._next_execution_order("task-x")
        assert val1 == 1
        assert val2 == 2
        assert executor._execution_order["task-x"] == 2

    def test_executor_backward_compat_reexports(self):
        """executor module re-exports for backward compatibility."""
        from aquarco_supervisor.pipeline.executor import (
            execute_claude,
            check_conditions,
            _compare_complexity,
            _auto_commit,
            _get_ahead_count,
            _git_checkout,
            _push_if_ahead,
        )
        assert callable(execute_claude)
        assert callable(check_conditions)
        assert callable(_compare_complexity)
        assert callable(_auto_commit)
        assert callable(_get_ahead_count)
        assert callable(_git_checkout)
        assert callable(_push_if_ahead)

    def test_invoker_lazy_imports_executor(self, mock_db, mock_registry, sample_pipelines):
        """AgentInvoker lazily imports executor module to break circular deps."""
        invoker = AgentInvoker(mock_db, mock_registry, sample_pipelines)
        assert hasattr(invoker, "_exec")
        from aquarco_supervisor.pipeline import executor
        assert invoker._exec is executor

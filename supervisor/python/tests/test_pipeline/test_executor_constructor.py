"""Tests for PipelineExecutor constructor — validates the fix for passing
resolved self._sm to child classes instead of the raw stage_manager parameter.

This was a latent bug where PipelinePlanner and StageRunner received None
when stage_manager was not explicitly provided (the default), causing
AttributeError on any call to self._sm methods.

Ref: GitHub issue #109, review finding (error severity).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.executor import PipelineExecutor
from aquarco_supervisor.stage_manager import StageManager
from aquarco_supervisor.task_queue import TaskQueue


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def mock_tq() -> AsyncMock:
    return AsyncMock(spec=TaskQueue)


@pytest.fixture
def mock_registry() -> MagicMock:
    return MagicMock()


class TestExecutorConstructorStageManager:
    """Verify PipelineExecutor passes resolved _sm to child classes."""

    def test_children_receive_resolved_sm_when_none_passed(
        self,
        mock_db: AsyncMock,
        mock_tq: AsyncMock,
        mock_registry: MagicMock,
        sample_pipelines: Any,
    ) -> None:
        """When stage_manager=None (default), children get StageManager(db) fallback."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
            stage_manager=None,
        )
        # self._sm should be a real StageManager, not None
        assert executor._sm is not None
        assert isinstance(executor._sm, StageManager)
        # Planner and Runner should have the same resolved _sm
        assert executor._planner._sm is executor._sm
        assert executor._runner._sm is executor._sm

    def test_children_receive_explicit_sm_when_provided(
        self,
        mock_db: AsyncMock,
        mock_tq: AsyncMock,
        mock_registry: MagicMock,
        sample_pipelines: Any,
    ) -> None:
        """When stage_manager is explicitly provided, children get that instance."""
        explicit_sm = MagicMock(spec=StageManager)
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
            stage_manager=explicit_sm,
        )
        assert executor._sm is explicit_sm
        assert executor._planner._sm is explicit_sm
        assert executor._runner._sm is explicit_sm

    def test_children_share_same_sm_instance(
        self,
        mock_db: AsyncMock,
        mock_tq: AsyncMock,
        mock_registry: MagicMock,
        sample_pipelines: Any,
    ) -> None:
        """Planner, Runner, and Executor all reference the same StageManager."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        # All three should point to the exact same object
        assert executor._planner._sm is executor._runner._sm
        assert executor._runner._sm is executor._sm


class TestExecutorConstructorSubmodules:
    """Verify child submodule wiring in constructor."""

    def test_invoker_created(
        self,
        mock_db: AsyncMock,
        mock_tq: AsyncMock,
        mock_registry: MagicMock,
        sample_pipelines: Any,
    ) -> None:
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._invoker is not None
        assert executor._invoker._db is mock_db
        assert executor._invoker._registry is mock_registry

    def test_planner_receives_working_execution_order(
        self,
        mock_db: AsyncMock,
        mock_tq: AsyncMock,
        mock_registry: MagicMock,
        sample_pipelines: Any,
    ) -> None:
        """Planner's next_execution_order should increment the executor's counter."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        # Calling via planner should affect the executor's counter
        val = executor._planner._next_execution_order("task-1")
        assert val == 1
        assert executor._execution_order["task-1"] == 1

    def test_runner_receives_working_execution_order(
        self,
        mock_db: AsyncMock,
        mock_tq: AsyncMock,
        mock_registry: MagicMock,
        sample_pipelines: Any,
    ) -> None:
        """Runner's next_execution_order should increment the executor's counter."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        val = executor._runner._next_execution_order("task-2")
        assert val == 1
        assert executor._execution_order["task-2"] == 1

    def test_execution_order_counter_starts_empty(
        self,
        mock_db: AsyncMock,
        mock_tq: AsyncMock,
        mock_registry: MagicMock,
        sample_pipelines: Any,
    ) -> None:
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._execution_order == {}

    def test_next_execution_order_increments(
        self,
        mock_db: AsyncMock,
        mock_tq: AsyncMock,
        mock_registry: MagicMock,
        sample_pipelines: Any,
    ) -> None:
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._next_execution_order("task-1") == 1
        assert executor._next_execution_order("task-1") == 2
        assert executor._next_execution_order("task-2") == 1
        assert executor._next_execution_order("task-1") == 3

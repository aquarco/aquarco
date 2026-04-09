"""Tests for PipelineExecutor constructor — self._sm wiring fix.

Validates that PipelinePlanner and StageRunner receive the resolved
self._sm (with StageManager fallback) rather than the raw stage_manager
constructor parameter.  This is a regression test for the bug fixed in
executor.py:72,76.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.models import PipelineConfig
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry
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
    return MagicMock(spec=AgentRegistry)


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


class TestExecutorSmWiring:
    """Regression tests for executor.py:72,76 — self._sm passed to children."""

    def test_planner_receives_resolved_sm(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """PipelinePlanner must receive self._sm (the resolved StageManager),
        not the raw stage_manager parameter."""
        sm = AsyncMock(spec=StageManager)
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines, stage_manager=sm,
        )
        assert executor._planner._sm is sm

    def test_runner_receives_resolved_sm(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """StageRunner must receive self._sm, not None."""
        sm = AsyncMock(spec=StageManager)
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines, stage_manager=sm,
        )
        assert executor._runner._sm is sm

    def test_sm_defaults_to_stage_manager_when_none(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """When stage_manager=None, self._sm should be a StageManager(db)."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert isinstance(executor._sm, StageManager)
        assert executor._sm._db is mock_db

    def test_planner_gets_fallback_sm_when_none(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """Even when stage_manager=None, planner should get a valid SM."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._planner._sm is not None
        assert executor._planner._sm is executor._sm

    def test_runner_gets_fallback_sm_when_none(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        """Even when stage_manager=None, runner should get a valid SM."""
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._runner._sm is not None
        assert executor._runner._sm is executor._sm


class TestNextExecutionOrder:
    """Tests for PipelineExecutor._next_execution_order."""

    def test_increments_from_zero(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._next_execution_order("task-1") == 1
        assert executor._next_execution_order("task-1") == 2
        assert executor._next_execution_order("task-1") == 3

    def test_separate_counters_per_task(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._next_execution_order("task-a") == 1
        assert executor._next_execution_order("task-b") == 1
        assert executor._next_execution_order("task-a") == 2
        assert executor._next_execution_order("task-b") == 2

    def test_initial_state_empty(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._execution_order == {}


class TestExecutorDelegation:
    """Test that executor properly delegates to sub-components."""

    def test_invoker_created(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._invoker is not None

    def test_planner_created(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._planner is not None

    def test_runner_created(
        self, mock_db, mock_tq, mock_registry, sample_pipelines,
    ):
        executor = PipelineExecutor(
            mock_db, mock_tq, mock_registry, sample_pipelines,
        )
        assert executor._runner is not None

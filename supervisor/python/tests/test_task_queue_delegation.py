"""Tests for TaskQueue backward-compatible delegation to StageManager via __getattr__.

After the refactoring, stage-related methods were moved from TaskQueue to StageManager.
TaskQueue.__getattr__ lazily delegates calls to self._sm. These tests validate that:
- Deprecated methods are accessible on TaskQueue instances
- They correctly delegate to StageManager methods
- AttributeError is raised for genuinely missing attributes
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.stage_manager import StageManager
from aquarco_supervisor.task_queue import TaskQueue


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def mock_sm() -> AsyncMock:
    sm = AsyncMock(spec=StageManager)
    sm.record_stage_executing = AsyncMock()
    sm.record_stage_failed = AsyncMock()
    sm.store_stage_output = AsyncMock()
    sm.create_iteration_stage = AsyncMock(return_value=42)
    sm.get_task_context = AsyncMock(return_value={"key": "val"})
    return sm


@pytest.fixture
def tq(mock_db, mock_sm) -> TaskQueue:
    tq = TaskQueue(mock_db)
    # Replace the internally-created StageManager with our mock
    tq._sm = mock_sm
    return tq


class TestGetAttrDelegation:
    """__getattr__ delegates stage methods to StageManager."""

    def test_delegates_record_stage_executing(self, tq, mock_sm):
        """TaskQueue.record_stage_executing should delegate to StageManager."""
        method = getattr(tq, "record_stage_executing", None)
        assert method is not None
        assert method is mock_sm.record_stage_executing

    def test_delegates_record_stage_failed(self, tq, mock_sm):
        method = getattr(tq, "record_stage_failed", None)
        assert method is not None
        assert method is mock_sm.record_stage_failed

    def test_delegates_store_stage_output(self, tq, mock_sm):
        method = getattr(tq, "store_stage_output", None)
        assert method is not None
        assert method is mock_sm.store_stage_output

    def test_delegates_create_iteration_stage(self, tq, mock_sm):
        method = getattr(tq, "create_iteration_stage", None)
        assert method is not None
        assert method is mock_sm.create_iteration_stage

    def test_delegates_get_task_context(self, tq, mock_sm):
        method = getattr(tq, "get_task_context", None)
        assert method is not None
        assert method is mock_sm.get_task_context

    def test_raises_attribute_error_for_missing(self, tq):
        """Genuinely missing attributes should still raise AttributeError."""
        with pytest.raises(AttributeError, match="totally_nonexistent_method"):
            _ = tq.totally_nonexistent_method

    def test_own_attributes_not_delegated(self, tq, mock_db):
        """Attributes defined directly on TaskQueue should NOT go through __getattr__."""
        assert tq._db is mock_db


class TestGetAttrCallable:
    """Delegated methods should be callable and return expected results."""

    @pytest.mark.asyncio
    async def test_delegated_create_iteration_stage_is_callable(self, tq, mock_sm):
        result = await tq.create_iteration_stage(
            task_id="task-1",
            stage_number=0,
            category="analyze",
            agent_name="test-agent",
        )
        assert result == 42
        mock_sm.create_iteration_stage.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delegated_get_task_context_is_callable(self, tq, mock_sm):
        result = await tq.get_task_context("task-1")
        assert result == {"key": "val"}
        mock_sm.get_task_context.assert_awaited_once_with("task-1")

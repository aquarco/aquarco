"""Tests for execution_order support in TaskQueue methods.

Covers:
- get_max_execution_order: returns max from DB, handles None/0
- get_stage_number_for_id: resolves stage id to stage_number
- record_stage_executing: passes execution_order through all code paths
- record_stage_skipped: passes execution_order through all code paths
- record_stage_skipped terminal status guard
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.task_queue import TaskQueue


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    return db


@pytest.fixture
def task_queue(mock_db: AsyncMock) -> TaskQueue:
    return TaskQueue(mock_db, max_retries=3)


# ---------------------------------------------------------------------------
# get_max_execution_order
# ---------------------------------------------------------------------------


class TestGetMaxExecutionOrder:
    """Tests for TaskQueue.get_max_execution_order."""

    @pytest.mark.asyncio
    async def test_returns_max_value(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """Returns the max execution_order from DB."""
        mock_db.fetch_val.return_value = 7
        result = await task_queue.get_max_execution_order("task-1")
        assert result == 7

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_rows(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """COALESCE returns 0 when no stages have execution_order set."""
        mock_db.fetch_val.return_value = 0
        result = await task_queue.get_max_execution_order("task-1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_returns_zero_when_none(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """If fetch_val returns None (should not happen with COALESCE), returns 0."""
        mock_db.fetch_val.return_value = None
        result = await task_queue.get_max_execution_order("task-1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_passes_correct_task_id(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """Verifies the task_id is passed correctly in the query params."""
        mock_db.fetch_val.return_value = 3
        await task_queue.get_max_execution_order("my-task-42")
        params = mock_db.fetch_val.call_args[0][1]
        assert params["task_id"] == "my-task-42"

    @pytest.mark.asyncio
    async def test_query_filters_non_null(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """SQL query must filter WHERE execution_order IS NOT NULL."""
        mock_db.fetch_val.return_value = 0
        await task_queue.get_max_execution_order("task-1")
        sql = mock_db.fetch_val.call_args[0][0]
        assert "execution_order IS NOT NULL" in sql


# ---------------------------------------------------------------------------
# get_stage_number_for_id
# ---------------------------------------------------------------------------


class TestGetStageNumberForId:
    """Tests for TaskQueue.get_stage_number_for_id."""

    @pytest.mark.asyncio
    async def test_returns_stage_number(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """Returns stage_number as int when found."""
        mock_db.fetch_val.return_value = 3
        result = await task_queue.get_stage_number_for_id(42)
        assert result == 3

    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """Returns None when no row matches the stage id."""
        mock_db.fetch_val.return_value = None
        result = await task_queue.get_stage_number_for_id(999)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_zero_correctly(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """Stage number 0 is valid and should not be confused with None."""
        mock_db.fetch_val.return_value = 0
        result = await task_queue.get_stage_number_for_id(1)
        assert result == 0
        assert result is not None


# ---------------------------------------------------------------------------
# record_stage_executing: execution_order in all code paths
# ---------------------------------------------------------------------------


class TestRecordStageExecutingExecutionOrder:
    """Tests that execution_order is included in all record_stage_executing paths."""

    @pytest.mark.asyncio
    async def test_stage_id_path_includes_execution_order(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """stage_id UPDATE path includes execution_order = %(eo)s."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "analyze-agent",
            stage_id=42,
            execution_order=5,
        )
        call_args = mock_db.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "execution_order" in sql
        assert params["eo"] == 5

    @pytest.mark.asyncio
    async def test_stage_key_path_includes_execution_order(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """stage_key UPDATE path includes execution_order = %(eo)s."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "analyze-agent",
            stage_key="0:analyze:analyze-agent",
            iteration=1,
            execution_order=3,
        )
        call_args = mock_db.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "execution_order" in sql
        assert params["eo"] == 3

    @pytest.mark.asyncio
    async def test_legacy_path_includes_execution_order(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """Legacy INSERT/ON CONFLICT path includes execution_order."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "analyze-agent",
            execution_order=1,
        )
        call_args = mock_db.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "execution_order" in sql
        assert params["eo"] == 1

    @pytest.mark.asyncio
    async def test_execution_order_none_when_not_provided(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """When execution_order not passed, eo parameter is None."""
        await task_queue.record_stage_executing(
            "task-1", 0, "analyze", "analyze-agent",
            stage_id=10,
        )
        params = mock_db.execute.call_args[0][1]
        assert params["eo"] is None


# ---------------------------------------------------------------------------
# record_stage_skipped: execution_order in all code paths
# ---------------------------------------------------------------------------


class TestRecordStageSkippedExecutionOrder:
    """Tests that execution_order is included in all record_stage_skipped paths."""

    @pytest.mark.asyncio
    async def test_stage_id_path_includes_execution_order(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """stage_id UPDATE path includes execution_order."""
        await task_queue.record_stage_skipped(
            "task-1", 2, "docs",
            stage_id=50,
            execution_order=4,
        )
        call_args = mock_db.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "execution_order" in sql
        assert params["eo"] == 4

    @pytest.mark.asyncio
    async def test_stage_key_path_includes_execution_order(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """stage_key UPDATE path includes execution_order."""
        await task_queue.record_stage_skipped(
            "task-1", 2, "docs",
            stage_key="2:docs:docs-agent",
            execution_order=6,
        )
        call_args = mock_db.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "execution_order" in sql
        assert params["eo"] == 6

    @pytest.mark.asyncio
    async def test_legacy_path_includes_execution_order(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """Legacy INSERT/ON CONFLICT path includes execution_order."""
        await task_queue.record_stage_skipped(
            "task-1", 2, "docs",
            execution_order=2,
        )
        call_args = mock_db.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "execution_order" in sql
        assert params["eo"] == 2

    @pytest.mark.asyncio
    async def test_execution_order_none_when_not_provided(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """When execution_order not passed, eo parameter is None."""
        await task_queue.record_stage_skipped(
            "task-1", 2, "docs",
            stage_id=50,
        )
        params = mock_db.execute.call_args[0][1]
        assert params["eo"] is None

    @pytest.mark.asyncio
    async def test_stage_id_path_guards_terminal_status(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """stage_id UPDATE has NOT IN guard to protect terminal statuses."""
        await task_queue.record_stage_skipped(
            "task-1", 2, "docs",
            stage_id=50,
            execution_order=4,
        )
        sql = mock_db.execute.call_args[0][0]
        assert "NOT IN" in sql
        assert "completed" in sql
        assert "failed" in sql

    @pytest.mark.asyncio
    async def test_stage_key_path_guards_terminal_status(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """stage_key UPDATE has NOT IN guard to protect terminal statuses."""
        await task_queue.record_stage_skipped(
            "task-1", 2, "docs",
            stage_key="2:docs:docs-agent",
        )
        sql = mock_db.execute.call_args[0][0]
        assert "NOT IN" in sql
        assert "completed" in sql
        assert "failed" in sql

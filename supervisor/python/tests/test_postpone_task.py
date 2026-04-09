"""Tests for TaskQueue.postpone_task() and get_postponed_tasks() — AC-17 through AC-21.

These tests cover the most risk-bearing changes in the retryable-error PR:
new SQL, changed schema usage, and per-row cooldown semantics.
"""

from __future__ import annotations

import warnings
from unittest.mock import AsyncMock, call

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.task_queue import TaskQueue


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def tq(mock_db: AsyncMock) -> TaskQueue:
    return TaskQueue(mock_db, max_retries=5)


# ---------------------------------------------------------------------------
# AC-17: postpone_task() persists cooldown_minutes and increments count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postpone_task_sql_sets_cooldown(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-17: postpone_task() UPDATE stores the caller-supplied cooldown_minutes."""
    mock_db.fetch_one = AsyncMock(
        return_value={"status": "rate_limited", "rate_limit_count": 1}
    )

    await tq.postpone_task("task-abc", "server error", cooldown_minutes=30, max_retries=12)

    # First call must be an UPDATE with correct params
    update_call = mock_db.execute.call_args_list[0]
    sql: str = update_call[0][0]
    params: dict = update_call[0][1]

    assert "postpone_cooldown_minutes" in sql
    assert "rate_limit_count" in sql
    assert params["id"] == "task-abc"
    assert params["cooldown"] == 30
    assert params["max"] == 12
    assert params["error"] == "server error"


@pytest.mark.asyncio
async def test_postpone_task_default_cooldown_is_60(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-17: default cooldown_minutes=60 is passed to SQL."""
    mock_db.fetch_one = AsyncMock(
        return_value={"status": "rate_limited", "rate_limit_count": 1}
    )

    await tq.postpone_task("task-def", "overload")

    params = mock_db.execute.call_args_list[0][0][1]
    assert params["cooldown"] == 60


@pytest.mark.asyncio
async def test_postpone_task_marks_failed_when_retries_exhausted(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-18: task is marked 'failed' when rate_limit_count >= max_retries."""
    # Simulate the DB returning status='failed' after the UPDATE
    mock_db.fetch_one = AsyncMock(
        return_value={"status": "failed", "rate_limit_count": 12}
    )

    await tq.postpone_task("task-exhausted", "too many retries", max_retries=12)

    # Should execute the UPDATE (which the DB transitions to failed internally)
    sql: str = mock_db.execute.call_args_list[0][0][0]
    assert "failed" in sql  # CASE WHEN ... THEN 'failed'


@pytest.mark.asyncio
async def test_postpone_task_rate_limited_status_when_retries_remain(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-18: task remains 'rate_limited' when retries are not yet exhausted."""
    mock_db.fetch_one = AsyncMock(
        return_value={"status": "rate_limited", "rate_limit_count": 3}
    )

    await tq.postpone_task("task-ok", "transient error", max_retries=12)

    sql: str = mock_db.execute.call_args_list[0][0][0]
    assert "rate_limited" in sql


# ---------------------------------------------------------------------------
# AC-19: rate_limit_task() delegates to postpone_task() with 60-min cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_task_delegates_to_postpone(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-19: rate_limit_task() calls postpone_task() with cooldown_minutes=60."""
    mock_db.fetch_one = AsyncMock(
        return_value={"status": "rate_limited", "rate_limit_count": 1}
    )

    await tq.rate_limit_task("task-rl", "rate limited", max_rate_limit_retries=24)

    params = mock_db.execute.call_args_list[0][0][1]
    assert params["cooldown"] == 60
    assert params["max"] == 24


# ---------------------------------------------------------------------------
# AC-20: get_postponed_tasks() uses per-row cooldown in the WHERE clause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_postponed_tasks_sql_uses_per_row_cooldown(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-20: get_postponed_tasks() WHERE clause references postpone_cooldown_minutes column."""
    mock_db.fetch_all = AsyncMock(return_value=[{"id": "task-1"}, {"id": "task-2"}])

    result = await tq.get_postponed_tasks()

    sql: str = mock_db.fetch_all.call_args[0][0]
    assert "postpone_cooldown_minutes" in sql
    assert "rate_limited" in sql
    assert result == ["task-1", "task-2"]


@pytest.mark.asyncio
async def test_get_postponed_tasks_empty_when_none_ready(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-20: get_postponed_tasks() returns [] when no tasks have elapsed cooldown."""
    mock_db.fetch_all = AsyncMock(return_value=[])

    result = await tq.get_postponed_tasks()

    assert result == []


# ---------------------------------------------------------------------------
# AC-21: get_rate_limited_tasks() is a deprecated alias for get_postponed_tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rate_limited_tasks_delegates_to_get_postponed_tasks(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-21: get_rate_limited_tasks() delegates to get_postponed_tasks()."""
    mock_db.fetch_all = AsyncMock(return_value=[])
    result = await tq.get_rate_limited_tasks()
    assert result == []
    mock_db.fetch_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_rate_limited_tasks_ignores_cooldown_minutes_param(
    tq: TaskQueue, mock_db: AsyncMock
) -> None:
    """AC-21: cooldown_minutes argument is silently ignored; per-row cooldown used instead."""
    mock_db.fetch_all = AsyncMock(return_value=[{"id": "t-1"}])

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        result = await tq.get_rate_limited_tasks(cooldown_minutes=999)

    # SQL must still reference the column, not the passed value
    sql: str = mock_db.fetch_all.call_args[0][0]
    assert "999" not in sql
    assert "postpone_cooldown_minutes" in sql
    assert result == ["t-1"]

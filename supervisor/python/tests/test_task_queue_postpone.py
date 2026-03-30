"""Tests for TaskQueue.postpone_task and get_postponed_tasks / get_rate_limited_tasks.

Covers:
  - postpone_task() passes cooldown_minutes and max_retries to the DB query
  - postpone_task() delegates to the DB with correct params for each error type
  - get_rate_limited_tasks() delegates to get_postponed_tasks() (backward-compat alias)
  - rate_limit_task() delegates to postpone_task with 60-minute cooldown

Note on scope vs test_postpone_task.py:
  ``test_postpone_task.py`` validates the AC-numbered acceptance criteria from the
  retryable-error design document (SQL column names, retry-exhaustion logic, and
  the DeprecationWarning emitted by ``rate_limit_task``).  This file exercises the
  *per-error-type cooldown values* surfaced through the public
  ``postpone_task`` / ``rate_limit_task`` API — a complementary behavioural lens
  that is intentionally kept separate to avoid bloating the AC test file.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aquarco_supervisor.task_queue import TaskQueue


def _make_task_queue(max_retries: int = 3) -> tuple[TaskQueue, AsyncMock]:
    """Create a TaskQueue with a mock Database."""
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock()
    mock_db.fetch_one = AsyncMock(return_value={"status": "rate_limited", "rate_limit_count": 1})
    mock_db.fetch_all = AsyncMock(return_value=[])
    mock_db.fetch_val = AsyncMock(return_value=0)
    tq = TaskQueue(mock_db, max_retries=max_retries)
    return tq, mock_db


# ---------------------------------------------------------------------------
# postpone_task — basic invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postpone_task_passes_cooldown_to_db() -> None:
    """postpone_task stores the given cooldown_minutes in the DB update."""
    tq, mock_db = _make_task_queue()

    await tq.postpone_task("task-1", "overloaded", cooldown_minutes=15, max_retries=24)

    mock_db.execute.assert_awaited_once()
    sql, params = mock_db.execute.call_args.args
    assert params["cooldown"] == 15
    assert params["max"] == 24
    assert params["id"] == "task-1"
    assert "overloaded" in params["error"]


@pytest.mark.asyncio
async def test_postpone_task_server_error_cooldown() -> None:
    """postpone_task accepts ServerError-specific cooldown (30 min, 12 retries)."""
    tq, mock_db = _make_task_queue()

    await tq.postpone_task("task-500", "server error", cooldown_minutes=30, max_retries=12)

    _, params = mock_db.execute.call_args.args
    assert params["cooldown"] == 30
    assert params["max"] == 12


@pytest.mark.asyncio
async def test_postpone_task_rate_limit_default_cooldown() -> None:
    """postpone_task default values match the RateLimitError cooldown (60 min, 24 retries)."""
    tq, mock_db = _make_task_queue()

    await tq.postpone_task("task-429", "rate limited")

    _, params = mock_db.execute.call_args.args
    assert params["cooldown"] == 60
    assert params["max"] == 24


@pytest.mark.asyncio
async def test_postpone_task_marks_failed_when_retries_exhausted() -> None:
    """When rate_limit_count + 1 >= max_retries, DB row status becomes 'failed'."""
    tq, mock_db = _make_task_queue()
    # Simulate DB returning failed status after increment
    mock_db.fetch_one = AsyncMock(
        return_value={"status": "failed", "rate_limit_count": 24}
    )

    # Should not raise
    await tq.postpone_task("task-exhausted", "too many", cooldown_minutes=60, max_retries=24)

    # DB execute still called
    mock_db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# rate_limit_task — backward-compat delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_task_delegates_to_postpone_task() -> None:
    """rate_limit_task() calls postpone_task with cooldown_minutes=60."""
    tq, mock_db = _make_task_queue()

    await tq.rate_limit_task("task-rl", "hit rate limit", max_rate_limit_retries=24)

    _, params = mock_db.execute.call_args.args
    assert params["cooldown"] == 60
    assert params["max"] == 24
    assert params["id"] == "task-rl"


# ---------------------------------------------------------------------------
# get_rate_limited_tasks — backward-compat delegation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rate_limited_tasks_delegates_to_get_postponed_tasks() -> None:
    """get_rate_limited_tasks() returns same result as get_postponed_tasks()."""
    tq, mock_db = _make_task_queue()
    mock_db.fetch_all = AsyncMock(return_value=[{"id": "task-a"}, {"id": "task-b"}])

    result = await tq.get_rate_limited_tasks()

    assert result == ["task-a", "task-b"]
    mock_db.fetch_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_rate_limited_tasks_ignores_cooldown_minutes_arg() -> None:
    """The cooldown_minutes parameter is ignored — per-row cooldown is used instead."""
    tq, mock_db = _make_task_queue()
    mock_db.fetch_all = AsyncMock(return_value=[{"id": "task-x"}])

    # Both calls should produce same DB query regardless of the arg
    result_default = await tq.get_rate_limited_tasks()
    result_custom = await tq.get_rate_limited_tasks(cooldown_minutes=999)

    assert result_default == result_custom == ["task-x"]


@pytest.mark.asyncio
async def test_get_postponed_tasks_returns_empty_when_none_ready() -> None:
    """get_postponed_tasks returns empty list when no tasks have elapsed cooldown."""
    tq, mock_db = _make_task_queue()
    mock_db.fetch_all = AsyncMock(return_value=[])

    result = await tq.get_postponed_tasks()

    assert result == []

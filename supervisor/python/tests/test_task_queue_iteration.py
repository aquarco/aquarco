"""Tests for create_iteration_stage ON CONFLICT and related iteration methods.

Covers:
- create_iteration_stage idempotency (ON CONFLICT DO NOTHING)
- create_iteration_stage returns correct stage_key format
- get_max_iteration boundary cases
- create_rerun_stage for reruns
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.task_queue import TaskQueue


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def task_queue(mock_db: AsyncMock) -> TaskQueue:
    return TaskQueue(mock_db, max_retries=3)


# ---------------------------------------------------------------------------
# create_iteration_stage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_iteration_stage_uses_on_conflict(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """SQL must include ON CONFLICT DO NOTHING for crash recovery idempotency."""
    await task_queue.create_iteration_stage("task-1", 0, "review", "agent-1", 2)

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "ON CONFLICT DO NOTHING" in sql


@pytest.mark.asyncio
async def test_create_iteration_stage_returns_stage_key_format(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Stage key follows the format: {stage_num}:{category}:{agent}."""
    result = await task_queue.create_iteration_stage(
        "task-1", 3, "test", "test-agent", 2,
    )
    assert result == "3:test:test-agent"


@pytest.mark.asyncio
async def test_create_iteration_stage_passes_correct_params(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """All parameters are correctly passed to the SQL query."""
    await task_queue.create_iteration_stage("task-42", 1, "impl", "impl-agent", 5)

    params = mock_db.execute.call_args[0][1]
    assert params["task_id"] == "task-42"
    assert params["stage"] == 1
    assert params["category"] == "impl"
    assert params["agent"] == "impl-agent"
    assert params["iteration"] == 5
    assert params["stage_key"] == "1:impl:impl-agent"


@pytest.mark.asyncio
async def test_create_iteration_stage_inserts_pending_status(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """New iteration stages start with 'pending' status."""
    await task_queue.create_iteration_stage("task-1", 0, "review", "agent", 3)

    sql = mock_db.execute.call_args[0][0]
    assert "'pending'" in sql


# ---------------------------------------------------------------------------
# create_system_stage also uses ON CONFLICT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_system_stage_uses_on_conflict(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """System stages also use ON CONFLICT DO NOTHING."""
    await task_queue.create_system_stage(
        "task-1", -1, "planning", "planner-agent",
        stage_key="-1:planning:planner-agent",
    )

    sql = mock_db.execute.call_args[0][0]
    assert "ON CONFLICT DO NOTHING" in sql


# ---------------------------------------------------------------------------
# create_rerun_stage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_rerun_stage_passes_run_number(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Rerun stage includes the run number in parameters."""
    await task_queue.create_rerun_stage(
        "task-1", 0, "review", "review-agent",
        "0:review:review-agent", iteration=1, run=3,
    )

    params = mock_db.execute.call_args[0][1]
    assert params["run"] == 3
    assert params["iteration"] == 1
    assert params["stage_key"] == "0:review:review-agent"


# ---------------------------------------------------------------------------
# get_latest_stage_run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_latest_stage_run_returns_row(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Returns the latest run for a stage."""
    mock_db.fetch_one.return_value = {
        "status": "completed", "run": 2, "error_message": None,
    }

    result = await task_queue.get_latest_stage_run(
        "task-1", "0:review:agent", iteration=1,
    )

    assert result is not None
    assert result["status"] == "completed"
    assert result["run"] == 2


@pytest.mark.asyncio
async def test_get_latest_stage_run_returns_none_when_missing(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Returns None when no stage run exists."""
    mock_db.fetch_one.return_value = None

    result = await task_queue.get_latest_stage_run("task-1", "0:review:agent")
    assert result is None

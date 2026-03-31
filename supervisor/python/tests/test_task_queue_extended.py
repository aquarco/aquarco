"""Extended tests for TaskQueue – covering stage_key paths and validation items."""

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


# --- record_stage_executing with stage_key ---


@pytest.mark.asyncio
async def test_record_stage_executing_with_stage_key(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """stage_key path uses UPDATE instead of INSERT."""
    await task_queue.record_stage_executing(
        "task-1", 0, "review", "review-agent",
        stage_key="0:review:review-agent", iteration=2,
        input_context={"some": "data"},
    )

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "UPDATE stages" in sql
    assert "stage_key = %(stage_key)s" in sql
    assert params["stage_key"] == "0:review:review-agent"
    assert params["iteration"] == 2
    assert params["agent"] == "review-agent"
    assert json.loads(params["input"]) == {"some": "data"}


@pytest.mark.asyncio
async def test_record_stage_executing_with_stage_key_no_input(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """stage_key path with no input_context passes None."""
    await task_queue.record_stage_executing(
        "task-1", 0, "review", "review-agent",
        stage_key="0:review:review-agent", iteration=1,
    )

    params = mock_db.execute.call_args[0][1]
    assert params["input"] is None


# --- record_stage_failed with stage_key ---


@pytest.mark.asyncio
async def test_record_stage_failed_with_stage_key(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.record_stage_failed(
        "task-1", 0, "timeout error",
        stage_key="0:review:review-agent", iteration=2,
    )

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "UPDATE stages" in sql
    assert "stage_key = %(stage_key)s" in sql
    assert params["stage_key"] == "0:review:review-agent"
    assert params["error"] == "timeout error"


# --- record_stage_skipped with stage_key ---


@pytest.mark.asyncio
async def test_record_stage_skipped_with_stage_key(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.record_stage_skipped(
        "task-1", 2, "docs",
        stage_key="2:docs:docs-agent",
    )

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "UPDATE stages" in sql
    assert "stage_key = %(stage_key)s" in sql
    assert params["stage_key"] == "2:docs:docs-agent"


@pytest.mark.asyncio
async def test_record_stage_skipped_with_stage_key_guards_terminal_status(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """UPDATE stage_key path must include AND status NOT IN ('completed', 'failed')
    so that a late 'skipped' signal never overwrites a row that already recorded
    a terminal status (core bugfix for the stage_key branch of record_stage_skipped).
    """
    await task_queue.record_stage_skipped(
        "task-1", 2, "docs",
        stage_key="2:docs:docs-agent",
    )

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "NOT IN" in sql, (
        "UPDATE WHERE clause must contain a NOT IN guard to prevent overwriting "
        "terminal status via stage_key path"
    )
    assert "completed" in sql, "SQL guard must exclude 'completed' rows"
    assert "failed" in sql, "SQL guard must exclude 'failed' rows"


# --- store_stage_output with stage_key ---


# --- create_planned_pending_stages ---


@pytest.mark.asyncio
async def test_create_planned_pending_stages(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    planned = [
        {"category": "review", "agents": ["review-agent", "qa-agent"]},
        {"category": "test", "agents": ["test-agent"]},
    ]

    # fetch_val returns sequential ids for each INSERT
    mock_db.fetch_val = AsyncMock(side_effect=[100, 101, 102])
    result = await task_queue.create_planned_pending_stages("task-1", planned)

    # 3 total agents across 2 stages
    assert mock_db.fetch_val.call_count == 3

    # Verify stage_key format in returned dict
    assert result == {
        "0:review:review-agent": 100,
        "0:review:qa-agent": 101,
        "1:test:test-agent": 102,
    }


@pytest.mark.asyncio
async def test_create_planned_pending_stages_agent_name_dict(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Planner may return agents as dicts with 'agent_name' key."""
    planned = [
        {
            "category": "analyze",
            "agents": [
                {"agent_name": "analyze-agent", "reasoning": "Primary agent"},
            ],
        },
        {
            "category": "review",
            "agents": [
                {"name": "review-agent"},
                "qa-agent",
            ],
        },
    ]

    mock_db.fetch_val = AsyncMock(side_effect=[200, 201, 202])
    result = await task_queue.create_planned_pending_stages("task-1", planned)

    assert mock_db.fetch_val.call_count == 3
    assert "0:analyze:analyze-agent" in result
    assert "1:review:review-agent" in result
    assert "1:review:qa-agent" in result


@pytest.mark.asyncio
async def test_create_planned_pending_stages_conflict_returns_none(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """ON CONFLICT DO NOTHING rows return None and are omitted from the mapping."""
    planned = [
        {"category": "review", "agents": ["review-agent"]},
    ]

    mock_db.fetch_val = AsyncMock(return_value=None)
    result = await task_queue.create_planned_pending_stages("task-1", planned)

    assert result == {}


# --- create_iteration_stage ---


@pytest.mark.asyncio
async def test_create_iteration_stage(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_val = AsyncMock(return_value=42)
    stage_key, row_id = await task_queue.create_iteration_stage(
        "task-1", 2, "review", "review-agent", 3,
    )

    assert stage_key == "2:review:review-agent"
    assert row_id == 42
    call_args = mock_db.fetch_val.call_args
    params = call_args[0][1]
    assert params["iteration"] == 3
    assert params["stage_key"] == "2:review:review-agent"


# --- get_max_iteration ---


@pytest.mark.asyncio
async def test_get_max_iteration(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_val.return_value = 3
    result = await task_queue.get_max_iteration("task-1", "0:review:review-agent")
    assert result == 3


@pytest.mark.asyncio
async def test_get_max_iteration_no_rows(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_val.return_value = None
    result = await task_queue.get_max_iteration("task-1", "0:review:review-agent")
    assert result == 0


# --- update_task_phase ---


@pytest.mark.asyncio
async def test_update_task_phase(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    from aquarco_supervisor.models import TaskPhase

    await task_queue.update_task_phase("task-1", TaskPhase.RUNNING)

    call_args = mock_db.execute.call_args
    params = call_args[0][1]
    assert params["phase"] == "running"


# --- store_planned_stages ---


@pytest.mark.asyncio
async def test_store_planned_stages(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    planned = [{"category": "review", "agents": ["a1"]}]
    await task_queue.store_planned_stages("task-1", planned)

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "planned_stages" in sql
    assert json.loads(params["stages"]) == planned


# --- update_task_status for timeout ---


@pytest.mark.asyncio
async def test_update_task_status_timeout(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Timeout status sets completed_at."""
    from aquarco_supervisor.models import TaskStatus

    await task_queue.update_task_status("task-1", TaskStatus.TIMEOUT)

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "completed_at = NOW()" in sql


# --- update_task_status for failed ---


@pytest.mark.asyncio
async def test_update_task_status_failed(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    from aquarco_supervisor.models import TaskStatus

    await task_queue.update_task_status("task-1", TaskStatus.FAILED)

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "completed_at = NOW()" in sql

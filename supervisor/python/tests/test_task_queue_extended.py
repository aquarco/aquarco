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


# --- store_stage_output with stage_key ---


@pytest.mark.asyncio
async def test_store_stage_output_with_validation_items(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Validation items in and out are passed as JSON."""
    vi_in = [{"id": 1, "description": "fix bug"}]
    vi_out = [{"category": "test", "description": "add test"}]
    output = {"verdict": "pass"}

    await task_queue.store_stage_output(
        "task-1", 1, "test", "test-agent", output,
        stage_key="1:test:test-agent", iteration=1,
        validation_items_in=vi_in,
        validation_items_out=vi_out,
    )

    first_params = mock_db.execute.call_args_list[0][0][1]
    assert json.loads(first_params["vi_in"]) == vi_in
    assert json.loads(first_params["vi_out"]) == vi_out


# --- create_planned_pending_stages ---


@pytest.mark.asyncio
async def test_create_planned_pending_stages(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    planned = [
        {"category": "review", "agents": ["review-agent", "qa-agent"]},
        {"category": "test", "agents": ["test-agent"]},
    ]

    await task_queue.create_planned_pending_stages("task-1", planned)

    # 3 total agents across 2 stages
    assert mock_db.execute.call_count == 3

    # Verify stage_key format
    calls = [c[0][1] for c in mock_db.execute.call_args_list]
    stage_keys = [c["stage_key"] for c in calls]
    assert "0:review:review-agent" in stage_keys
    assert "0:review:qa-agent" in stage_keys
    assert "1:test:test-agent" in stage_keys


# --- create_iteration_stage ---


@pytest.mark.asyncio
async def test_create_iteration_stage(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    result = await task_queue.create_iteration_stage(
        "task-1", 2, "review", "review-agent", 3,
    )

    assert result == "2:review:review-agent"
    call_args = mock_db.execute.call_args
    params = call_args[0][1]
    assert params["iteration"] == 3
    assert params["stage_key"] == "2:review:review-agent"


# --- validation items ---


@pytest.mark.asyncio
async def test_add_validation_item(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_one.return_value = {"id": 42}

    vi_id = await task_queue.add_validation_item(
        "task-1", "0:review:review-agent", "test", "Missing unit tests",
    )

    assert vi_id == 42
    call_args = mock_db.fetch_one.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "INSERT INTO validation_items" in sql
    assert params["desc"] == "Missing unit tests"


@pytest.mark.asyncio
async def test_resolve_validation_item(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.resolve_validation_item(42, "1:test:test-agent")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "status = 'resolved'" in sql
    assert params["id"] == 42
    assert params["resolved_by"] == "1:test:test-agent"


@pytest.mark.asyncio
async def test_get_open_validation_items_with_category(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_all.return_value = [
        {
            "id": 1,
            "task_id": "task-1",
            "stage_key": "0:review:agent",
            "category": "test",
            "description": "Need tests",
            "status": "open",
            "resolved_by": None,
            "resolved_at": None,
            "created_at": None,
        }
    ]

    items = await task_queue.get_open_validation_items("task-1", "test")

    assert len(items) == 1
    assert items[0].description == "Need tests"
    call_args = mock_db.fetch_all.call_args
    sql = call_args[0][0]
    assert "category = %(cat)s" in sql


@pytest.mark.asyncio
async def test_get_open_validation_items_without_category(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_all.return_value = []

    items = await task_queue.get_open_validation_items("task-1")

    assert len(items) == 0
    call_args = mock_db.fetch_all.call_args
    sql = call_args[0][0]
    # The no-category path should NOT have "category = %(cat)s" filter
    assert "%(cat)s" not in sql


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

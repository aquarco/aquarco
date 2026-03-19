"""Tests for TaskQueue with mocked database."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aifishtank_supervisor.database import Database
from aifishtank_supervisor.models import TaskStatus
from aifishtank_supervisor.task_queue import TaskQueue


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    return db


@pytest.fixture
def task_queue(mock_db: AsyncMock) -> TaskQueue:
    return TaskQueue(mock_db, max_retries=3)


@pytest.mark.asyncio
async def test_create_task_success(task_queue: TaskQueue, mock_db: AsyncMock) -> None:
    mock_db.fetch_one.return_value = {"id": "test-1"}

    result = await task_queue.create_task(
        task_id="test-1",
        title="Test Task",
        source="github-issues",
        source_ref="42",
        repository="test-repo",
        pipeline="feature-pipeline",
        context={"key": "value"},
    )

    assert result is True
    mock_db.fetch_one.assert_called_once()
    call_args = mock_db.fetch_one.call_args
    assert "INSERT INTO tasks" in call_args[0][0]
    params = call_args[0][1]
    assert params["id"] == "test-1"
    assert params["title"] == "Test Task"
    assert json.loads(params["context"]) == {"key": "value"}


@pytest.mark.asyncio
async def test_create_task_already_exists(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_one.return_value = None

    result = await task_queue.create_task(
        task_id="dup-1",
        title="Dup",
        source="test",
        source_ref="",
        repository="repo",
        pipeline="feature-pipeline",
    )

    assert result is False


@pytest.mark.asyncio
async def test_get_next_task_found(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_one.return_value = {
        "id": "task-1",
        "title": "Next Task",
        "pipeline": "pr-review-pipeline",
        "repository": "test-repo",
        "source": "github-prs",
        "source_ref": "5",
        "status": "queued",
        "priority": 50,
        "initial_context": {},
        "created_at": None,
        "updated_at": None,
        "started_at": None,
        "completed_at": None,
        "assigned_agent": None,
        "current_stage": 0,
        "retry_count": 0,
        "error_message": None,
    }

    task = await task_queue.get_next_task()
    assert task is not None
    assert task.id == "task-1"
    assert task.status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_get_next_task_empty(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_one.return_value = None
    task = await task_queue.get_next_task()
    assert task is None


@pytest.mark.asyncio
async def test_update_task_status_executing(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.update_task_status("task-1", TaskStatus.EXECUTING)

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "started_at = NOW()" in sql
    assert "error_message = NULL" in sql


@pytest.mark.asyncio
async def test_update_task_status_completed(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.update_task_status("task-1", TaskStatus.COMPLETED)

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "completed_at = NOW()" in sql


@pytest.mark.asyncio
async def test_task_exists_true(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_val.return_value = 1
    assert await task_queue.task_exists("task-1") is True


@pytest.mark.asyncio
async def test_task_exists_false(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_val.return_value = 0
    assert await task_queue.task_exists("task-1") is False


@pytest.mark.asyncio
async def test_fail_task(task_queue: TaskQueue, mock_db: AsyncMock) -> None:
    await task_queue.fail_task("task-1", "something broke")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "retry_count = retry_count + 1" in sql
    assert params["error"] == "something broke"
    assert params["max_retries"] == 3


@pytest.mark.asyncio
async def test_complete_task(task_queue: TaskQueue, mock_db: AsyncMock) -> None:
    await task_queue.complete_task("task-1")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "status = 'completed'" in sql


@pytest.mark.asyncio
async def test_store_stage_output(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    output = {"summary": "looks good", "issues": []}
    await task_queue.store_stage_output("task-1", 2, "review", "reviewer-agent", output)

    # Two calls: INSERT stage + UPDATE task current_stage
    assert mock_db.execute.call_count == 2

    # First call: insert stage
    first_sql = mock_db.execute.call_args_list[0][0][0]
    assert "INSERT INTO stages" in first_sql

    # Second call: advance current_stage
    second_params = mock_db.execute.call_args_list[1][0][1]
    assert second_params["next_stage"] == 3


@pytest.mark.asyncio
async def test_assign_agent(task_queue: TaskQueue, mock_db: AsyncMock) -> None:
    await task_queue.assign_agent("task-1", "analyzer-agent")

    call_args = mock_db.execute.call_args
    params = call_args[0][1]
    assert params["agent"] == "analyzer-agent"
    assert params["id"] == "task-1"


@pytest.mark.asyncio
async def test_get_timed_out_tasks(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_all.return_value = [{"id": "old-1"}, {"id": "old-2"}]
    result = await task_queue.get_timed_out_tasks(timeout_minutes=90)
    assert result == ["old-1", "old-2"]


@pytest.mark.asyncio
async def test_update_poll_state(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.update_poll_state("github-tasks", "2026-03-16T12:00:00Z")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "INSERT INTO poll_state" in sql


@pytest.mark.asyncio
async def test_get_poll_cursor_found(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_val.return_value = "2026-03-16T12:00:00Z"
    result = await task_queue.get_poll_cursor("github-tasks")
    assert result == "2026-03-16T12:00:00Z"


@pytest.mark.asyncio
async def test_get_poll_cursor_not_found(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_val.return_value = None
    result = await task_queue.get_poll_cursor("new-poller")
    assert result == ""


@pytest.mark.asyncio
async def test_checkpoint_pipeline(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.checkpoint_pipeline("task-1", 3, {"branch": "feature/x"})

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "pipeline_checkpoints" in sql
    params = call_args[0][1]
    assert params["stage"] == 3


@pytest.mark.asyncio
async def test_delete_checkpoint(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.delete_checkpoint("task-1")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "DELETE FROM pipeline_checkpoints" in sql


@pytest.mark.asyncio
async def test_record_stage_executing(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.record_stage_executing("task-1", 0, "analyze", "agent-1")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "status = 'executing'" in sql


@pytest.mark.asyncio
async def test_record_stage_failed(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.record_stage_failed("task-1", 0, "timeout")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "status = 'failed'" in sql


@pytest.mark.asyncio
async def test_record_stage_skipped(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.record_stage_skipped("task-1", 2, "docs")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "status = 'skipped'" in sql


@pytest.mark.asyncio
async def test_create_pending_stages(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    stages = [
        {"category": "analyze"},
        {"category": "implementation"},
        {"category": "test"},
    ]
    await task_queue.create_pending_stages("task-1", stages)

    assert mock_db.execute.call_count == 3


@pytest.mark.asyncio
async def test_update_task_status_pending(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Pending status uses the generic update branch."""
    await task_queue.update_task_status("task-1", TaskStatus.PENDING)

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    assert "status = %(status)s" in sql
    assert "started_at" not in sql
    assert "completed_at" not in sql


@pytest.mark.asyncio
async def test_get_task_found(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """get_task returns a Task model when found."""
    mock_db.fetch_one.return_value = {
        "id": "task-99",
        "title": "Test",
        "status": "pending",
        "priority": 50,
        "source": "github-issues",
        "source_ref": "10",
        "pipeline": "feature-pipeline",
        "repository": "repo",
        "initial_context": {},
        "created_at": None,
        "updated_at": None,
        "started_at": None,
        "completed_at": None,
        "assigned_agent": None,
        "current_stage": 0,
        "retry_count": 0,
        "error_message": None,
    }

    task = await task_queue.get_task("task-99")
    assert task is not None
    assert task.id == "task-99"
    assert task.title == "Test"


@pytest.mark.asyncio
async def test_get_task_not_found(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """get_task returns None when not found."""
    mock_db.fetch_one.return_value = None
    task = await task_queue.get_task("missing")
    assert task is None


@pytest.mark.asyncio
async def test_get_task_context_string(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """get_task_context parses JSON string result."""
    mock_db.fetch_val.return_value = '{"key": "value"}'
    ctx = await task_queue.get_task_context("task-1")
    assert ctx == {"key": "value"}


@pytest.mark.asyncio
async def test_get_task_context_dict(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """get_task_context returns dict result directly."""
    mock_db.fetch_val.return_value = {"key": "value"}
    ctx = await task_queue.get_task_context("task-1")
    assert ctx == {"key": "value"}


@pytest.mark.asyncio
async def test_get_task_context_none(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """get_task_context returns None when no result."""
    mock_db.fetch_val.return_value = None
    ctx = await task_queue.get_task_context("task-1")
    assert ctx is None


@pytest.mark.asyncio
async def test_get_checkpoint(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """get_checkpoint returns the checkpoint row."""
    mock_db.fetch_one.return_value = {
        "task_id": "task-1",
        "last_completed_stage": 2,
        "checkpoint_data": {},
        "created_at": None,
    }
    result = await task_queue.get_checkpoint("task-1")
    assert result is not None
    assert result["last_completed_stage"] == 2

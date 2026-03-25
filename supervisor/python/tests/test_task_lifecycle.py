"""Tests for task lifecycle operations: retry, rerun, close."""

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


# --- retry_task ---


@pytest.mark.asyncio
async def test_retry_task_resets_stage_and_task(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """retry_task resets the latest failed stage and the task to pending."""
    await task_queue.retry_task("task-1")

    assert mock_db.execute.call_count == 2

    # First call: reset latest failed/rate_limited stage
    stage_sql = mock_db.execute.call_args_list[0][0][0]
    assert "UPDATE stages" in stage_sql
    assert "status = 'pending'" in stage_sql
    assert "error_message = NULL" in stage_sql
    assert "structured_output = NULL" in stage_sql
    assert "raw_output = NULL" in stage_sql
    assert "live_output = NULL" in stage_sql
    assert "IN ('failed', 'rate_limited')" in stage_sql

    # Second call: reset task to pending
    task_sql = mock_db.execute.call_args_list[1][0][0]
    task_params = mock_db.execute.call_args_list[1][0][1]
    assert "status = 'pending'" in task_sql
    assert "error_message = NULL" in task_sql
    assert task_params["id"] == "task-1"


# --- rerun_task ---


@pytest.mark.asyncio
async def test_rerun_task_creates_new_task(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """rerun_task creates a new task referencing the original."""
    mock_db.fetch_val.return_value = 0  # no existing reruns
    mock_db.fetch_one.return_value = {
        "id": "task-1",
        "title": "Original Task",
        "source": "github-issues",
        "source_ref": "issue-42",
        "repository": "test-repo",
        "pipeline": "feature-pipeline",
        "initial_context": {"key": "value"},
    }

    new_id = await task_queue.rerun_task("task-1")

    # ID uses a UUID-based suffix: issue-42-rerun-<8hex>
    assert new_id.startswith("issue-42-rerun-")
    assert len(new_id.split("rerun-")[1]) == 8  # short UUID hex
    mock_db.execute.assert_called_once()
    call_sql = mock_db.execute.call_args[0][0]
    call_params = mock_db.execute.call_args[0][1]
    assert "INSERT INTO tasks" in call_sql
    assert "parent_task_id" in call_sql
    assert call_params["new_id"] == new_id
    assert call_params["parent_id"] == "task-1"
    assert call_params["title"] == "Original Task"
    assert json.loads(call_params["context"]) == {"key": "value"}


@pytest.mark.asyncio
async def test_rerun_task_increments_counter(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """rerun_task uses existing rerun count to generate unique ID."""
    mock_db.fetch_val.return_value = 3  # 3 existing reruns
    mock_db.fetch_one.return_value = {
        "id": "task-1",
        "title": "Task",
        "source": "test",
        "source_ref": "ref-1",
        "repository": "repo",
        "pipeline": "pipeline",
        "initial_context": None,
    }

    new_id = await task_queue.rerun_task("task-1")
    assert new_id.startswith("ref-1-rerun-")
    assert len(new_id.split("rerun-")[1]) == 8


@pytest.mark.asyncio
async def test_rerun_task_not_found(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """rerun_task raises ValueError when task not found."""
    mock_db.fetch_val.return_value = 0
    mock_db.fetch_one.return_value = None

    with pytest.raises(ValueError, match="Task task-missing not found"):
        await task_queue.rerun_task("task-missing")


@pytest.mark.asyncio
async def test_rerun_task_fallback_to_task_id(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """rerun_task falls back to task_id when source_ref is empty."""
    mock_db.fetch_val.return_value = 0
    mock_db.fetch_one.return_value = {
        "id": "task-1",
        "title": "Task",
        "source": "test",
        "source_ref": "",
        "repository": "repo",
        "pipeline": "pipeline",
        "initial_context": None,
    }

    new_id = await task_queue.rerun_task("task-1")
    assert new_id.startswith("task-1-rerun-")
    assert len(new_id.split("rerun-")[1]) == 8


# --- close_task ---


@pytest.mark.asyncio
async def test_close_task_sets_status_and_deletes_checkpoint(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """close_task updates status to closed and deletes checkpoint."""
    await task_queue.close_task("task-1")

    assert mock_db.execute.call_count == 2

    # First: update status
    status_sql = mock_db.execute.call_args_list[0][0][0]
    assert "status = 'closed'" in status_sql

    # Second: delete checkpoint
    cp_sql = mock_db.execute.call_args_list[1][0][0]
    assert "DELETE FROM pipeline_checkpoints" in cp_sql


# --- store_pr_info ---


@pytest.mark.asyncio
async def test_store_pr_info(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.store_pr_info("task-1", 42, "aquarco/task-1")

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "pr_number" in sql
    assert "branch_name" in sql
    assert params["pr_number"] == 42
    assert params["branch"] == "aquarco/task-1"
    assert params["id"] == "task-1"


# --- get_tasks_pending_close ---


@pytest.mark.asyncio
async def test_get_tasks_pending_close(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_all.return_value = [
        {"id": "task-1", "pr_number": 10, "repository": "repo-a"},
        {"id": "task-2", "pr_number": 20, "repository": "repo-b"},
    ]

    result = await task_queue.get_tasks_pending_close()

    assert len(result) == 2
    assert result[0]["id"] == "task-1"
    assert result[0]["pr_number"] == 10
    assert result[1]["repository"] == "repo-b"

    sql = mock_db.fetch_all.call_args[0][0]
    assert "status = 'completed'" in sql
    assert "pr_number IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_get_tasks_pending_close_empty(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    mock_db.fetch_all.return_value = []
    result = await task_queue.get_tasks_pending_close()
    assert result == []


# --- create_rerun_stage ---


@pytest.mark.asyncio
async def test_create_rerun_stage(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    await task_queue.create_rerun_stage(
        "task-1", 2, "review", "review-agent",
        "2:review:review-agent", 1, 3,
    )

    call_args = mock_db.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "INSERT INTO stages" in sql
    assert params["run"] == 3
    assert params["stage_key"] == "2:review:review-agent"
    assert params["iteration"] == 1

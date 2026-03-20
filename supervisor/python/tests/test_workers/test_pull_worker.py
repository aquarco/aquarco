"""Tests for pull worker."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.workers.pull_worker import PullWorker


@pytest.mark.asyncio
async def test_pull_ready_repos_empty_list() -> None:
    """Does nothing when there are no ready repositories."""
    db = AsyncMock(spec=Database)
    db.fetch_all = AsyncMock(return_value=[])

    worker = PullWorker(db)
    await worker.pull_ready_repos()

    db.execute.assert_not_called()
    db.fetch_val.assert_not_called()


@pytest.mark.asyncio
async def test_pull_ready_repos_skips_missing_git_dir(tmp_path: Any) -> None:
    """Repos whose clone_dir has no .git folder are skipped before the active-pipeline check."""
    db = AsyncMock(spec=Database)
    db.fetch_all = AsyncMock(
        return_value=[
            {
                "name": "missing-repo",
                "clone_dir": str(tmp_path / "nonexistent"),
                "branch": "main",
            }
        ]
    )

    worker = PullWorker(db)
    await worker.pull_ready_repos()

    db.execute.assert_not_called()
    # No .git directory means we short-circuit before querying for active tasks
    db.fetch_val.assert_not_called()


@pytest.mark.asyncio
async def test_pull_ready_repos_pulls_and_updates_db(tmp_path: Any) -> None:
    """When .git exists and no active pipelines, git commands are run and DB is updated."""
    db = AsyncMock(spec=Database)

    repo_dir = tmp_path / "my-repo"
    git_dir = repo_dir / ".git"
    git_dir.mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[
            {
                "name": "my-repo",
                "clone_dir": str(repo_dir),
                "branch": "main",
            }
        ]
    )
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    sha_sequence = ["oldsha123", "", "", "newsha456"]

    with patch(
        "aquarco_supervisor.workers.pull_worker._run_git",
        new_callable=AsyncMock,
        side_effect=sha_sequence,
    ) as mock_git:
        worker = PullWorker(db)
        await worker.pull_ready_repos()

    # Should have called: rev-parse HEAD, fetch, reset, rev-parse HEAD
    assert mock_git.await_count == 4

    db.execute.assert_awaited_once()
    params = db.execute.await_args.args[1]
    assert params["name"] == "my-repo"
    assert params["sha"] == "newsha456"


@pytest.mark.asyncio
async def test_pull_ready_repos_no_db_update_on_same_sha(tmp_path: Any) -> None:
    """DB is still updated even when SHA hasn't changed (timestamp refresh)."""
    db = AsyncMock(spec=Database)

    repo_dir = tmp_path / "stable-repo"
    git_dir = repo_dir / ".git"
    git_dir.mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[
            {
                "name": "stable-repo",
                "clone_dir": str(repo_dir),
                "branch": "main",
            }
        ]
    )
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    same_sha = "abc123"
    sha_sequence = [same_sha, "", "", same_sha]

    with patch(
        "aquarco_supervisor.workers.pull_worker._run_git",
        new_callable=AsyncMock,
        side_effect=sha_sequence,
    ):
        worker = PullWorker(db)
        await worker.pull_ready_repos()

    # DB update still happens even when SHA is unchanged
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_pull_ready_repos_handles_git_error_gracefully(tmp_path: Any) -> None:
    """When git commands fail, the error is logged but other repos continue."""
    db = AsyncMock(spec=Database)

    repo1_dir = tmp_path / "broken-repo"
    repo1_dir.mkdir(parents=True)
    (repo1_dir / ".git").mkdir()

    repo2_dir = tmp_path / "healthy-repo"
    repo2_dir.mkdir(parents=True)
    (repo2_dir / ".git").mkdir()

    db.fetch_all = AsyncMock(
        return_value=[
            {"name": "broken-repo", "clone_dir": str(repo1_dir), "branch": "main"},
            {"name": "healthy-repo", "clone_dir": str(repo2_dir), "branch": "main"},
        ]
    )
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    call_count = 0

    async def mock_git(clone_dir: str, *args: Any) -> str:
        nonlocal call_count
        call_count += 1
        if "broken-repo" in clone_dir:
            raise RuntimeError("git fetch failed")
        return "sha-healthy"

    with patch(
        "aquarco_supervisor.workers.pull_worker._run_git",
        side_effect=mock_git,
    ):
        worker = PullWorker(db)
        await worker.pull_ready_repos()

    # Only the healthy repo should update the DB
    db.execute.assert_awaited_once()
    params = db.execute.await_args.args[1]
    assert params["name"] == "healthy-repo"


@pytest.mark.asyncio
async def test_pull_ready_repos_multiple_repos(tmp_path: Any) -> None:
    """All ready repos with no active pipelines are pulled in sequence."""
    db = AsyncMock(spec=Database)

    repos = []
    for name in ["repo-a", "repo-b", "repo-c"]:
        d = tmp_path / name
        d.mkdir(parents=True)
        (d / ".git").mkdir()
        repos.append({"name": name, "clone_dir": str(d), "branch": "main"})

    db.fetch_all = AsyncMock(return_value=repos)
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    # 4 git calls per repo: rev-parse, fetch, reset, rev-parse
    git_responses = ["sha1", "", "", "sha1"] * 3

    with patch(
        "aquarco_supervisor.workers.pull_worker._run_git",
        new_callable=AsyncMock,
        side_effect=git_responses,
    ):
        worker = PullWorker(db)
        await worker.pull_ready_repos()

    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_pull_skipped_when_active_pipeline_uses_repo(tmp_path: Any) -> None:
    """Pull is skipped for a repo that has queued or executing tasks."""
    db = AsyncMock(spec=Database)

    repo_dir = tmp_path / "busy-repo"
    git_dir = repo_dir / ".git"
    git_dir.mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[
            {
                "name": "busy-repo",
                "clone_dir": str(repo_dir),
                "branch": "main",
            }
        ]
    )
    db.execute = AsyncMock()
    # Simulate one executing task referencing this repo
    db.fetch_val = AsyncMock(return_value=1)

    with patch(
        "aquarco_supervisor.workers.pull_worker._run_git",
        new_callable=AsyncMock,
    ) as mock_git:
        worker = PullWorker(db)
        await worker.pull_ready_repos()

    # Git must not be touched and DB must not be updated
    mock_git.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_pull_active_pipeline_check_uses_correct_query(tmp_path: Any) -> None:
    """The active-pipeline check passes the repository name and correct statuses."""
    db = AsyncMock(spec=Database)

    repo_dir = tmp_path / "check-repo"
    (repo_dir / ".git").mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[
            {
                "name": "check-repo",
                "clone_dir": str(repo_dir),
                "branch": "main",
            }
        ]
    )
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    with patch(
        "aquarco_supervisor.workers.pull_worker._run_git",
        new_callable=AsyncMock,
        side_effect=["sha-old", "", "", "sha-new"],
    ):
        worker = PullWorker(db)
        await worker.pull_ready_repos()

    # Verify fetch_val was called and the params contain the repo name
    db.fetch_val.assert_awaited_once()
    call_params = db.fetch_val.await_args.args[1]
    assert call_params["name"] == "check-repo"

    # Verify the query string targets the right statuses
    query_str = db.fetch_val.await_args.args[0]
    assert "queued" in query_str
    assert "executing" in query_str


@pytest.mark.asyncio
async def test_pull_idle_repo_not_blocked_by_other_active_repo(tmp_path: Any) -> None:
    """An idle repo is still pulled even when a different repo has active tasks."""
    db = AsyncMock(spec=Database)

    busy_dir = tmp_path / "busy-repo"
    (busy_dir / ".git").mkdir(parents=True)
    idle_dir = tmp_path / "idle-repo"
    (idle_dir / ".git").mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[
            {"name": "busy-repo", "clone_dir": str(busy_dir), "branch": "main"},
            {"name": "idle-repo", "clone_dir": str(idle_dir), "branch": "main"},
        ]
    )
    db.execute = AsyncMock()
    # busy-repo has 2 active tasks; idle-repo has none
    db.fetch_val = AsyncMock(side_effect=[2, 0])

    with patch(
        "aquarco_supervisor.workers.pull_worker._run_git",
        new_callable=AsyncMock,
        side_effect=["sha-old", "", "", "sha-new"],
    ):
        worker = PullWorker(db)
        await worker.pull_ready_repos()

    # Only idle-repo should have been updated
    db.execute.assert_awaited_once()
    params = db.execute.await_args.args[1]
    assert params["name"] == "idle-repo"

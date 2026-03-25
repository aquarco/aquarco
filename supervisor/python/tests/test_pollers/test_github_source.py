"""Tests for GitHub source poller."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.pollers.github_source import (
    GitHubSourcePoller,
)
from aquarco_supervisor.task_queue import TaskQueue
from aquarco_supervisor.utils import url_to_slug as _url_to_slug

SAMPLE_REPO = {
    "name": "test-repo",
    "url": "git@github.com:test/repo.git",
    "branch": "main",
    "clone_dir": "/tmp/test/repos/test-repo",
    "pollers": ["github-tasks", "github-source"],
}


def test_url_to_slug_https() -> None:
    assert _url_to_slug("https://github.com/owner/repo.git") == "owner/repo"
    assert _url_to_slug("https://github.com/owner/repo") == "owner/repo"


def test_url_to_slug_ssh() -> None:
    assert _url_to_slug("git@github.com:owner/repo.git") == "owner/repo"


def test_url_to_slug_invalid() -> None:
    assert _url_to_slug("not-a-url") is None


# --- GitHubSourcePoller._process_pr ---

@pytest.mark.asyncio
async def test_process_pr_skips_aquarco_branches(sample_config: Any) -> None:
    """PRs from aquarco/ branches are not processed."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    pr = {
        "number": 42,
        "title": "Automated PR",
        "headRefName": "aquarco/task-001/my-feature",
        "baseRefName": "main",
        "url": "https://github.com/owner/repo/pull/42",
        "updatedAt": "2024-01-01T00:00:00Z",
        "createdAt": "2024-01-01T00:00:00Z",
        "additions": 10,
        "deletions": 2,
        "changedFiles": 3,
    }

    result = await poller._process_pr(pr, "test-repo", "owner/repo", "pr_opened")
    assert result == 0
    mock_tq.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_process_pr_skips_when_no_triggers_for_event(sample_config: Any) -> None:
    """If the event type has no trigger categories, nothing is created."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._triggers = {}

    pr = {
        "number": 5,
        "title": "Some PR",
        "headRefName": "feature/xyz",
        "baseRefName": "main",
        "url": "https://github.com/owner/repo/pull/5",
        "updatedAt": "2024-01-01T00:00:00Z",
        "createdAt": "2024-01-01T00:00:00Z",
        "additions": 1,
        "deletions": 0,
        "changedFiles": 1,
    }

    result = await poller._process_pr(pr, "test-repo", "owner/repo", "pr_merged")
    assert result == 0
    mock_tq.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_process_pr_creates_task_for_pr_opened(sample_config: Any) -> None:
    """A new PR event creates one task per trigger category."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._triggers = {"pr_opened": ["review"]}

    pr = {
        "number": 7,
        "title": "Add feature X",
        "headRefName": "feature/x",
        "baseRefName": "main",
        "url": "https://github.com/owner/repo/pull/7",
        "updatedAt": "2024-06-01T00:00:00Z",
        "createdAt": "2024-06-01T00:00:00Z",
        "additions": 50,
        "deletions": 5,
        "changedFiles": 4,
    }

    result = await poller._process_pr(pr, "test-repo", "owner/repo", "pr_opened")
    assert result == 1
    mock_tq.create_task.assert_awaited_once()

    call_kwargs = mock_tq.create_task.await_args.kwargs
    assert call_kwargs["task_id"] == "github-pr-test-repo-7-review"
    assert call_kwargs["pipeline"] == "review"
    assert call_kwargs["source"] == "github-prs"
    assert call_kwargs["source_ref"] == "7"


@pytest.mark.asyncio
async def test_process_pr_skips_existing_task(sample_config: Any) -> None:
    """When the task already exists, create_task is not called."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=True)
    mock_tq.create_task = AsyncMock(return_value=False)
    mock_db = AsyncMock(spec=Database)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._triggers = {"pr_opened": ["review"]}

    pr = {
        "number": 8,
        "title": "Duplicate PR",
        "headRefName": "feature/dup",
        "baseRefName": "main",
        "url": "https://github.com/owner/repo/pull/8",
        "updatedAt": "2024-06-01T00:00:00Z",
        "createdAt": "2024-06-01T00:00:00Z",
        "additions": 10,
        "deletions": 0,
        "changedFiles": 1,
    }

    result = await poller._process_pr(pr, "test-repo", "owner/repo", "pr_opened")
    assert result == 0
    mock_tq.create_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_pr_updated_uses_timestamped_task_id(sample_config: Any) -> None:
    """For pr_updated events, the task ID includes a timestamp suffix."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._triggers = {"pr_updated": ["review"]}

    pr = {
        "number": 9,
        "title": "Updated PR",
        "headRefName": "feature/y",
        "baseRefName": "main",
        "url": "https://github.com/owner/repo/pull/9",
        "updatedAt": "2024-06-01T00:00:00Z",
        "createdAt": "2024-05-01T00:00:00Z",
        "additions": 5,
        "deletions": 1,
        "changedFiles": 1,
    }

    result = await poller._process_pr(pr, "test-repo", "owner/repo", "pr_updated")
    assert result == 1

    call_kwargs = mock_tq.create_task.await_args.kwargs
    assert "github-pr-test-repo-9-review-" in call_kwargs["task_id"]


# --- GitHubSourcePoller._poll_prs ---

@pytest.mark.asyncio
async def test_poll_prs_filters_old_prs(sample_config: Any) -> None:
    """PRs with updatedAt and createdAt <= cursor are skipped."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    cursor = "2024-06-01T00:00:00+00:00"
    old_prs = [
        {
            "number": 1,
            "title": "Old PR",
            "headRefName": "feature/old",
            "baseRefName": "main",
            "url": "https://github.com/owner/repo/pull/1",
            "updatedAt": "2024-05-01T00:00:00Z",
            "createdAt": "2024-04-01T00:00:00Z",
            "additions": 0,
            "deletions": 0,
            "changedFiles": 0,
        }
    ]

    with patch(
        "aquarco_supervisor.pollers.github_source._gh_list_prs",
        new_callable=AsyncMock,
        return_value=old_prs,
    ):
        result = await poller._poll_prs("test-repo", "owner/repo", cursor)

    assert result == 0
    mock_tq.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_poll_prs_processes_new_prs(sample_config: Any) -> None:
    """PRs newer than cursor are processed."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._triggers = {"pr_opened": ["review"]}

    cursor = "2024-01-01T00:00:00+00:00"
    new_prs = [
        {
            "number": 10,
            "title": "Fresh PR",
            "headRefName": "feature/fresh",
            "baseRefName": "main",
            "url": "https://github.com/owner/repo/pull/10",
            "updatedAt": "2024-06-01T00:00:00Z",
            "createdAt": "2024-06-01T00:00:00Z",
            "additions": 20,
            "deletions": 0,
            "changedFiles": 2,
        }
    ]

    with patch(
        "aquarco_supervisor.pollers.github_source._gh_list_prs",
        new_callable=AsyncMock,
        return_value=new_prs,
    ):
        result = await poller._poll_prs("test-repo", "owner/repo", cursor)

    assert result == 1


# --- GitHubSourcePoller._poll_commits ---

# Valid 40-hex SHAs required by the new _validate_sha guard
_SHA_A = "aaa1112223331122334455667788990011223344"
_SHA_B = "bbb4445556661122334455667788990011223344"
_SHA_NEW = "abcdef1234567890abcdef1234567890abcdef12"
_SHA_OLD = "111111aaaaaabbbbbbcccccc222222ddddddeeee"


@pytest.mark.asyncio
async def test_poll_commits_skips_pipeline_branch_subjects(
    sample_config: Any, tmp_path: Any
) -> None:
    """Commits with 'aquarco/' in subject are filtered; remaining commits form one push task."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    # Two commits: one normal, one with aquarco/ in the subject (pipeline merge)
    commit_lines = (
        f"{_SHA_A}\tAdd feature\tDev\t2024-06-01T00:00:00+00:00\n"
        f"{_SHA_B}\tMerge pull request from borissuska/aquarco/github-commit-aquarco-bc1db35/review\tDev\t2024-06-01T00:00:00+00:00"
    )

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            return commit_lines
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, new_sha = await poller._poll_commits(
            "test-repo",
            str(tmp_path / "repo"),
            "2024-01-01T00:00:00Z",
        )

    # One push task created (the aquarco/ commit is filtered but the normal one remains)
    assert count == 1
    assert new_sha == _SHA_NEW
    call_kwargs = mock_tq.create_task.await_args.kwargs
    assert call_kwargs["task_id"] == f"github-push-test-repo-{_SHA_NEW[:12]}"


@pytest.mark.asyncio
async def test_poll_commits_skips_all_aquarco_subjects(
    sample_config: Any, tmp_path: Any
) -> None:
    """When all commits have aquarco/ in subject, no task is created but new_sha is returned."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_line = f"{_SHA_A}\taquarco/github-commit-repo-abc/review\tBot\t2024-06-01T00:00:00+00:00"

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            return commit_line
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, new_sha = await poller._poll_commits(
            "test-repo",
            str(tmp_path / "repo"),
            "2024-01-01T00:00:00Z",
        )

    assert count == 0
    assert new_sha == _SHA_NEW
    mock_tq.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_poll_commits_uses_no_merges_flag(
    sample_config: Any, tmp_path: Any
) -> None:
    """git log is called with --no-merges to exclude merge commits."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)
    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    captured_args: list = []

    async def mock_run_git(clone_dir: str, *args: str, **kwargs: Any) -> str:
        captured_args.append(args)
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            return ""
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        await poller._poll_commits(
            "test-repo",
            str(tmp_path / "repo"),
            "2024-01-01T00:00:00Z",
        )

    # Find the log call and verify --no-merges is present
    log_call = [a for a in captured_args if a[0] == "log"]
    assert len(log_call) == 1
    assert "--no-merges" in log_call[0]


@pytest.mark.asyncio
async def test_poll_commits_skips_missing_clone_dir(sample_config: Any, tmp_path: Any) -> None:
    """If the .git directory does not exist, returns (0, None)."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    result = await poller._poll_commits(
        "test-repo",
        str(tmp_path / "nonexistent"),
        "2024-01-01T00:00:00Z",
    )

    assert result == (0, None)


@pytest.mark.asyncio
async def test_poll_commits_creates_push_task_for_new_commits(
    sample_config: Any, tmp_path: Any
) -> None:
    """Creates a single push task when new commits arrive."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_line = f"{_SHA_A}\tAdd new feature\tJohn Doe\t2024-06-01T00:00:00+00:00"

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            return commit_line
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, new_sha = await poller._poll_commits(
            "test-repo",
            str(tmp_path / "repo"),
            "2024-01-01T00:00:00Z",
        )

    assert count == 1
    assert new_sha == _SHA_NEW
    call_kwargs = mock_tq.create_task.await_args.kwargs
    assert call_kwargs["task_id"] == f"github-push-test-repo-{_SHA_NEW[:12]}"
    assert call_kwargs["pipeline"] == "pr-review-pipeline"
    assert call_kwargs["source"] == "github-commits"


@pytest.mark.asyncio
async def test_poll_commits_skips_existing_push_task(
    sample_config: Any, tmp_path: Any
) -> None:
    """Does not create a push task when one already exists for the same head sha."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=True)
    mock_tq.create_task = AsyncMock(return_value=False)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_line = f"{_SHA_A}\tOld commit\tJane Doe\t2024-06-01T00:00:00+00:00"

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            return commit_line
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, _ = await poller._poll_commits(
            "test-repo",
            str(tmp_path / "repo"),
            "2024-01-01T00:00:00Z",
        )

    assert count == 0
    mock_tq.create_task.assert_not_awaited()


# --- GitHubSourcePoller.poll ---

@pytest.mark.asyncio
async def test_poll_uses_fallback_cursor_when_none(sample_config: Any) -> None:
    """When no cursor exists, poll uses a 1-hour lookback."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value=None)
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[SAMPLE_REPO])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    # _poll_commits now returns (tasks_created, new_head_sha)
    poller._poll_prs = AsyncMock(return_value=0)
    poller._poll_commits = AsyncMock(return_value=(0, None))

    await poller.poll()

    poller._poll_prs.assert_awaited_once()
    poller._poll_commits.assert_awaited_once()
    mock_tq.update_poll_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_accumulates_total_created(sample_config: Any) -> None:
    """poll() sums up tasks created across repos."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value="2024-01-01T00:00:00Z")
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[SAMPLE_REPO])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._poll_prs = AsyncMock(return_value=2)
    # _poll_commits returns (tasks_created, new_head_sha)
    poller._poll_commits = AsyncMock(return_value=(3, None))

    total = await poller.poll()

    assert total == 5


@pytest.mark.asyncio
async def test_poll_handles_pr_poll_exception(sample_config: Any) -> None:
    """Exceptions in _poll_prs do not crash poll() — they are logged."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value="2024-01-01T00:00:00Z")
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[SAMPLE_REPO])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._poll_prs = AsyncMock(side_effect=RuntimeError("gh cli failed"))
    # _poll_commits returns (tasks_created, new_head_sha)
    poller._poll_commits = AsyncMock(return_value=(1, None))

    total = await poller.poll()

    assert total == 1
    mock_tq.update_poll_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_handles_commit_poll_exception(sample_config: Any) -> None:
    """Exceptions in _poll_commits do not crash poll()."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value="2024-01-01T00:00:00Z")
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[SAMPLE_REPO])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._poll_prs = AsyncMock(return_value=1)
    poller._poll_commits = AsyncMock(side_effect=RuntimeError("git error"))

    total = await poller.poll()

    assert total == 1
    mock_tq.update_poll_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_commits_falls_back_to_main(
    sample_config: Any, tmp_path: Any
) -> None:
    """When rev-parse --abbrev-ref fails, default branch falls back to 'main'."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_line = f"{_SHA_A}\tFix bug\tDev\t2024-06-01T00:00:00+00:00"

    async def mock_run_git(clone_dir: str, *args: str, **kwargs: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            raise RuntimeError("not found")
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            assert "origin/main" in args[1]
            return commit_line
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, _ = await poller._poll_commits(
            "test-repo",
            str(tmp_path / "repo"),
            "2024-01-01T00:00:00Z",
        )

    assert count == 1


@pytest.mark.asyncio
async def test_poll_commits_empty_output(
    sample_config: Any, tmp_path: Any
) -> None:
    """Empty git log output returns (0, new_head_sha)."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)
    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, new_sha = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z",
        )

    assert count == 0
    assert new_sha == _SHA_NEW


@pytest.mark.asyncio
async def test_poll_commits_malformed_line_skipped(
    sample_config: Any, tmp_path: Any
) -> None:
    """Lines with fewer than 4 tab-separated fields are skipped; (0, new_sha) returned."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)
    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            return "short\tline"
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, _ = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z",
        )

    assert count == 0
    mock_tq.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_poll_commits_skips_aquarco_subject(
    sample_config: Any, tmp_path: Any
) -> None:
    """aquarco/ pipeline commit is filtered; remaining normal commit forms a push task."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    # One pipeline commit (should be skipped) and one normal commit
    commit_lines = (
        f"{_SHA_A}\tMerge aquarco/task-1/feature\tBot\t2024-06-01T00:00:00+00:00\n"
        f"{_SHA_B}\tFix actual bug\tDev\t2024-06-01T00:00:00+00:00"
    )

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            return commit_lines
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, _ = await poller._poll_commits(
            "test-repo",
            str(tmp_path / "repo"),
            "2024-01-01T00:00:00Z",
        )

    # One push task for the non-aquarco commit
    assert count == 1
    call_kwargs = mock_tq.create_task.await_args.kwargs
    assert call_kwargs["task_id"] == f"github-push-test-repo-{_SHA_NEW[:12]}"


@pytest.mark.asyncio
async def test_poll_commits_skips_all_aquarco_subjects_v2(
    sample_config: Any, tmp_path: Any
) -> None:
    """When all commits have aquarco/ in subject, no task is created."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_lines = (
        f"{_SHA_A}\tfeat: Review aquarco/task-1\tBot\t2024-06-01T00:00:00+00:00"
    )

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return _SHA_NEW
        if args[0] == "log":
            return commit_lines
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, _ = await poller._poll_commits(
            "test-repo",
            str(tmp_path / "repo"),
            "2024-01-01T00:00:00Z",
        )

    assert count == 0
    mock_tq.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_poll_no_repos_from_db(sample_config: Any) -> None:
    """When DB returns no repos, nothing is polled."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value="2024-01-01T00:00:00Z")
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._poll_prs = AsyncMock(return_value=0)
    poller._poll_commits = AsyncMock(return_value=(0, None))

    total = await poller.poll()

    assert total == 0
    poller._poll_prs.assert_not_called()
    poller._poll_commits.assert_not_called()

"""Tests for the refactored push-based commit polling in GitHubSourcePoller.

These tests cover the new _poll_commits behaviour introduced in the last commit:
- Returns (int, str | None) instead of int
- Reads old_head_sha from poll_state DB table
- Uses SHA-based rev ranges instead of --since=cursor
- Batches all commits in a push into a single task (github-push-...)
- poll() collects head_sha_updates and merges them into update_poll_state state dict
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.pollers.github_source import GitHubSourcePoller
from aquarco_supervisor.task_queue import TaskQueue

SAMPLE_REPO = {
    "name": "test-repo",
    "url": "git@github.com:test/repo.git",
    "branch": "main",
    "clone_dir": "/tmp/test/repos/test-repo",
    "pollers": ["github-source"],
}

NEW_SHA = "abcdef1234567890abcdef1234567890abcdef12"
OLD_SHA = "111111aaaaaabbbbbbcccccc222222ddddddeeee"


# ---------------------------------------------------------------------------
# _poll_commits return type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_returns_tuple_when_no_git_dir(
    sample_config: Any, tmp_path: Any
) -> None:
    """When .git directory is missing, returns (0, None) not just 0."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    result = await poller._poll_commits(
        "test-repo", str(tmp_path / "nonexistent"), "2024-01-01T00:00:00Z"
    )

    assert result == (0, None)


# ---------------------------------------------------------------------------
# SHA-based short-circuit: unchanged head
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_returns_zero_none_when_sha_unchanged(
    sample_config: Any, tmp_path: Any
) -> None:
    """When old_head_sha matches new_head_sha, no task is created and (0, None) is returned."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": {f"head_sha:test-repo": NEW_SHA}}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        result = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    assert result == (0, None)
    mock_tq.create_task.assert_not_called()


# ---------------------------------------------------------------------------
# SHA-based rev range when old_head_sha exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_uses_sha_range_when_old_head_sha_known(
    sample_config: Any, tmp_path: Any
) -> None:
    """When old_head_sha is known, git log uses '<old>..<new>' and NOT --since."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": {f"head_sha:test-repo": OLD_SHA}}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    captured_args: list[tuple] = []

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        captured_args.append(args)
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return f"{NEW_SHA}\tAdd feature\tDev\t2024-06-01T00:00:00+00:00"
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    log_args = [a for a in captured_args if a[0] == "log"]
    assert len(log_args) == 1
    rev_range_arg = log_args[0][1]
    assert f"{OLD_SHA}..origin/main" == rev_range_arg
    # --since must NOT be present when old_head_sha is known
    assert not any("--since" in str(a) for a in log_args[0])


@pytest.mark.asyncio
async def test_poll_commits_uses_since_when_no_old_head_sha(
    sample_config: Any, tmp_path: Any
) -> None:
    """When no old_head_sha is stored, git log uses the cursor via --since."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    # No state row at all
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    captured_args: list[tuple] = []
    cursor = "2024-01-01T00:00:00Z"

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        captured_args.append(args)
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return f"{NEW_SHA}\tAdd feature\tDev\t2024-06-01T00:00:00+00:00"
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), cursor
        )

    log_args = [a for a in captured_args if a[0] == "log"]
    assert len(log_args) == 1
    assert any(f"--since={cursor}" in str(a) for a in log_args[0])


# ---------------------------------------------------------------------------
# Batch task creation (single github-push-... task)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_creates_single_push_task_for_multiple_commits(
    sample_config: Any, tmp_path: Any
) -> None:
    """Multiple new commits produce exactly one task with id github-push-<repo>-<sha>."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": {f"head_sha:test-repo": OLD_SHA}}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_lines = (
        f"{NEW_SHA}\tAdd feature A\tAlice\t2024-06-02T00:00:00+00:00\n"
        "deadbeef1234deadbeef1234deadbeef12341234\tFix bug B\tBob\t2024-06-01T00:00:00+00:00"
    )

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return commit_lines
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, returned_sha = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    assert count == 1
    assert returned_sha == NEW_SHA
    mock_tq.create_task.assert_awaited_once()
    kw = mock_tq.create_task.await_args.kwargs
    assert kw["task_id"] == f"github-push-test-repo-{NEW_SHA[:12]}"
    assert kw["source"] == "github-commits"
    assert kw["source_ref"] == NEW_SHA
    assert kw["pipeline"] == "pr-review-pipeline"


@pytest.mark.asyncio
async def test_poll_commits_single_commit_title_uses_subject(
    sample_config: Any, tmp_path: Any
) -> None:
    """Single commit push uses the commit subject as the task title."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    subject = "Fix the critical regression in auth"
    commit_line = f"{NEW_SHA}\t{subject}\tAlice\t2024-06-01T00:00:00+00:00"

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return commit_line
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    kw = mock_tq.create_task.await_args.kwargs
    assert kw["title"] == f"Review push: {subject}"


@pytest.mark.asyncio
async def test_poll_commits_multi_commit_title_includes_count_and_range(
    sample_config: Any, tmp_path: Any
) -> None:
    """Multi-commit push title shows commit count and sha range."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": {f"head_sha:test-repo": OLD_SHA}}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_lines = (
        f"{NEW_SHA}\tCommit one\tAlice\t2024-06-02T00:00:00+00:00\n"
        "deadbeef1234deadbeef1234deadbeef12341234\tCommit two\tBob\t2024-06-01T00:00:00+00:00"
    )

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return commit_lines
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    kw = mock_tq.create_task.await_args.kwargs
    title = kw["title"]
    assert "2 commits" in title
    assert OLD_SHA[:8] in title
    assert NEW_SHA[:8] in title


# ---------------------------------------------------------------------------
# Context shape for push tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_context_contains_push_fields(
    sample_config: Any, tmp_path: Any
) -> None:
    """Task context includes push_old_sha, push_new_sha, commit_count, and commits list."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": {f"head_sha:test-repo": OLD_SHA}}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    sha2 = "deadbeef1234deadbeef1234deadbeef12341234"
    commit_lines = (
        f"{NEW_SHA}\tCommit one\tAlice\t2024-06-02T00:00:00+00:00\n"
        f"{sha2}\tCommit two\tBob\t2024-06-01T00:00:00+00:00"
    )

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return commit_lines
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    kw = mock_tq.create_task.await_args.kwargs
    ctx = kw["context"]
    assert ctx["push_old_sha"] == OLD_SHA
    assert ctx["push_new_sha"] == NEW_SHA
    assert ctx["commit_count"] == 2
    assert len(ctx["commits"]) == 2
    assert ctx["commits"][0]["sha"] == NEW_SHA
    assert ctx["commits"][0]["subject"] == "Commit one"
    assert ctx["commits"][0]["author"] == "Alice"


# ---------------------------------------------------------------------------
# Deduplication: existing push task is skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_skips_push_task_that_already_exists(
    sample_config: Any, tmp_path: Any
) -> None:
    """When a push task already exists for the new head sha, returns (0, new_head_sha)."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=True)
    mock_tq.create_task = AsyncMock(return_value=False)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": {f"head_sha:test-repo": OLD_SHA}}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_line = f"{NEW_SHA}\tAdd feature\tDev\t2024-06-01T00:00:00+00:00"

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return commit_line
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, returned_sha = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    assert count == 0
    assert returned_sha == NEW_SHA
    mock_tq.create_task.assert_not_awaited()


# ---------------------------------------------------------------------------
# Empty git log after head change: returns (0, new_head_sha)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_returns_new_sha_even_when_no_tasks_created(
    sample_config: Any, tmp_path: Any
) -> None:
    """Even when git log is empty, new_head_sha is returned so state is advanced."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": {f"head_sha:test-repo": OLD_SHA}}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return ""
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, returned_sha = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    assert count == 0
    assert returned_sha == NEW_SHA


@pytest.mark.asyncio
async def test_poll_commits_returns_new_sha_when_all_commits_are_pipeline_merges(
    sample_config: Any, tmp_path: Any
) -> None:
    """When all commits are aquarco/ pipeline merges, returns (0, new_head_sha)."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": {f"head_sha:test-repo": OLD_SHA}}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    commit_line = (
        f"{NEW_SHA}\tMerge aquarco/github-commit-repo-abc123/review\tBot\t2024-06-01T00:00:00+00:00"
    )

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return commit_line
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, returned_sha = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    assert count == 0
    assert returned_sha == NEW_SHA
    mock_tq.create_task.assert_not_called()


# ---------------------------------------------------------------------------
# State data: JSON string vs dict handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_parses_state_data_as_json_string(
    sample_config: Any, tmp_path: Any
) -> None:
    """state_data returned as a JSON string is correctly parsed."""
    import json

    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)
    # DB returns state_data as a raw JSON string (some drivers do this)
    state_dict = {f"head_sha:test-repo": OLD_SHA}
    mock_db.fetch_one = AsyncMock(
        return_value={"state_data": json.dumps(state_dict)}
    )

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return "origin/main"
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            return ""
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, returned_sha = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    # OLD_SHA != NEW_SHA so head changed; no commits so (0, new_sha)
    assert returned_sha == NEW_SHA


# ---------------------------------------------------------------------------
# poll() collects head_sha_updates into update_poll_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_merges_head_sha_updates_into_state(sample_config: Any) -> None:
    """poll() includes head_sha:<repo> keys from _poll_commits in update_poll_state."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value="2024-01-01T00:00:00Z")
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[SAMPLE_REPO])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._poll_prs = AsyncMock(return_value=0)
    # Return 1 task created and a new sha
    poller._poll_commits = AsyncMock(return_value=(1, NEW_SHA))

    total = await poller.poll()

    assert total == 1
    mock_tq.update_poll_state.assert_awaited_once()
    _, __, state = mock_tq.update_poll_state.await_args.args
    assert state["tasks_created"] == 1
    assert state[f"head_sha:{SAMPLE_REPO['name']}"] == NEW_SHA


@pytest.mark.asyncio
async def test_poll_does_not_include_head_sha_when_none_returned(
    sample_config: Any,
) -> None:
    """When _poll_commits returns None as sha, head_sha key is NOT included in state."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value="2024-01-01T00:00:00Z")
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[SAMPLE_REPO])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._poll_prs = AsyncMock(return_value=0)
    poller._poll_commits = AsyncMock(return_value=(0, None))

    await poller.poll()

    _, __, state = mock_tq.update_poll_state.await_args.args
    assert f"head_sha:{SAMPLE_REPO['name']}" not in state


@pytest.mark.asyncio
async def test_poll_accumulates_tasks_from_prs_and_commits(sample_config: Any) -> None:
    """poll() sums task counts from both _poll_prs and _poll_commits correctly."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value="2024-01-01T00:00:00Z")
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[SAMPLE_REPO])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._poll_prs = AsyncMock(return_value=3)
    poller._poll_commits = AsyncMock(return_value=(2, NEW_SHA))

    total = await poller.poll()

    assert total == 5


@pytest.mark.asyncio
async def test_poll_handles_commit_poll_exception_no_sha_update(
    sample_config: Any,
) -> None:
    """When _poll_commits raises, no head_sha key is included in the state update."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_poll_cursor = AsyncMock(return_value="2024-01-01T00:00:00Z")
    mock_tq.update_poll_state = AsyncMock()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_all = AsyncMock(return_value=[SAMPLE_REPO])

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)
    poller._poll_prs = AsyncMock(return_value=0)
    poller._poll_commits = AsyncMock(side_effect=RuntimeError("git error"))

    total = await poller.poll()

    # Should not crash; commit exception is swallowed
    assert total == 0
    _, __, state = mock_tq.update_poll_state.await_args.args
    assert f"head_sha:{SAMPLE_REPO['name']}" not in state


# ---------------------------------------------------------------------------
# default branch fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_falls_back_to_main_when_rev_parse_abbrev_ref_fails(
    sample_config: Any, tmp_path: Any
) -> None:
    """When rev-parse --abbrev-ref throws, default branch falls back to 'main'."""
    git_dir = tmp_path / "repo" / ".git"
    git_dir.mkdir(parents=True)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.task_exists = AsyncMock(return_value=False)
    mock_tq.create_task = AsyncMock(return_value=True)
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    captured_log_args: list[tuple] = []

    async def mock_run_git(clone_dir: str, *args: str, **_: Any) -> str:
        captured_log_args.append(args)
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            raise RuntimeError("no HEAD ref")
        if args[0] == "rev-parse":
            return NEW_SHA
        if args[0] == "log":
            # Verify the branch used is origin/main
            assert "origin/main" in args[1]
            return f"{NEW_SHA}\tFix bug\tDev\t2024-06-01T00:00:00+00:00"
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        count, _ = await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    assert count == 1


# ---------------------------------------------------------------------------
# DB fetch_one called with correct poller name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_commits_queries_poll_state_with_correct_poller_name(
    sample_config: Any, tmp_path: Any
) -> None:
    """_poll_commits fetches poll_state using the poller's own name."""
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
            return NEW_SHA
        return ""

    with patch(
        "aquarco_supervisor.pollers.github_source._run_git",
        side_effect=mock_run_git,
    ):
        await poller._poll_commits(
            "test-repo", str(tmp_path / "repo"), "2024-01-01T00:00:00Z"
        )

    mock_db.fetch_one.assert_awaited_once()
    query_params = mock_db.fetch_one.await_args.args[1]
    assert query_params["name"] == "github-source"

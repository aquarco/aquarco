"""Tests for GitHub tasks poller."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aifishtank_supervisor.models import SupervisorConfig
from aifishtank_supervisor.pollers.github_tasks import GitHubTasksPoller
from aifishtank_supervisor.task_queue import TaskQueue
from aifishtank_supervisor.utils import url_to_slug as _url_to_slug


def test_url_to_slug_https() -> None:
    assert _url_to_slug("https://github.com/owner/repo.git") == "owner/repo"
    assert _url_to_slug("https://github.com/owner/repo") == "owner/repo"


def test_url_to_slug_ssh() -> None:
    assert _url_to_slug("git@github.com:owner/repo.git") == "owner/repo"
    assert _url_to_slug("git@github.com:owner/repo") == "owner/repo"


def test_url_to_slug_invalid() -> None:
    assert _url_to_slug("not-a-url") is None
    assert _url_to_slug("") is None


def test_categorize(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    poller = GitHubTasksPoller(sample_config, tq)

    assert poller._categorize(["bug"]) == "implementation"
    assert poller._categorize(["feature"]) == "analyze"
    assert poller._categorize(["unknown-label"]) == "analyze"
    assert poller._categorize([]) == "analyze"


def test_select_pipeline(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    poller = GitHubTasksPoller(sample_config, tq)

    assert poller._select_pipeline(["feature"]) == "feature-pipeline"
    assert poller._select_pipeline(["enhancement"]) == "feature-pipeline"
    assert poller._select_pipeline(["bug"]) == "bugfix-pipeline"
    assert poller._select_pipeline(["unrelated"]) == "feature-pipeline"


@pytest.mark.asyncio
async def test_process_issue_creates_task(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    tq.task_exists = AsyncMock(return_value=False)
    tq.create_task = AsyncMock(return_value=True)
    poller = GitHubTasksPoller(sample_config, tq)

    issue = {
        "number": 42,
        "title": "Fix the widget",
        "body": "It's broken",
        "url": "https://github.com/owner/repo/issues/42",
        "labels": [{"name": "bug"}],
    }
    result = await poller._process_issue(issue, "my-repo", "owner/repo")
    assert result is True
    tq.create_task.assert_called_once()
    call_kwargs = tq.create_task.call_args[1]
    assert call_kwargs["task_id"] == "github-issue-my-repo-42"
    assert call_kwargs["category"] == "implementation"  # bug -> implementation
    assert call_kwargs["pipeline"] == "bugfix-pipeline"
    assert call_kwargs["source"] == "github-issues"


@pytest.mark.asyncio
async def test_process_issue_skips_existing(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    tq.task_exists = AsyncMock(return_value=True)
    poller = GitHubTasksPoller(sample_config, tq)

    issue = {"number": 1, "title": "Test", "labels": []}
    result = await poller._process_issue(issue, "repo", "owner/repo")
    assert result is False
    tq.create_task.assert_not_called()


@pytest.mark.asyncio
async def test_poll_creates_tasks_from_issues(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    tq.get_poll_cursor = AsyncMock(return_value=None)
    tq.task_exists = AsyncMock(return_value=False)
    tq.create_task = AsyncMock(return_value=True)
    tq.update_poll_state = AsyncMock()
    poller = GitHubTasksPoller(sample_config, tq)

    issues = [
        {"number": 10, "title": "Issue A", "body": "", "url": "", "labels": []},
        {"number": 11, "title": "Issue B", "body": "", "url": "", "labels": [{"name": "bug"}]},
    ]

    with patch(
        "aifishtank_supervisor.pollers.github_tasks._gh_list_issues",
        AsyncMock(return_value=issues),
    ):
        count = await poller.poll()

    assert count == 2
    assert tq.create_task.call_count == 2
    tq.update_poll_state.assert_called_once()
    call_kwargs = tq.update_poll_state.call_args[0]
    assert call_kwargs[0] == "github-tasks"
    assert call_kwargs[2] == {"tasks_created": 2}


@pytest.mark.asyncio
async def test_poll_handles_gh_error(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    tq.get_poll_cursor = AsyncMock(return_value="2026-01-01T00:00:00Z")
    tq.update_poll_state = AsyncMock()
    poller = GitHubTasksPoller(sample_config, tq)

    with patch(
        "aifishtank_supervisor.pollers.github_tasks._gh_list_issues",
        AsyncMock(side_effect=RuntimeError("gh failed")),
    ):
        count = await poller.poll()

    assert count == 0
    tq.update_poll_state.assert_called_once()


@pytest.mark.asyncio
async def test_poll_skips_repos_without_poller(sample_config: SupervisorConfig) -> None:
    # Remove github-tasks from repo pollers
    for repo in sample_config.spec.repositories:
        repo.pollers = ["github-source"]

    tq = AsyncMock(spec=TaskQueue)
    tq.get_poll_cursor = AsyncMock(return_value=None)
    tq.update_poll_state = AsyncMock()
    poller = GitHubTasksPoller(sample_config, tq)

    with patch(
        "aifishtank_supervisor.pollers.github_tasks._gh_list_issues",
        AsyncMock(return_value=[]),
    ) as mock_gh:
        count = await poller.poll()

    assert count == 0
    mock_gh.assert_not_called()


@pytest.mark.asyncio
async def test_poll_skips_repo_with_invalid_url(
    sample_config: SupervisorConfig,
) -> None:
    """When url_to_slug returns None, repo is skipped."""
    sample_config.spec.repositories[0].url = "not-a-valid-url"

    tq = AsyncMock(spec=TaskQueue)
    tq.get_poll_cursor = AsyncMock(return_value=None)
    tq.update_poll_state = AsyncMock()
    poller = GitHubTasksPoller(sample_config, tq)

    with patch(
        "aifishtank_supervisor.pollers.github_tasks._gh_list_issues",
        AsyncMock(return_value=[]),
    ) as mock_gh:
        count = await poller.poll()

    assert count == 0
    mock_gh.assert_not_called()

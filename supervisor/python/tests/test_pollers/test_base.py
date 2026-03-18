"""Tests for base poller."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aifishtank_supervisor.models import SupervisorConfig
from aifishtank_supervisor.pollers.github_tasks import GitHubTasksPoller
from aifishtank_supervisor.task_queue import TaskQueue


def test_get_poller_config(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    poller = GitHubTasksPoller(sample_config, tq)
    cfg = poller._get_poller_config()
    assert isinstance(cfg, dict)


def testis_enabled(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    poller = GitHubTasksPoller(sample_config, tq)
    # github-tasks should be enabled in sample config
    result = poller.is_enabled()
    assert isinstance(result, bool)


def testis_enabled_missing_poller(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    poller = GitHubTasksPoller(sample_config, tq)
    poller.name = "nonexistent-poller"
    assert poller.is_enabled() is False


def testget_interval(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    poller = GitHubTasksPoller(sample_config, tq)
    interval = poller.get_interval()
    assert isinstance(interval, int)
    assert interval > 0


def testget_interval_missing_poller(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    poller = GitHubTasksPoller(sample_config, tq)
    poller.name = "nonexistent-poller"
    assert poller.get_interval() == 60


def test_get_poller_config_missing(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    poller = GitHubTasksPoller(sample_config, tq)
    poller.name = "nonexistent-poller"
    assert poller._get_poller_config() == {}

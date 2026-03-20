"""Tests for base poller."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aquarco_supervisor.database import Database
from aquarco_supervisor.models import SupervisorConfig
from aquarco_supervisor.pollers.github_tasks import GitHubTasksPoller
from aquarco_supervisor.task_queue import TaskQueue


def test_get_poller_config(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    db = AsyncMock(spec=Database)
    poller = GitHubTasksPoller(sample_config, tq, db)
    cfg = poller._get_poller_config()
    assert isinstance(cfg, dict)


def testis_enabled(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    db = AsyncMock(spec=Database)
    poller = GitHubTasksPoller(sample_config, tq, db)
    # github-tasks should be enabled in sample config
    result = poller.is_enabled()
    assert isinstance(result, bool)


def testis_enabled_missing_poller(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    db = AsyncMock(spec=Database)
    poller = GitHubTasksPoller(sample_config, tq, db)
    poller.name = "nonexistent-poller"
    assert poller.is_enabled() is False


def testget_interval(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    db = AsyncMock(spec=Database)
    poller = GitHubTasksPoller(sample_config, tq, db)
    interval = poller.get_interval()
    assert isinstance(interval, int)
    assert interval > 0


def testget_interval_missing_poller(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    db = AsyncMock(spec=Database)
    poller = GitHubTasksPoller(sample_config, tq, db)
    poller.name = "nonexistent-poller"
    assert poller.get_interval() == 60


def test_get_poller_config_missing(sample_config: SupervisorConfig) -> None:
    tq = AsyncMock(spec=TaskQueue)
    db = AsyncMock(spec=Database)
    poller = GitHubTasksPoller(sample_config, tq, db)
    poller.name = "nonexistent-poller"
    assert poller._get_poller_config() == {}

"""Tests for dispatch pausing when Claude/GitHub auth is broken.

Validates that _dispatch_pending_tasks skips dispatch and logs periodically
(every 60 seconds) when authentication flags are set.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.main import Supervisor


# ---------------------------------------------------------------------------
# Claude auth broken — dispatch is paused
# ---------------------------------------------------------------------------


class TestDispatchPausedClaudeAuth:
    """When _claude_auth_broken is True, dispatch is skipped."""

    @pytest.mark.asyncio
    async def test_dispatch_skips_when_claude_auth_broken(self, sample_config: Any) -> None:
        """No tasks should be dispatched when Claude auth is broken."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._claude_auth_broken = True

        await supervisor._dispatch_pending_tasks()

        # Should not attempt to get any tasks
        mock_tq.get_next_task.assert_not_called()
        mock_db.fetch_val.assert_not_called()

    @pytest.mark.asyncio
    async def test_claude_auth_broken_logs_warning_first_time(self, sample_config: Any) -> None:
        """First dispatch while auth is broken should log a warning."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._claude_auth_broken = True

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            mock_log.warning.assert_called_once()
            call_args = mock_log.warning.call_args
            assert "dispatch_paused_claude_auth" in call_args[0]

    @pytest.mark.asyncio
    async def test_claude_auth_broken_throttles_logging(self, sample_config: Any) -> None:
        """Subsequent dispatches within 60s should NOT log again."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._claude_auth_broken = True
        # Simulate: log was just emitted
        supervisor._last_claude_auth_log = time.monotonic()

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            mock_log.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_claude_auth_broken_logs_again_after_60s(self, sample_config: Any) -> None:
        """After 60s, the log should fire again."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._claude_auth_broken = True
        # Simulate: log was emitted 61 seconds ago
        supervisor._last_claude_auth_log = time.monotonic() - 61

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# GitHub auth broken — dispatch is paused
# ---------------------------------------------------------------------------


class TestDispatchPausedGitHubAuth:
    """When _github_auth_broken is True, dispatch is skipped."""

    @pytest.mark.asyncio
    async def test_dispatch_skips_when_github_auth_broken(self, sample_config: Any) -> None:
        """No tasks should be dispatched when GitHub auth is broken."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._github_auth_broken = True

        await supervisor._dispatch_pending_tasks()

        mock_tq.get_next_task.assert_not_called()
        mock_db.fetch_val.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_auth_broken_logs_warning_first_time(self, sample_config: Any) -> None:
        """First dispatch while GitHub auth is broken should log a warning."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._github_auth_broken = True

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            mock_log.warning.assert_called_once()
            call_args = mock_log.warning.call_args
            assert "dispatch_paused_github_auth" in call_args[0]

    @pytest.mark.asyncio
    async def test_github_auth_broken_throttles_logging(self, sample_config: Any) -> None:
        """Subsequent dispatches within 60s should NOT log again."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._github_auth_broken = True
        supervisor._last_github_auth_log = time.monotonic()

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            mock_log.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_github_auth_broken_logs_again_after_60s(self, sample_config: Any) -> None:
        """After 60s, the log should fire again."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._github_auth_broken = True
        supervisor._last_github_auth_log = time.monotonic() - 61

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            mock_log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# Auth broken priority — Claude auth checked before GitHub auth
# ---------------------------------------------------------------------------


class TestAuthBrokenPriority:
    """Claude auth check happens before GitHub auth check."""

    @pytest.mark.asyncio
    async def test_both_broken_logs_claude_not_github(self, sample_config: Any) -> None:
        """When both auth systems are broken, Claude auth is reported first."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_tq = AsyncMock()
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._claude_auth_broken = True
        supervisor._github_auth_broken = True

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            # Should log Claude auth, not GitHub auth
            assert mock_log.warning.call_count == 1
            call_args = mock_log.warning.call_args
            assert "dispatch_paused_claude_auth" in call_args[0]


# ---------------------------------------------------------------------------
# Normal dispatch — auth flags not set
# ---------------------------------------------------------------------------


class TestDispatchNotPausedWhenAuthOk:
    """When auth flags are False, dispatch proceeds normally."""

    @pytest.mark.asyncio
    async def test_dispatch_proceeds_when_auth_ok(self, sample_config: Any) -> None:
        """Dispatch should proceed to capacity check when auth is fine."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_db.fetch_val = AsyncMock(return_value=0)  # 0 active agents
        mock_tq = AsyncMock()
        mock_tq.get_next_task = AsyncMock(return_value=None)
        mock_registry = AsyncMock()
        mock_executor = AsyncMock()

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = mock_registry
        supervisor._executor = mock_executor
        supervisor._claude_auth_broken = False
        supervisor._github_auth_broken = False

        await supervisor._dispatch_pending_tasks()

        # Should have reached the capacity check (fetch_val is called for active count)
        mock_db.fetch_val.assert_called_once()

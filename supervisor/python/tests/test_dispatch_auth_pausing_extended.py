"""Extended tests for dispatch auth pausing behavior.

Covers edge cases around the getattr-based _last_*_auth_log attributes and
verifies the interaction between Claude and GitHub auth broken states.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.main import Supervisor


# ---------------------------------------------------------------------------
# getattr fallback for _last_*_auth_log attributes
# ---------------------------------------------------------------------------


class TestLastAuthLogGetattr:
    """The code uses getattr(self, '_last_claude_auth_log', 0) which means
    the attribute may not be initialized in __init__. Tests verify the
    first-access behavior.
    """

    @pytest.mark.asyncio
    async def test_first_claude_auth_log_works_without_init(self, sample_config: Any) -> None:
        """First access to _last_claude_auth_log should default to 0 via getattr."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        supervisor._db = mock_db
        supervisor._tq = AsyncMock()
        supervisor._registry = AsyncMock()
        supervisor._executor = AsyncMock()
        supervisor._claude_auth_broken = True

        # Ensure _last_claude_auth_log is NOT set as an instance attribute
        if hasattr(supervisor, "_last_claude_auth_log"):
            delattr(supervisor, "_last_claude_auth_log")

        with patch("aquarco_supervisor.main.log") as mock_log:
            # Should not raise AttributeError
            await supervisor._dispatch_pending_tasks()
            # Should have logged (since getattr returns 0, which is far in the past)
            mock_log.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_github_auth_log_works_without_init(self, sample_config: Any) -> None:
        """First access to _last_github_auth_log should default to 0 via getattr."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        supervisor._db = mock_db
        supervisor._tq = AsyncMock()
        supervisor._registry = AsyncMock()
        supervisor._executor = AsyncMock()
        supervisor._github_auth_broken = True

        if hasattr(supervisor, "_last_github_auth_log"):
            delattr(supervisor, "_last_github_auth_log")

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            mock_log.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_last_auth_log_is_set_after_first_log(self, sample_config: Any) -> None:
        """After the first warning log, the timestamp attribute should be set."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        supervisor._db = mock_db
        supervisor._tq = AsyncMock()
        supervisor._registry = AsyncMock()
        supervisor._executor = AsyncMock()
        supervisor._claude_auth_broken = True

        with patch("aquarco_supervisor.main.log"):
            await supervisor._dispatch_pending_tasks()

        # After the call, the attribute should now exist on the instance
        assert hasattr(supervisor, "_last_claude_auth_log")
        assert supervisor._last_claude_auth_log > 0


# ---------------------------------------------------------------------------
# Drain mode takes precedence over auth checks
# ---------------------------------------------------------------------------


class TestDrainModePrecedence:
    """Drain mode check happens before auth checks."""

    @pytest.mark.asyncio
    async def test_drain_mode_returns_before_auth_check(self, sample_config: Any) -> None:
        """When drain mode is active, auth broken state doesn't matter."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": "true",
            "active_count": 1,  # not idle, so no shutdown
            "executing_count": 1,
        })
        supervisor._db = mock_db
        supervisor._tq = AsyncMock()
        supervisor._registry = AsyncMock()
        supervisor._executor = AsyncMock()
        supervisor._claude_auth_broken = True
        supervisor._github_auth_broken = True

        with patch("aquarco_supervisor.main.log") as mock_log:
            await supervisor._dispatch_pending_tasks()
            # Should NOT log auth warnings — drain mode returned first
            for call in mock_log.warning.call_args_list:
                assert "dispatch_paused_claude_auth" not in str(call)
                assert "dispatch_paused_github_auth" not in str(call)


# ---------------------------------------------------------------------------
# No components initialized — early return
# ---------------------------------------------------------------------------


class TestDispatchEarlyReturn:
    """Dispatch should return early if components are not initialized."""

    @pytest.mark.asyncio
    async def test_returns_when_tq_is_none(self, sample_config: Any) -> None:
        """No crash when _tq is None."""
        supervisor = Supervisor(sample_config, {})
        supervisor._tq = None
        supervisor._db = AsyncMock()
        supervisor._registry = AsyncMock()
        supervisor._executor = AsyncMock()

        # Should not raise
        await supervisor._dispatch_pending_tasks()

    @pytest.mark.asyncio
    async def test_returns_when_db_is_none(self, sample_config: Any) -> None:
        """No crash when _db is None."""
        supervisor = Supervisor(sample_config, {})
        supervisor._db = None
        supervisor._tq = AsyncMock()
        supervisor._registry = AsyncMock()
        supervisor._executor = AsyncMock()

        await supervisor._dispatch_pending_tasks()

    @pytest.mark.asyncio
    async def test_returns_when_registry_is_none(self, sample_config: Any) -> None:
        """No crash when _registry is None."""
        supervisor = Supervisor(sample_config, {})
        supervisor._registry = None
        supervisor._db = AsyncMock()
        supervisor._tq = AsyncMock()
        supervisor._executor = AsyncMock()

        await supervisor._dispatch_pending_tasks()

    @pytest.mark.asyncio
    async def test_returns_when_executor_is_none(self, sample_config: Any) -> None:
        """No crash when _executor is None."""
        supervisor = Supervisor(sample_config, {})
        supervisor._executor = None
        supervisor._db = AsyncMock()
        supervisor._tq = AsyncMock()
        supervisor._registry = AsyncMock()

        await supervisor._dispatch_pending_tasks()


# ---------------------------------------------------------------------------
# Auth recovery clears log timestamp
# ---------------------------------------------------------------------------


class TestAuthRecovery:
    """After auth is cleared, dispatch should proceed normally."""

    @pytest.mark.asyncio
    async def test_dispatch_resumes_after_claude_auth_cleared(self, sample_config: Any) -> None:
        """After _claude_auth_broken is set to False, dispatch proceeds."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_db.fetch_val = AsyncMock(return_value=0)
        mock_tq = AsyncMock()
        mock_tq.get_next_task = AsyncMock(return_value=None)

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = AsyncMock()
        supervisor._executor = AsyncMock()

        # First: auth is broken
        supervisor._claude_auth_broken = True
        with patch("aquarco_supervisor.main.log"):
            await supervisor._dispatch_pending_tasks()
        mock_db.fetch_val.assert_not_called()

        # Then: auth is cleared
        supervisor._claude_auth_broken = False
        await supervisor._dispatch_pending_tasks()
        # Should have reached capacity check
        mock_db.fetch_val.assert_called()

    @pytest.mark.asyncio
    async def test_dispatch_resumes_after_github_auth_cleared(self, sample_config: Any) -> None:
        """After _github_auth_broken is set to False, dispatch proceeds."""
        supervisor = Supervisor(sample_config, {})

        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value={
            "drain_val": None,
            "active_count": 0,
            "executing_count": 0,
        })
        mock_db.fetch_val = AsyncMock(return_value=0)
        mock_tq = AsyncMock()
        mock_tq.get_next_task = AsyncMock(return_value=None)

        supervisor._db = mock_db
        supervisor._tq = mock_tq
        supervisor._registry = AsyncMock()
        supervisor._executor = AsyncMock()

        # First: auth is broken
        supervisor._github_auth_broken = True
        with patch("aquarco_supervisor.main.log"):
            await supervisor._dispatch_pending_tasks()
        mock_db.fetch_val.assert_not_called()

        # Then: auth is cleared
        supervisor._github_auth_broken = False
        await supervisor._dispatch_pending_tasks()
        mock_db.fetch_val.assert_called()

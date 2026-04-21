"""Integration tests for the refactored auth error detection in pollers.

After the DRY refactoring, both github_source.py and github_tasks.py import
is_github_auth_error from auth_utils.py. These tests verify the actual
integration points where the function is called within the _gh_list_* helpers.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.exceptions import GitHubAuthenticationError


# ---------------------------------------------------------------------------
# _gh_list_prs — auth error detection integration
# ---------------------------------------------------------------------------


class TestGhListPrsAuthErrorIntegration:
    """Test that _gh_list_prs correctly raises GitHubAuthenticationError
    for auth-related failures and RuntimeError for other failures.
    """

    async def test_raises_github_auth_error_on_401(self) -> None:
        """401 stderr from gh pr list should raise GitHubAuthenticationError."""
        from aquarco_supervisor.pollers.github_source import _gh_list_prs

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"HTTP 401: Bad credentials")
        )

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(GitHubAuthenticationError):
                await _gh_list_prs("owner/repo")

    async def test_raises_runtime_error_on_generic_failure(self) -> None:
        """Non-auth failures should raise RuntimeError, not GitHubAuthenticationError."""
        from aquarco_supervisor.pollers.github_source import _gh_list_prs

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"connection refused to api.github.com")
        )

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(RuntimeError, match="gh pr list failed"):
                await _gh_list_prs("owner/repo")

    async def test_returns_parsed_json_on_success(self) -> None:
        """Successful gh pr list returns parsed JSON."""
        from aquarco_supervisor.pollers.github_source import _gh_list_prs

        prs = [{"number": 1, "title": "Test PR"}]
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(json.dumps(prs).encode(), b"")
        )

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await _gh_list_prs("owner/repo")

        assert result == prs

    async def test_token_keyword_does_not_trigger_auth_error(self) -> None:
        """The word 'token' in stderr should NOT trigger GitHubAuthenticationError.

        This was the key fix: the bare 'token' keyword was removed.
        """
        from aquarco_supervisor.pollers.github_source import _gh_list_prs

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"invalid JSON token in response body")
        )

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(RuntimeError):
                await _gh_list_prs("owner/repo")


# ---------------------------------------------------------------------------
# _gh_list_issues — auth error detection integration
# ---------------------------------------------------------------------------


class TestGhListIssuesAuthErrorIntegration:
    """Test that _gh_list_issues correctly raises GitHubAuthenticationError."""

    async def test_raises_github_auth_error_on_not_logged_in(self) -> None:
        """'not logged in' stderr should raise GitHubAuthenticationError."""
        from aquarco_supervisor.pollers.github_tasks import _gh_list_issues

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"You are not logged in to any GitHub hosts")
        )

        with patch(
            "aquarco_supervisor.pollers.github_tasks.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(GitHubAuthenticationError):
                await _gh_list_issues("owner/repo", "2026-01-01T00:00:00Z")

    async def test_raises_runtime_error_on_network_issue(self) -> None:
        """Network errors should raise RuntimeError, not auth error."""
        from aquarco_supervisor.pollers.github_tasks import _gh_list_issues

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"DNS resolution failed for github.com")
        )

        with patch(
            "aquarco_supervisor.pollers.github_tasks.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(RuntimeError, match="gh issue list failed"):
                await _gh_list_issues("owner/repo", "2026-01-01T00:00:00Z")

    async def test_token_keyword_does_not_trigger_auth_error_issues(self) -> None:
        """Same fix: bare 'token' in issues stderr should not trigger auth error."""
        from aquarco_supervisor.pollers.github_tasks import _gh_list_issues

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"rate limit for token exceeded, retry after 60s")
        )

        with patch(
            "aquarco_supervisor.pollers.github_tasks.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(RuntimeError):
                await _gh_list_issues("owner/repo", "2026-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# _gh_list_merged_prs — auth error detection integration
# ---------------------------------------------------------------------------


class TestGhListMergedPrsAuthErrorIntegration:
    """Test that _gh_list_merged_prs correctly raises GitHubAuthenticationError."""

    async def test_raises_github_auth_error_on_unauthorized(self) -> None:
        """'unauthorized' stderr should raise GitHubAuthenticationError."""
        from aquarco_supervisor.pollers.github_source import _gh_list_merged_prs

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"You are unauthorized to access this resource")
        )

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(GitHubAuthenticationError):
                await _gh_list_merged_prs("owner/repo")

    async def test_raises_github_auth_error_on_bad_credentials(self) -> None:
        """'bad credentials' stderr should raise GitHubAuthenticationError."""
        from aquarco_supervisor.pollers.github_source import _gh_list_merged_prs

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"error: bad credentials for repository")
        )

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(GitHubAuthenticationError):
                await _gh_list_merged_prs("owner/repo")

    async def test_timeout_raises_runtime_error(self) -> None:
        """Timeout should raise RuntimeError."""
        from aquarco_supervisor.pollers.github_source import _gh_list_merged_prs

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_proc.kill = AsyncMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ), patch(
            "aquarco_supervisor.pollers.github_source.asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ):
            with pytest.raises(RuntimeError, match="timed out"):
                await _gh_list_merged_prs("owner/repo")

    async def test_returns_parsed_json_on_success(self) -> None:
        """Successful gh pr list (merged) returns parsed JSON."""
        from aquarco_supervisor.pollers.github_source import _gh_list_merged_prs

        prs = [{"number": 42, "title": "Merged PR", "mergedAt": "2026-04-20T12:00:00Z"}]
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(json.dumps(prs).encode(), b"")
        )

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            result = await _gh_list_merged_prs("owner/repo")

        assert result == prs


# ---------------------------------------------------------------------------
# Credential redaction in error messages
# ---------------------------------------------------------------------------


class TestCredentialRedaction:
    """Verify that embedded credentials in stderr are redacted."""

    async def test_gh_list_prs_redacts_embedded_token_in_url(self) -> None:
        """URLs containing tokens (https://token@host/...) should be redacted."""
        from aquarco_supervisor.pollers.github_source import _gh_list_prs

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"",
                b"failed to authenticate: https://ghp_secrettoken123@github.com/owner/repo 401",
            )
        )

        with patch(
            "aquarco_supervisor.pollers.github_source.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            with pytest.raises(GitHubAuthenticationError) as exc_info:
                await _gh_list_prs("owner/repo")

        # The error message should not contain the raw token
        error_msg = str(exc_info.value)
        assert "ghp_secrettoken123" not in error_msg
        assert "<redacted>" in error_msg

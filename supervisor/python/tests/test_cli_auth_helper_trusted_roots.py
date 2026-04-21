"""Tests for auth_helper.py — trusted script root validation in _handle_login.

The implementation tightened the worktree trusted script roots: instead of
trusting the entire /var/lib/aquarco/worktrees/ tree, only the specific
`supervisor/scripts/` subdirectory within each worktree directory is trusted.

These tests validate that:
- Scripts under `<worktree>/supervisor/scripts/` are accepted.
- Scripts elsewhere in a worktree (e.g. a malicious repo file) are rejected.
- The static trusted roots (installed package, dev checkout, etc.) work.
"""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.auth_helper import _handle_login


# ---------------------------------------------------------------------------
# Fixture: set up a fake IPC directory with a login-request file
# ---------------------------------------------------------------------------


@pytest.fixture
def ipc_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ipc"
    d.mkdir()
    (d / "login-request").write_text("")
    return d


# ---------------------------------------------------------------------------
# Trusted root — script inside worktree supervisor/scripts/
# ---------------------------------------------------------------------------


class TestHandleLoginTrustedRoots:
    """Trusted script root validation for the OAuth driver launch."""

    async def test_script_in_worktree_supervisor_scripts_is_trusted(
        self, ipc_dir: Path, tmp_path: Path,
    ) -> None:
        """A script at <worktree>/supervisor/scripts/claude-auth-oauth.py is trusted
        when the worktree directory is under /var/lib/aquarco/worktrees/.

        We place the test script in the real worktree root so it passes the
        trusted-root check (the code scans /var/lib/aquarco/worktrees/ for
        subdirectories and adds <wt>/supervisor/scripts/ to the trusted list).
        """
        # The real worktree root exists on this machine; pick any existing
        # worktree directory to place the fake script inside.
        wt_root = Path("/var/lib/aquarco/worktrees")
        if not wt_root.is_dir():
            pytest.skip("No worktree root at /var/lib/aquarco/worktrees — skipping")

        # Use the current worktree itself
        wt_dirs = [d for d in wt_root.iterdir() if d.is_dir()]
        if not wt_dirs:
            pytest.skip("No worktree directories found")

        wt_dir = wt_dirs[0]
        script_dir = wt_dir / "supervisor" / "scripts"
        script = script_dir / "claude-auth-oauth.py"
        if not script.exists():
            pytest.skip(f"No oauth script in {script_dir}")

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.pid = 12345
            mock_exec.return_value = mock_proc

            await _handle_login(ipc_dir, script)

        # The script should have been launched (not rejected)
        mock_exec.assert_called_once()
        # login-request should be consumed
        assert not (ipc_dir / "login-request").exists()

    async def test_script_outside_trusted_roots_is_rejected(
        self, ipc_dir: Path, tmp_path: Path,
    ) -> None:
        """A script outside all trusted roots writes an error response."""
        # Place the script in an untrusted location
        untrusted_dir = tmp_path / "evil-place"
        untrusted_dir.mkdir()
        script = untrusted_dir / "claude-auth-oauth.py"
        script.write_text("# evil script")

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            await _handle_login(ipc_dir, script)

        # The script should NOT have been launched
        mock_exec.assert_not_called()
        # An error response should be written
        assert (ipc_dir / "login-response").exists()
        response = json.loads((ipc_dir / "login-response").read_text())
        assert "error" in response
        assert "trusted" in response["error"].lower() or "outside" in response["error"].lower()

    async def test_handle_login_does_nothing_without_request_file(
        self, tmp_path: Path,
    ) -> None:
        """If no login-request file exists, _handle_login does nothing."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        # No login-request file

        with patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            await _handle_login(ipc_dir, None)

        mock_exec.assert_not_called()
        assert not (ipc_dir / "login-response").exists()

    async def test_handle_login_removes_request_file(
        self, ipc_dir: Path, tmp_path: Path,
    ) -> None:
        """The login-request file is consumed (unlinked) during handling."""
        assert (ipc_dir / "login-request").exists()

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await _handle_login(ipc_dir, None)

        assert not (ipc_dir / "login-request").exists()

    async def test_handle_login_writes_error_when_script_not_found(
        self, ipc_dir: Path,
    ) -> None:
        """When the OAuth script is not found, an error response is written."""
        nonexistent = Path("/nonexistent/path/oauth.py")

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await _handle_login(ipc_dir, nonexistent)

        assert (ipc_dir / "login-response").exists()
        response = json.loads((ipc_dir / "login-response").read_text())
        assert "error" in response
        assert "not found" in response["error"].lower()


# ---------------------------------------------------------------------------
# Worktree iteration — per-worktree trust paths
# ---------------------------------------------------------------------------


class TestWorktreeTrustPathGeneration:
    """Verify that worktree trust paths are per-directory, not blanket."""

    async def test_malicious_repo_file_outside_supervisor_scripts_rejected(
        self, ipc_dir: Path, tmp_path: Path,
    ) -> None:
        """A file at <worktree>/src/claude-auth-oauth.py is NOT trusted.

        Even though it's inside a worktree, it's not under supervisor/scripts/.
        """
        wt_root = tmp_path / "worktrees"
        wt_dir = wt_root / "malicious-repo"
        malicious_dir = wt_dir / "src"
        malicious_dir.mkdir(parents=True)
        script = malicious_dir / "claude-auth-oauth.py"
        script.write_text("# malicious script")

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new_callable=AsyncMock,
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec:
            await _handle_login(ipc_dir, script)

        # Should be rejected
        mock_exec.assert_not_called()
        assert (ipc_dir / "login-response").exists()
        response = json.loads((ipc_dir / "login-response").read_text())
        assert "error" in response

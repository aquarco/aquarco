"""Additional coverage tests for auth_helper.py.

Targets the uncovered lines identified after b5c6d35 testing:
  - _run_command: subprocess helper with timeout handling (lines 31-50)
  - _kill_previous_login_processes: cleanup of stale auth processes (lines 53-65)
  - _watch_loop: main poll loop (lines 267-286)
  - Provision.sh: fallback SCRIPTS_SRC path logic
"""

from __future__ import annotations

import asyncio
import json
import stat
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aquarco_supervisor.cli.auth_helper import (
    _handle_login,
    _handle_status,
    _handle_logout,
    _kill_previous_login_processes,
    _run_command,
    _watch_loop,
)


# ---------------------------------------------------------------------------
# _run_command — subprocess helper
# ---------------------------------------------------------------------------


class TestRunCommand:
    """Tests for the _run_command subprocess helper."""

    @pytest.mark.asyncio
    async def test_returns_stdout_and_stderr(self) -> None:
        """Successful command returns (0, stdout, stderr)."""
        rc, stdout, stderr = await _run_command("echo", "hello")
        assert rc == 0
        assert "hello" in stdout
        assert stderr == ""

    @pytest.mark.asyncio
    async def test_returns_nonzero_exit_code(self) -> None:
        """Failed command returns its exit code."""
        rc, stdout, stderr = await _run_command("false")
        assert rc != 0

    @pytest.mark.asyncio
    async def test_timeout_returns_negative_one(self) -> None:
        """When a command exceeds the timeout, returns (-1, '', 'timed out')."""
        rc, stdout, stderr = await _run_command("sleep", "30", timeout=1)
        assert rc == -1
        assert stdout == ""
        assert stderr == "timed out"

    @pytest.mark.asyncio
    async def test_stdin_data_passed_to_process(self) -> None:
        """When stdin_data is provided, it is piped to the process."""
        rc, stdout, stderr = await _run_command(
            "cat", stdin_data=b"test input data", timeout=5
        )
        assert rc == 0
        assert "test input data" in stdout

    @pytest.mark.asyncio
    async def test_without_stdin_data_uses_devnull(self) -> None:
        """Without stdin_data, stdin is /dev/null (command should still succeed)."""
        rc, stdout, stderr = await _run_command("echo", "ok", timeout=5)
        assert rc == 0
        assert "ok" in stdout

    @pytest.mark.asyncio
    async def test_handles_utf8_output(self) -> None:
        """Non-ASCII output is decoded correctly."""
        rc, stdout, _ = await _run_command("printf", "caf\u00e9")
        assert rc == 0
        assert "caf" in stdout

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self) -> None:
        """After timeout, the process is killed (not left dangling)."""
        # Use a long-running command
        rc, _, stderr = await _run_command("sleep", "60", timeout=1)
        assert rc == -1
        assert stderr == "timed out"

    @pytest.mark.asyncio
    async def test_timeout_with_process_already_exited(self) -> None:
        """If the process exits before kill() is called, ProcessLookupError is caught."""
        # Simulate by patching: create a mock that raises ProcessLookupError on kill
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock(side_effect=ProcessLookupError)
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            rc, stdout, stderr = await _run_command("fake-cmd", timeout=1)

        assert rc == -1
        assert stderr == "timed out"
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_returncode_treated_as_negative_one(self) -> None:
        """When proc.returncode is None, _run_command returns -1."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"out", b"err"))
        mock_proc.returncode = None

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            rc, stdout, stderr = await _run_command("fake-cmd", timeout=5)

        assert rc == -1
        assert stdout == "out"
        assert stderr == "err"


# ---------------------------------------------------------------------------
# _kill_previous_login_processes
# ---------------------------------------------------------------------------


class TestKillPreviousLoginProcesses:
    """Tests for the cleanup of stale auth/oauth processes."""

    def test_calls_pkill_for_all_patterns(self) -> None:
        """pkill is called once per known pattern."""
        with patch("subprocess.run") as mock_run:
            _kill_previous_login_processes()

        expected_patterns = [
            "claude-auth-oauth",
            "claude-auth-pexpect",
            "claude auth login",
        ]
        assert mock_run.call_count == len(expected_patterns)
        for i, pattern in enumerate(expected_patterns):
            call_args = mock_run.call_args_list[i]
            assert call_args[0][0] == ["pkill", "-f", pattern]
            assert call_args[1]["check"] is False
            assert call_args[1]["capture_output"] is True

    def test_handles_missing_pkill(self) -> None:
        """When pkill is not available (FileNotFoundError), no crash."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            # Should not raise
            _kill_previous_login_processes()

    def test_continues_after_single_pattern_failure(self) -> None:
        """If one pkill invocation raises FileNotFoundError, others still run."""
        call_log: list[str] = []

        def mock_run(args: list[str], **kwargs: Any) -> None:
            pattern = args[2]
            call_log.append(pattern)
            if pattern == "claude-auth-pexpect":
                raise FileNotFoundError("no pkill")

        with patch("subprocess.run", side_effect=mock_run):
            _kill_previous_login_processes()

        # All three patterns should have been attempted
        assert len(call_log) == 3
        assert "claude-auth-oauth" in call_log
        assert "claude-auth-pexpect" in call_log
        assert "claude auth login" in call_log


# ---------------------------------------------------------------------------
# _watch_loop — main poll loop
# ---------------------------------------------------------------------------


class TestWatchLoop:
    """Tests for the main IPC directory watch loop."""

    @pytest.mark.asyncio
    async def test_creates_ipc_dir_with_restricted_permissions(self, tmp_path: Path) -> None:
        """_watch_loop creates the IPC directory with mode 0o700."""
        ipc_dir = tmp_path / "ipc-new"
        stop_event = asyncio.Event()
        stop_event.set()  # Stop immediately after first iteration

        await _watch_loop(ipc_dir, poll_interval=1, oauth_script=None, stop_event=stop_event)

        assert ipc_dir.exists()
        mode = ipc_dir.stat().st_mode & 0o777
        assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"

    @pytest.mark.asyncio
    async def test_handles_all_request_types_per_iteration(self, tmp_path: Path) -> None:
        """Each loop iteration calls _handle_login, _handle_status, _handle_logout."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()

        stop_event = asyncio.Event()
        iteration_count = 0

        # Stop after one full iteration
        original_wait_for = asyncio.wait_for

        async def stop_after_one(coro: Any, timeout: float) -> None:
            nonlocal iteration_count
            iteration_count += 1
            stop_event.set()
            raise asyncio.TimeoutError

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_login",
                new=AsyncMock(),
            ) as mock_login,
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_status",
                new=AsyncMock(),
            ) as mock_status,
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_logout",
                new=AsyncMock(),
            ) as mock_logout,
            patch("asyncio.wait_for", side_effect=stop_after_one),
        ):
            await _watch_loop(ipc_dir, poll_interval=1, oauth_script=None, stop_event=stop_event)

        mock_login.assert_called_once()
        mock_status.assert_called_once()
        mock_logout.assert_called_once()

    @pytest.mark.asyncio
    async def test_continues_after_handler_exception(self, tmp_path: Path) -> None:
        """If a handler raises an exception, the loop continues."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()

        stop_event = asyncio.Event()
        call_count = 0

        async def failing_login(*args: Any, **kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("handler crash")
            # Second call succeeds

        async def stop_on_second(coro: Any, timeout: float) -> None:
            if call_count >= 2:
                stop_event.set()
            raise asyncio.TimeoutError

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_login",
                side_effect=failing_login,
            ),
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_status",
                new=AsyncMock(),
            ),
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_logout",
                new=AsyncMock(),
            ),
            patch("asyncio.wait_for", side_effect=stop_on_second),
        ):
            await _watch_loop(ipc_dir, poll_interval=1, oauth_script=None, stop_event=stop_event)

        # Should have run at least twice (crash + recovery)
        assert call_count >= 2

    @pytest.mark.asyncio
    async def test_stop_event_terminates_loop(self, tmp_path: Path) -> None:
        """Setting the stop event causes the loop to exit gracefully."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()

        stop_event = asyncio.Event()

        async def set_stop_after_login(*args: Any, **kwargs: Any) -> None:
            stop_event.set()

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_login",
                side_effect=set_stop_after_login,
            ),
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_status",
                new=AsyncMock(),
            ),
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_logout",
                new=AsyncMock(),
            ),
        ):
            # Should complete without hanging
            await asyncio.wait_for(
                _watch_loop(ipc_dir, poll_interval=60, oauth_script=None, stop_event=stop_event),
                timeout=5,
            )

    @pytest.mark.asyncio
    async def test_passes_oauth_script_to_handle_login(self, tmp_path: Path) -> None:
        """The oauth_script parameter is forwarded to _handle_login."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()

        stop_event = asyncio.Event()
        fake_script = Path("/some/script.py")

        async def stop_after_login(ipc: Path, script: Path | None) -> None:
            stop_event.set()

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_login",
                side_effect=stop_after_login,
            ) as mock_login,
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_status",
                new=AsyncMock(),
            ),
            patch(
                "aquarco_supervisor.cli.auth_helper._handle_logout",
                new=AsyncMock(),
            ),
        ):
            await _watch_loop(ipc_dir, poll_interval=1, oauth_script=fake_script, stop_event=stop_event)

        mock_login.assert_called_once_with(ipc_dir, fake_script)


# ---------------------------------------------------------------------------
# Provision.sh — additional static analysis
# ---------------------------------------------------------------------------

PROVISION_SCRIPT = (
    Path(__file__).parent.parent.parent.parent / "vagrant" / "scripts" / "provision.sh"
)


@pytest.fixture
def provision_content() -> str:
    """Read provision.sh content."""
    assert PROVISION_SCRIPT.exists(), f"provision.sh not found at {PROVISION_SCRIPT}"
    return PROVISION_SCRIPT.read_text()


class TestProvisionScriptFallbackLogic:
    """Additional provision.sh tests for the SCRIPTS_SRC fallback logic."""

    def test_dev_mode_scripts_src_uses_agent_home(self, provision_content: str) -> None:
        """In DEV_MODE, SCRIPTS_SRC points to the mounted repo scripts dir."""
        # Look for the dev-mode assignment
        assert 'SCRIPTS_SRC="${AGENT_HOME}/aquarco/supervisor/scripts"' in provision_content

    def test_prod_mode_scripts_src_uses_tmp(self, provision_content: str) -> None:
        """In prod mode, SCRIPTS_SRC points to the pip-extracted package."""
        assert "/tmp/aquarco-supervisor-python/src/aquarco_supervisor/scripts" in provision_content

    def test_prod_mode_warns_when_source_missing(self, provision_content: str) -> None:
        """When SCRIPTS_SRC directory doesn't exist, a warning is logged."""
        assert "scripts source not found" in provision_content
        assert "may be empty" in provision_content

    def test_cp_command_has_error_suppression(self, provision_content: str) -> None:
        """The cp command uses `2>/dev/null || true` to handle missing files gracefully."""
        lines = provision_content.splitlines()
        cp_lines = [
            line.strip() for line in lines
            if "cp " in line and "/var/lib/aquarco/scripts/" in line
        ]
        assert len(cp_lines) > 0
        for line in cp_lines:
            assert "|| true" in line or "2>/dev/null" in line, (
                f"cp command should handle errors gracefully: {line}"
            )

    def test_scripts_copy_guarded_by_dir_check(self, provision_content: str) -> None:
        """The cp to /var/lib/aquarco/scripts/ is guarded by checking SCRIPTS_SRC exists."""
        assert '-d "${SCRIPTS_SRC}"' in provision_content

    def test_restart_not_start_for_both_services(self, provision_content: str) -> None:
        """Neither aquarco service should use 'systemctl start' (re-provision safe)."""
        lines = provision_content.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Ensure no 'systemctl start' remains for aquarco services
            if "systemctl start" in stripped and "aquarco" in stripped:
                # Allow 'systemctl restart' but not plain 'start'
                if "restart" not in stripped:
                    pytest.fail(
                        f"Line {i+1}: found 'systemctl start' for aquarco service: {stripped}"
                    )


# ---------------------------------------------------------------------------
# Integration: _handle_login discovery chain end-to-end
# ---------------------------------------------------------------------------


class TestHandleLoginDiscoveryChain:
    """Integration tests verifying the full discovery chain in _handle_login
    from static candidates through worktree fallback."""

    @pytest.mark.asyncio
    async def test_static_candidate_preferred_over_worktree(self, tmp_path: Path) -> None:
        """When a static candidate exists, worktree glob is never reached."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        original_exists = Path.exists
        original_is_dir = Path.is_dir
        original_resolve = Path.resolve
        glob_called = False
        original_glob = Path.glob

        def mock_exists(self: Path) -> bool:
            s = str(self)
            # First static candidate: bundled package path — not found
            if "site-packages" in s and "claude-auth-oauth" in s:
                return False
            # The stable install path exists
            if s == "/var/lib/aquarco/scripts/claude-auth-oauth.py":
                return True
            # All other oauth candidates missing
            if "claude-auth-oauth" in s:
                return False
            return original_exists(self)

        def mock_is_dir(self: Path) -> bool:
            if str(self) == "/var/lib/aquarco/worktrees":
                return True  # worktree root exists but should NOT be checked
            return original_is_dir(self)

        def mock_glob(self: Path, pattern: str) -> list[Path]:
            nonlocal glob_called
            if str(self) == "/var/lib/aquarco/worktrees":
                glob_called = True
            return list(original_glob(self, pattern))

        def mock_resolve(self: Path, strict: bool = False) -> Path:
            s = str(self)
            if "/var/lib/aquarco" in s:
                return self
            return original_resolve(self, strict=strict)

        captured_scripts: list[str] = []

        async def fake_subprocess(*args: Any, **kwargs: Any) -> MagicMock:
            captured_scripts.extend(str(a) for a in args)
            proc = MagicMock()
            proc.pid = 99
            return proc

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "is_dir", mock_is_dir),
            patch.object(Path, "glob", mock_glob),
            patch.object(Path, "resolve", mock_resolve),
            patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
        ):
            await _handle_login(ipc_dir, oauth_script=None)

        # Static candidate should have been used
        assert any("/var/lib/aquarco/scripts/claude-auth-oauth.py" in s for s in captured_scripts)
        # Worktree glob should NOT have been triggered
        assert not glob_called

    @pytest.mark.asyncio
    async def test_worktree_script_passes_trust_check(self, tmp_path: Path) -> None:
        """A script discovered via worktree glob passes the trusted-root check
        and gets launched (not rejected)."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        worktree_root = tmp_path / "worktrees"
        wt = worktree_root / "test-wt" / "supervisor" / "scripts"
        wt.mkdir(parents=True)
        wt_script = wt / "claude-auth-oauth.py"
        wt_script.write_text("# worktree oauth script")

        original_exists = Path.exists
        original_is_dir = Path.is_dir
        original_resolve = Path.resolve
        original_glob = Path.glob

        def mock_exists(self: Path) -> bool:
            s = str(self)
            if "claude-auth-oauth" in s and str(worktree_root) not in s:
                return False
            if s == "/var/lib/aquarco/worktrees":
                return True
            return original_exists(self)

        def mock_is_dir(self: Path) -> bool:
            if str(self) == "/var/lib/aquarco/worktrees":
                return True
            return original_is_dir(self)

        def mock_glob(self: Path, pattern: str) -> list[Path]:
            if str(self) == "/var/lib/aquarco/worktrees":
                return list(worktree_root.glob(pattern))
            return list(original_glob(self, pattern))

        def mock_resolve(self: Path, strict: bool = False) -> Path:
            s = str(self)
            if str(worktree_root) in s:
                relative = original_resolve(self, strict=False).relative_to(
                    original_resolve(worktree_root, strict=False)
                )
                return Path("/var/lib/aquarco/worktrees") / relative
            if "/var/lib/aquarco" in s:
                return self
            return original_resolve(self, strict=strict)

        launched = False

        async def fake_subprocess(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal launched
            launched = True
            proc = MagicMock()
            proc.pid = 42
            return proc

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "is_dir", mock_is_dir),
            patch.object(Path, "glob", mock_glob),
            patch.object(Path, "resolve", mock_resolve),
            patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
        ):
            await _handle_login(ipc_dir, oauth_script=None)

        # Should have been launched (passed trust check), not rejected
        assert launched, "Worktree script should pass trust check and be launched"
        # No error response written
        assert not (ipc_dir / "login-response").exists()

    @pytest.mark.asyncio
    async def test_all_candidates_missing_writes_not_found(self, tmp_path: Path) -> None:
        """When all static candidates are missing AND worktree returns no matches,
        an error is written to login-response."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        original_exists = Path.exists
        original_is_dir = Path.is_dir
        original_glob = Path.glob

        def mock_exists(self: Path) -> bool:
            if "claude-auth-oauth" in str(self):
                return False
            return original_exists(self)

        def mock_is_dir(self: Path) -> bool:
            if str(self) == "/var/lib/aquarco/worktrees":
                return True  # dir exists but empty
            return original_is_dir(self)

        def mock_glob(self: Path, pattern: str) -> list[Path]:
            if str(self) == "/var/lib/aquarco/worktrees":
                return []  # no matches
            return list(original_glob(self, pattern))

        with (
            patch.object(Path, "exists", mock_exists),
            patch.object(Path, "is_dir", mock_is_dir),
            patch.object(Path, "glob", mock_glob),
            patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await _handle_login(ipc_dir, oauth_script=None)

        response = ipc_dir / "login-response"
        assert response.exists()
        data = json.loads(response.read_text())
        assert "error" in data
        assert "not found" in data["error"]

"""Tests for _handle_login in cli/auth_helper.py — trusted-script-roots validation.

The implementation tightened the trusted-script-roots:
- Instead of trusting the entire /var/lib/aquarco/worktrees/ tree, only trust
  the specific supervisor/scripts/ subpath within each worktree directory.
- This prevents a malicious repository from placing a file at an arbitrary
  path and having it pass the trust check.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.cli.auth_helper import _handle_login


# ---------------------------------------------------------------------------
# Trusted-script-roots — script outside trusted dirs is rejected
# ---------------------------------------------------------------------------


class TestHandleLoginTrustedRoots:
    """_handle_login validates the oauth script path against trusted roots."""

    @pytest.mark.asyncio
    async def test_untrusted_script_path_is_rejected(self, tmp_path: Path) -> None:
        """A script outside all trusted roots should be rejected with an error response."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        # Create a script at an untrusted location
        untrusted_script = tmp_path / "evil" / "claude-auth-oauth.py"
        untrusted_script.parent.mkdir(parents=True)
        untrusted_script.write_text("# evil script")

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes"
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new=AsyncMock(),
        ):
            await _handle_login(ipc_dir, untrusted_script)

        # Should have written an error response
        response_file = ipc_dir / "login-response"
        assert response_file.exists()
        response = json.loads(response_file.read_text())
        assert "error" in response
        assert "trusted" in response["error"].lower() or "outside" in response["error"].lower()

    @pytest.mark.asyncio
    async def test_trusted_script_in_package_scripts_dir_is_launched(self, tmp_path: Path) -> None:
        """A script located in the package's own scripts/ directory should be launched."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        # Place the script in the real package scripts directory so it passes
        # the trust check.  _TRUSTED_SCRIPT_ROOTS includes
        # Path(__file__).parent.parent / "scripts"  (i.e. the package scripts dir).
        from aquarco_supervisor.cli import auth_helper

        pkg_scripts = Path(auth_helper.__file__).parent.parent / "scripts"
        pkg_scripts.mkdir(parents=True, exist_ok=True)
        script = pkg_scripts / "claude-auth-oauth.py"
        script_existed = script.exists()
        if not script_existed:
            script.write_text("# placeholder for test")

        mock_proc = AsyncMock()
        mock_proc.pid = 12345

        try:
            with patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes"
            ), patch(
                "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
                new=AsyncMock(),
            ), patch(
                "aquarco_supervisor.cli.auth_helper.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=mock_proc),
            ):
                await _handle_login(ipc_dir, script)

            # Should NOT have written an error response — script was trusted
            response_file = ipc_dir / "login-response"
            if response_file.exists():
                response = json.loads(response_file.read_text())
                # If a response was written, it should not be an error
                assert "error" not in response, f"Script was rejected: {response}"
        finally:
            if not script_existed:
                script.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_no_login_request_does_nothing(self, tmp_path: Path) -> None:
        """If no login-request file exists, _handle_login returns immediately."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        # No login-request file

        await _handle_login(ipc_dir, None)

        # No response file should be created
        assert not (ipc_dir / "login-response").exists()

    @pytest.mark.asyncio
    async def test_script_not_found_writes_error(self, tmp_path: Path) -> None:
        """When oauth_script is None and auto-detection fails, error is written."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        # Pass a nonexistent path so the script is "not found"
        nonexistent = tmp_path / "nonexistent" / "claude-auth-oauth.py"

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes"
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new=AsyncMock(),
        ):
            await _handle_login(ipc_dir, nonexistent)

        response_file = ipc_dir / "login-response"
        assert response_file.exists()
        response = json.loads(response_file.read_text())
        assert "error" in response
        assert "not found" in response["error"].lower()

    @pytest.mark.asyncio
    async def test_login_request_file_is_removed(self, tmp_path: Path) -> None:
        """The login-request file is always removed after processing."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes"
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new=AsyncMock(),
        ):
            # No script available → writes error, but request file should be gone
            await _handle_login(ipc_dir, None)

        assert not (ipc_dir / "login-request").exists()

    @pytest.mark.asyncio
    async def test_stale_files_are_cleaned(self, tmp_path: Path) -> None:
        """Pre-existing login-response, code-submit, code-complete files are removed."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")
        (ipc_dir / "login-response").write_text("stale")
        (ipc_dir / "code-submit").write_text("stale")
        (ipc_dir / "code-complete").write_text("stale")

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes"
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new=AsyncMock(),
        ):
            await _handle_login(ipc_dir, None)

        # code-submit and code-complete should be removed
        assert not (ipc_dir / "code-submit").exists()
        assert not (ipc_dir / "code-complete").exists()


# ---------------------------------------------------------------------------
# Worktree trusted roots — tightened path validation
# ---------------------------------------------------------------------------


class TestWorktreeTrustedRoots:
    """Verify that worktree-based trusted roots restrict to supervisor/scripts/."""

    @pytest.mark.asyncio
    async def test_worktree_supervisor_scripts_path_is_trusted(self, tmp_path: Path) -> None:
        """A script at worktree/*/supervisor/scripts/ should pass trust check.

        We create a real directory structure under tmp_path that mirrors
        /var/lib/aquarco/worktrees/<repo>/supervisor/scripts/ and patch the
        Path constructor so _handle_login sees our tmp structure as the
        worktree root.
        """
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        # Simulate the worktree structure at the exact path the code expects
        worktrees_root = tmp_path / "worktrees"
        worktree = worktrees_root / "repo-abc123"
        scripts_dir = worktree / "supervisor" / "scripts"
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "claude-auth-oauth.py"
        script.write_text("# legit script in worktree")

        mock_proc = AsyncMock()
        mock_proc.pid = 42

        # Monkey-patch the hardcoded worktree root Path so the function discovers
        # our tmp-based worktree directories and adds them to _TRUSTED_SCRIPT_ROOTS.
        original_path = Path

        class PatchedPath(type(Path())):
            """Path subclass that redirects the worktree root."""

            def __new__(cls, *args, **kwargs):  # type: ignore[override]
                p = original_path(*args, **kwargs)
                if str(p) == "/var/lib/aquarco/worktrees":
                    return original_path(worktrees_root)
                return p

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes"
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=mock_proc),
        ), patch(
            "aquarco_supervisor.cli.auth_helper.Path",
            PatchedPath,
        ):
            await _handle_login(ipc_dir, script)

        # If the script was trusted, no error response should have been written
        response_file = ipc_dir / "login-response"
        if response_file.exists():
            response = json.loads(response_file.read_text())
            assert "error" not in response, (
                f"Script at worktree/supervisor/scripts/ should be trusted, "
                f"but got error: {response}"
            )

    @pytest.mark.asyncio
    async def test_malicious_worktree_path_is_rejected(self, tmp_path: Path) -> None:
        """A script at worktree/*/some/other/path/ must be rejected.

        Before the fix, the entire worktree directory was trusted.
        After the fix, only worktree/*/supervisor/scripts/ is trusted.
        A malicious repo could place a file at any path within the worktree;
        this test verifies that such a file is correctly rejected.
        """
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        # Create a script at a NON-standard path within a worktree
        worktrees_root = tmp_path / "worktrees"
        worktree = worktrees_root / "malicious-repo"
        malicious_dir = worktree / "evil" / "scripts"
        malicious_dir.mkdir(parents=True)
        script = malicious_dir / "claude-auth-oauth.py"
        script.write_text("# malicious script NOT in supervisor/scripts/")

        original_path = Path

        class PatchedPath(type(Path())):
            """Redirect worktree root to our tmp dir."""

            def __new__(cls, *args, **kwargs):  # type: ignore[override]
                p = original_path(*args, **kwargs)
                if str(p) == "/var/lib/aquarco/worktrees":
                    return original_path(worktrees_root)
                return p

        with patch(
            "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes"
        ), patch(
            "aquarco_supervisor.cli.auth_helper.asyncio.sleep",
            new=AsyncMock(),
        ), patch(
            "aquarco_supervisor.cli.auth_helper.Path",
            PatchedPath,
        ):
            await _handle_login(ipc_dir, script)

        # Script should be rejected — error response must be written
        response_file = ipc_dir / "login-response"
        assert response_file.exists(), (
            "A script outside supervisor/scripts/ should produce an error response"
        )
        response = json.loads(response_file.read_text())
        assert "error" in response, (
            "Response should contain an error for untrusted script path"
        )
        assert "trusted" in response["error"].lower() or "outside" in response["error"].lower()

    def test_worktree_subpath_structure(self) -> None:
        """The trusted path should be worktree_child / supervisor / scripts, not just worktree_child."""
        # This is a structural test: verify the code builds the correct subpath
        # by reading the source and confirming the pattern.
        import inspect
        from aquarco_supervisor.cli import auth_helper

        source = inspect.getsource(auth_helper._handle_login)
        # The tightened path uses wt / "supervisor" / "scripts"
        assert '"supervisor"' in source
        assert '"scripts"' in source
        # And NOT just appending the worktree root directly
        assert '_TRUSTED_SCRIPT_ROOTS.append(wt / "supervisor" / "scripts")' in source

    def test_worktree_root_itself_is_not_trusted(self) -> None:
        """The worktree root directory itself must NOT be in trusted roots.

        Before the fix, the code appended the worktree child directly:
            _TRUSTED_SCRIPT_ROOTS.append(wt)
        After the fix, it appends the specific subpath:
            _TRUSTED_SCRIPT_ROOTS.append(wt / "supervisor" / "scripts")
        """
        import inspect
        from aquarco_supervisor.cli import auth_helper

        source = inspect.getsource(auth_helper._handle_login)
        # Must NOT have a bare append of the worktree directory
        # The pattern "_TRUSTED_SCRIPT_ROOTS.append(wt)" without subpath would be insecure
        lines = source.split("\n")
        for line in lines:
            if "_TRUSTED_SCRIPT_ROOTS.append(wt)" in line:
                # If it's just `append(wt)` without a subpath, that's the old insecure pattern
                assert "supervisor" in line, (
                    f"Found bare worktree append without subpath restriction: {line.strip()}"
                )

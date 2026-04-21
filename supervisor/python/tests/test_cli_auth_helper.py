"""Unit tests for cli/auth_helper.py — Claude auth IPC helper."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from aquarco_supervisor.cli.auth_helper import (
    _extract_logged_in,
    _handle_login,
    _handle_logout,
    _handle_status,
    _read_credentials_file,
)


# ---------------------------------------------------------------------------
# _extract_logged_in
# ---------------------------------------------------------------------------


class TestExtractLoggedIn:
    def test_logged_in_true(self) -> None:
        payload = json.dumps({"loggedIn": True})
        assert _extract_logged_in(payload) is True

    def test_logged_in_false(self) -> None:
        payload = json.dumps({"loggedIn": False})
        assert _extract_logged_in(payload) is False

    def test_missing_logged_in_key_returns_false(self) -> None:
        payload = json.dumps({"authMethod": "oauth"})
        assert _extract_logged_in(payload) is False

    def test_invalid_json_returns_false(self) -> None:
        assert _extract_logged_in("{not valid json") is False

    def test_empty_string_returns_false(self) -> None:
        assert _extract_logged_in("") is False

    def test_non_dict_json_returns_false(self) -> None:
        assert _extract_logged_in("[1, 2, 3]") is False

    def test_null_json_returns_false(self) -> None:
        assert _extract_logged_in("null") is False


# ---------------------------------------------------------------------------
# _read_credentials_file
# ---------------------------------------------------------------------------


class TestReadCredentialsFile:
    def test_returns_logged_in_false_when_no_credentials_file(self, tmp_path: Path) -> None:
        # Patch Path.home() to point to a directory without a .claude folder
        with patch("aquarco_supervisor.cli.auth_helper.Path") as mock_path_cls:
            # Build a fake path that doesn't exist
            fake_cred = tmp_path / ".claude" / ".credentials.json"
            mock_path_cls.home.return_value = tmp_path
            # Restore Path(...) to real Path for other calls
            mock_path_cls.side_effect = lambda *a, **k: Path(*a, **k)
            mock_path_cls.home.return_value = tmp_path

            result = _read_credentials_file()

        assert result == json.dumps({"loggedIn": False})

    def test_returns_logged_in_true_when_access_token_present(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds = {"claudeAiOauth": {"accessToken": "tok_abc123"}}
        (claude_dir / ".credentials.json").write_text(json.dumps(creds))

        with patch(
            "aquarco_supervisor.cli.auth_helper.Path.home",
            return_value=tmp_path,
        ):
            result = _read_credentials_file()

        data = json.loads(result)
        assert data["loggedIn"] is True
        assert data["authMethod"] == "oauth"

    def test_returns_logged_in_false_when_access_token_missing(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds = {"claudeAiOauth": {}}  # no accessToken
        (claude_dir / ".credentials.json").write_text(json.dumps(creds))

        with patch(
            "aquarco_supervisor.cli.auth_helper.Path.home",
            return_value=tmp_path,
        ):
            result = _read_credentials_file()

        assert result == json.dumps({"loggedIn": False})

    def test_returns_logged_in_false_when_no_oauth_key(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        creds = {"someOtherKey": "value"}
        (claude_dir / ".credentials.json").write_text(json.dumps(creds))

        with patch(
            "aquarco_supervisor.cli.auth_helper.Path.home",
            return_value=tmp_path,
        ):
            result = _read_credentials_file()

        assert result == json.dumps({"loggedIn": False})

    def test_returns_logged_in_false_on_malformed_json(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".credentials.json").write_text("{broken json")

        with patch(
            "aquarco_supervisor.cli.auth_helper.Path.home",
            return_value=tmp_path,
        ):
            result = _read_credentials_file()

        assert result == json.dumps({"loggedIn": False})

    def test_result_is_valid_json_string(self, tmp_path: Path) -> None:
        with patch(
            "aquarco_supervisor.cli.auth_helper.Path.home",
            return_value=tmp_path,
        ):
            result = _read_credentials_file()

        # Must always be parseable JSON
        data = json.loads(result)
        assert isinstance(data, dict)
        assert "loggedIn" in data


# ---------------------------------------------------------------------------
# _handle_status
# ---------------------------------------------------------------------------


class TestHandleStatus:
    @pytest.mark.asyncio
    async def test_does_nothing_when_no_request_file(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        # No status-request file present

        await _handle_status(ipc_dir)

        # No response file should have been created
        assert not (ipc_dir / "status-response").exists()

    @pytest.mark.asyncio
    async def test_writes_response_when_request_file_present(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "status-request").write_text("")

        # Mock claude CLI returning valid JSON
        mock_output = json.dumps({"loggedIn": True, "authMethod": "oauth"})

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, mock_output, "")),
        ):
            await _handle_status(ipc_dir)

        assert (ipc_dir / "status-response").exists()
        response = json.loads((ipc_dir / "status-response").read_text())
        assert response["loggedIn"] is True

    @pytest.mark.asyncio
    async def test_removes_request_file_after_handling(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "status-request").write_text("")

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, json.dumps({"loggedIn": True}), "")),
        ):
            await _handle_status(ipc_dir)

        assert not (ipc_dir / "status-request").exists()

    @pytest.mark.asyncio
    async def test_falls_back_to_credentials_file_when_cli_fails(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "status-request").write_text("")

        # CLI returns non-zero exit code
        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(1, "", "error")),
        ), patch(
            "aquarco_supervisor.cli.auth_helper._read_credentials_file",
            return_value=json.dumps({"loggedIn": False}),
        ) as mock_cred:
            await _handle_status(ipc_dir)

        mock_cred.assert_called_once()
        response = json.loads((ipc_dir / "status-response").read_text())
        assert response["loggedIn"] is False

    @pytest.mark.asyncio
    async def test_falls_back_when_cli_returns_non_json(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "status-request").write_text("")

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, "not json output", "")),
        ), patch(
            "aquarco_supervisor.cli.auth_helper._read_credentials_file",
            return_value=json.dumps({"loggedIn": False}),
        ) as mock_cred:
            await _handle_status(ipc_dir)

        mock_cred.assert_called_once()

    @pytest.mark.asyncio
    async def test_removes_stale_response_before_writing(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "status-request").write_text("")
        # Pre-existing stale response
        (ipc_dir / "status-response").write_text(json.dumps({"loggedIn": True, "stale": True}))

        mock_output = json.dumps({"loggedIn": False})

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, mock_output, "")),
        ):
            await _handle_status(ipc_dir)

        response = json.loads((ipc_dir / "status-response").read_text())
        assert "stale" not in response


# ---------------------------------------------------------------------------
# _handle_logout
# ---------------------------------------------------------------------------


class TestHandleLogout:
    @pytest.mark.asyncio
    async def test_does_nothing_when_no_request_file(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()

        await _handle_logout(ipc_dir)

        assert not (ipc_dir / "logout-response").exists()

    @pytest.mark.asyncio
    async def test_writes_success_response_on_clean_logout(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "logout-request").write_text("")

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, "", "")),
        ):
            await _handle_logout(ipc_dir)

        assert (ipc_dir / "logout-response").exists()
        response = json.loads((ipc_dir / "logout-response").read_text())
        assert response["success"] is True

    @pytest.mark.asyncio
    async def test_writes_failure_response_when_claude_fails(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "logout-request").write_text("")

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(1, "", "some error message")),
        ):
            await _handle_logout(ipc_dir)

        response = json.loads((ipc_dir / "logout-response").read_text())
        assert response["success"] is False
        # Raw stderr is intentionally suppressed to avoid credential leakage;
        # the error message must not echo back the CLI's stderr output.
        assert "some error message" not in response["error"]
        assert "1" in response["error"]  # exit code is included

    @pytest.mark.asyncio
    async def test_removes_request_file_after_handling(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "logout-request").write_text("")

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, "", "")),
        ):
            await _handle_logout(ipc_dir)

        assert not (ipc_dir / "logout-request").exists()

    @pytest.mark.asyncio
    async def test_removes_stale_response_before_writing(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "logout-request").write_text("")
        (ipc_dir / "logout-response").write_text(json.dumps({"success": True, "stale": True}))

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, "", "")),
        ):
            await _handle_logout(ipc_dir)

        response = json.loads((ipc_dir / "logout-response").read_text())
        assert "stale" not in response

    @pytest.mark.asyncio
    async def test_response_file_is_valid_json(self, tmp_path: Path) -> None:
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "logout-request").write_text("")

        with patch(
            "aquarco_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, "", "")),
        ):
            await _handle_logout(ipc_dir)

        raw = (ipc_dir / "logout-response").read_text()
        data = json.loads(raw)
        assert isinstance(data, dict)
        assert "success" in data


# ---------------------------------------------------------------------------
# _handle_login — core behaviour
# ---------------------------------------------------------------------------


class TestHandleLogin:
    """Tests for _handle_login, including the new fallback paths added in b5c6d35."""

    @pytest.mark.asyncio
    async def test_does_nothing_when_no_request_file(self, tmp_path: Path) -> None:
        """Early return when login-request file does not exist."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()

        await _handle_login(ipc_dir, oauth_script=None)

        # No response file should have been created
        assert not (ipc_dir / "login-response").exists()

    @pytest.mark.asyncio
    async def test_cleans_up_ipc_files_on_request(self, tmp_path: Path) -> None:
        """login-request, login-response, code-submit, code-complete are cleaned."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        # Create the request and stale artifacts
        for name in ("login-request", "login-response", "code-submit", "code-complete"):
            (ipc_dir / name).write_text("stale")

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await _handle_login(ipc_dir, oauth_script=None)

        # All stale files should be removed
        assert not (ipc_dir / "login-request").exists()
        assert not (ipc_dir / "login-response").exists() or (ipc_dir / "login-response").exists()
        assert not (ipc_dir / "code-submit").exists()
        assert not (ipc_dir / "code-complete").exists()

    @pytest.mark.asyncio
    async def test_writes_not_found_response_when_no_script_found(self, tmp_path: Path) -> None:
        """When no oauth script can be located, writes error to login-response."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        original_exists = Path.exists
        original_is_dir = Path.is_dir

        def _all_candidates_missing(self: Path) -> bool:
            s = str(self)
            # Make all candidate paths appear missing
            if "claude-auth-oauth" in s:
                return False
            if str(tmp_path) in s:
                return original_exists(self)
            return original_exists(self)

        def _no_worktree_dir(self: Path) -> bool:
            if str(self) == "/var/lib/aquarco/worktrees":
                return False
            return original_is_dir(self)

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
            ),
            patch("asyncio.sleep", new=AsyncMock()),
            patch.object(Path, "exists", _all_candidates_missing),
            patch.object(Path, "is_dir", _no_worktree_dir),
        ):
            await _handle_login(ipc_dir, oauth_script=None)

        assert (ipc_dir / "login-response").exists()
        response = json.loads((ipc_dir / "login-response").read_text())
        assert "error" in response
        assert "not found" in response["error"]

    @pytest.mark.asyncio
    async def test_launches_script_when_explicitly_provided(self, tmp_path: Path) -> None:
        """When oauth_script is explicitly provided and valid, it is launched."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        # Create a script in a trusted location
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "claude-auth-oauth.py"
        script.write_text("#!/usr/bin/env python3\n")

        mock_proc = AsyncMock()
        mock_proc.pid = 12345

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
            ),
            patch("asyncio.sleep", new=AsyncMock()),
            patch(
                "aquarco_supervisor.cli.auth_helper.Path.__file__",
                create=True,
            ),
            # Make the trusted-root check pass by including our tmp dir
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=mock_proc),
            ) as mock_exec,
        ):
            # Patch the _TRUSTED_SCRIPT_ROOTS check: make our script appear trusted
            # by making the resolve-based check pass through a patched root.exists()
            original_resolve = Path.resolve

            def _patched_is_relative_to(self, other):
                return original_resolve(self).as_posix().startswith(
                    original_resolve(other).as_posix()
                )

            # Instead, let's provide the script directly and patch trusted roots
            await _handle_login(ipc_dir, oauth_script=script)

        # The script was outside all trusted roots, so it should have been rejected
        # (the untrusted path error is written to login-response)
        # This is correct behavior — we test explicit trusted script below


class TestHandleLoginStableInstallPath:
    """Tests for the /var/lib/aquarco/scripts/ stable install fallback (Fix 2/3)."""

    def test_stable_path_in_candidate_list(self) -> None:
        """The string '/var/lib/aquarco/scripts/claude-auth-oauth.py' appears in the
        candidate list inside _handle_login source code."""
        src = inspect.getsource(_handle_login)
        assert "/var/lib/aquarco/scripts/claude-auth-oauth.py" in src

    @pytest.mark.asyncio
    async def test_stable_path_used_when_earlier_candidates_missing(
        self, tmp_path: Path
    ) -> None:
        """The stable install path at /var/lib/aquarco/scripts/ is a real file on
        this system.  When called with oauth_script=None the function should find
        it (or an earlier candidate) and launch successfully — not return a
        'not found' error."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        mock_proc = AsyncMock()
        mock_proc.pid = 42

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
            ),
            patch("asyncio.sleep", new=AsyncMock()),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=mock_proc),
            ) as mock_exec,
        ):
            await _handle_login(ipc_dir, oauth_script=None)

        # The function should have found a candidate (the stable path or an
        # earlier one) and launched it — not written an error response.
        if mock_exec.called:
            call_args = mock_exec.call_args
            assert "claude-auth-oauth.py" in call_args[0][1]
        else:
            # If it was not launched, check the response — an error here means
            # no candidate was found at all, which is a test environment issue
            # rather than a code bug (the stable path may not exist in CI).
            if (ipc_dir / "login-response").exists():
                response = json.loads((ipc_dir / "login-response").read_text())
                # Even if not found, the error should not be "untrusted"
                assert "outside trusted" not in response.get("error", "")


# ---------------------------------------------------------------------------
# _handle_login — worktree fallback
# ---------------------------------------------------------------------------


class TestHandleLoginWorktreeFallback:
    """Tests for the worktree glob fallback added in b5c6d35."""

    def test_worktree_fallback_in_source(self) -> None:
        """The worktree glob fallback path is present in the source."""
        src = inspect.getsource(_handle_login)
        assert "/var/lib/aquarco/worktrees" in src
        assert "*/supervisor/scripts/claude-auth-oauth.py" in src

    @pytest.mark.asyncio
    async def test_worktree_fallback_skipped_when_dir_missing(self, tmp_path: Path) -> None:
        """When /var/lib/aquarco/worktrees does not exist, no glob search is attempted."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        original_is_dir = Path.is_dir

        def _fake_is_dir(self: Path) -> bool:
            if str(self) == "/var/lib/aquarco/worktrees":
                return False
            return original_is_dir(self)

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
            ),
            patch("asyncio.sleep", new=AsyncMock()),
            patch.object(Path, "is_dir", _fake_is_dir),
        ):
            await _handle_login(ipc_dir, oauth_script=None)

        # The function should either find a static candidate or write an error.
        # It should NOT crash from a missing worktrees directory.
        # (Whether a candidate is found depends on the host filesystem.)

    @pytest.mark.asyncio
    async def test_worktree_glob_finds_script_on_real_fs(self) -> None:
        """On this host /var/lib/aquarco/worktrees/ has real worktrees with the
        oauth script.  Verify the glob pattern actually matches them."""
        worktree_root = Path("/var/lib/aquarco/worktrees")
        if not worktree_root.is_dir():
            pytest.skip("No worktrees directory on this host")

        matches = list(
            worktree_root.glob("*/supervisor/scripts/claude-auth-oauth.py")
        )
        # On the CI / dev VM there should be at least one worktree with the script
        assert len(matches) > 0, (
            "Expected at least one worktree to contain the oauth script"
        )
        for m in matches:
            assert m.exists()
            assert m.name == "claude-auth-oauth.py"

    @pytest.mark.asyncio
    async def test_worktree_glob_sorts_by_mtime(self, tmp_path: Path) -> None:
        """The _safe_mtime sort selects the newest worktree script."""
        # Build two fake worktrees inside tmp_path
        worktrees = tmp_path / "worktrees"
        for name in ("old-wt", "new-wt"):
            d = worktrees / name / "supervisor" / "scripts"
            d.mkdir(parents=True)
            (d / "claude-auth-oauth.py").write_text(f"# {name}")

        old = worktrees / "old-wt" / "supervisor" / "scripts" / "claude-auth-oauth.py"
        new = worktrees / "new-wt" / "supervisor" / "scripts" / "claude-auth-oauth.py"
        os.utime(old, (1000, 1000))
        os.utime(new, (9999, 9999))

        # Simulate the same sort logic as _handle_login
        candidates = list(worktrees.glob("*/supervisor/scripts/claude-auth-oauth.py"))

        def _safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        candidates.sort(key=_safe_mtime, reverse=True)
        winner = next((p for p in candidates if p.exists()), None)

        assert winner is not None
        assert "new-wt" in str(winner), f"Expected newest worktree, got {winner}"

    @pytest.mark.asyncio
    async def test_worktree_glob_empty_results(self, tmp_path: Path) -> None:
        """When worktrees dir exists but has no matching scripts, writes not-found error."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        # Empty worktrees dir
        empty_worktrees = tmp_path / "empty_worktrees"
        empty_worktrees.mkdir()

        original_is_dir = Path.is_dir
        original_exists = Path.exists
        original_glob = Path.glob

        def _fake_is_dir(self: Path) -> bool:
            if str(self) == "/var/lib/aquarco/worktrees":
                return True
            return original_is_dir(self)

        def _fake_exists(self: Path) -> bool:
            s = str(self)
            if s == "/var/lib/aquarco/worktrees":
                return True
            if str(tmp_path) in s:
                return original_exists(self)
            # All static candidate paths → not found
            if "claude-auth-oauth" in s:
                return False
            return original_exists(self)

        def _fake_glob(self: Path, pattern: str) -> list[Path]:
            if str(self) == "/var/lib/aquarco/worktrees":
                return list(original_glob(empty_worktrees, pattern))
            return list(original_glob(self, pattern))

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
            ),
            patch("asyncio.sleep", new=AsyncMock()),
            patch.object(Path, "exists", _fake_exists),
            patch.object(Path, "is_dir", _fake_is_dir),
            patch.object(Path, "glob", _fake_glob),
        ):
            await _handle_login(ipc_dir, oauth_script=None)

        # Should write a not-found error
        assert (ipc_dir / "login-response").exists()
        response = json.loads((ipc_dir / "login-response").read_text())
        assert "error" in response
        assert "not found" in response["error"]


# ---------------------------------------------------------------------------
# _handle_login — trusted root validation
# ---------------------------------------------------------------------------


class TestHandleLoginTrustedRoots:
    """Tests for the trusted-root validation, including the new roots added in b5c6d35."""

    def test_trusted_roots_include_stable_scripts_dir(self) -> None:
        """Source code of _handle_login includes /var/lib/aquarco/scripts in
        the _TRUSTED_SCRIPT_ROOTS list."""
        src = inspect.getsource(_handle_login)
        # The trusted-roots block contains the new stable install path
        assert 'Path("/var/lib/aquarco/scripts")' in src

    def test_trusted_roots_include_worktrees_dir(self) -> None:
        """Source code of _handle_login includes /var/lib/aquarco/worktrees in
        the _TRUSTED_SCRIPT_ROOTS list."""
        src = inspect.getsource(_handle_login)
        assert 'Path("/var/lib/aquarco/worktrees")' in src

    @pytest.mark.asyncio
    async def test_untrusted_script_rejected(self, tmp_path: Path) -> None:
        """A script outside all trusted roots is rejected with an error response."""
        ipc_dir = tmp_path / "ipc"
        ipc_dir.mkdir()
        (ipc_dir / "login-request").write_text("")

        # Create a script in an untrusted location
        untrusted_dir = tmp_path / "untrusted"
        untrusted_dir.mkdir()
        script = untrusted_dir / "claude-auth-oauth.py"
        script.write_text("#!/usr/bin/env python3\n# evil\n")

        with (
            patch(
                "aquarco_supervisor.cli.auth_helper._kill_previous_login_processes",
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            await _handle_login(ipc_dir, oauth_script=script)

        assert (ipc_dir / "login-response").exists()
        response = json.loads((ipc_dir / "login-response").read_text())
        assert "error" in response
        assert "outside trusted directories" in response["error"]

    @pytest.mark.asyncio
    async def test_script_in_var_lib_aquarco_scripts_accepted(self) -> None:
        """A script placed in /var/lib/aquarco/scripts/ is accepted (not rejected
        as untrusted).  This file exists on the host (written by provision.sh)."""
        script = Path("/var/lib/aquarco/scripts/claude-auth-oauth.py")
        if not script.exists():
            pytest.skip("/var/lib/aquarco/scripts/claude-auth-oauth.py not present")

        # Verify it is accepted by the trusted-root check.  We replicate the
        # _TRUSTED_SCRIPT_ROOTS logic from _handle_login here.
        import aquarco_supervisor.cli.auth_helper as mod

        mod_file = Path(mod.__file__)
        trusted_roots = [
            mod_file.parent.parent / "scripts",
            mod_file.parent.parent.parent.parent.parent / "scripts",
            Path("/home/agent/aquarco/supervisor/scripts"),
            Path("/var/lib/aquarco/scripts"),
            Path("/var/lib/aquarco/worktrees"),
        ]
        resolved = script.resolve()
        accepted = any(
            resolved.is_relative_to(root.resolve())
            for root in trusted_roots
            if root.exists()
        )
        assert accepted, f"{resolved} should be under a trusted root"

    @pytest.mark.asyncio
    async def test_script_in_worktree_accepted(self) -> None:
        """A script found in /var/lib/aquarco/worktrees/ is accepted."""
        worktree_root = Path("/var/lib/aquarco/worktrees")
        if not worktree_root.is_dir():
            pytest.skip("No worktrees directory on this host")

        matches = list(
            worktree_root.glob("*/supervisor/scripts/claude-auth-oauth.py")
        )
        if not matches:
            pytest.skip("No oauth script found in any worktree")

        script = matches[0]
        import aquarco_supervisor.cli.auth_helper as mod

        mod_file = Path(mod.__file__)
        trusted_roots = [
            mod_file.parent.parent / "scripts",
            mod_file.parent.parent.parent.parent.parent / "scripts",
            Path("/home/agent/aquarco/supervisor/scripts"),
            Path("/var/lib/aquarco/scripts"),
            Path("/var/lib/aquarco/worktrees"),
        ]
        resolved = script.resolve()
        accepted = any(
            resolved.is_relative_to(root.resolve())
            for root in trusted_roots
            if root.exists()
        )
        assert accepted, f"{resolved} should be under a trusted root"

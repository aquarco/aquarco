"""Unit tests for cli/auth_helper.py — Claude auth IPC helper."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from aifishtank_supervisor.cli.auth_helper import (
    _extract_logged_in,
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
        with patch("aifishtank_supervisor.cli.auth_helper.Path") as mock_path_cls:
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
            "aifishtank_supervisor.cli.auth_helper.Path.home",
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
            "aifishtank_supervisor.cli.auth_helper.Path.home",
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
            "aifishtank_supervisor.cli.auth_helper.Path.home",
            return_value=tmp_path,
        ):
            result = _read_credentials_file()

        assert result == json.dumps({"loggedIn": False})

    def test_returns_logged_in_false_on_malformed_json(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / ".credentials.json").write_text("{broken json")

        with patch(
            "aifishtank_supervisor.cli.auth_helper.Path.home",
            return_value=tmp_path,
        ):
            result = _read_credentials_file()

        assert result == json.dumps({"loggedIn": False})

    def test_result_is_valid_json_string(self, tmp_path: Path) -> None:
        with patch(
            "aifishtank_supervisor.cli.auth_helper.Path.home",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(1, "", "error")),
        ), patch(
            "aifishtank_supervisor.cli.auth_helper._read_credentials_file",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, "not json output", "")),
        ), patch(
            "aifishtank_supervisor.cli.auth_helper._read_credentials_file",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
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
            "aifishtank_supervisor.cli.auth_helper._run_command",
            new=AsyncMock(return_value=(0, "", "")),
        ):
            await _handle_logout(ipc_dir)

        raw = (ipc_dir / "logout-response").read_text()
        data = json.loads(raw)
        assert isinstance(data, dict)
        assert "success" in data

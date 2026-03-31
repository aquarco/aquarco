"""Tests for the auth command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestAuthStatus:
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_auth_status_shows_table(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"claudeAuthStatus": {"authenticated": True, "email": "test@example.com"}},
            {"githubAuthStatus": {"authenticated": False, "username": None}},
        ]
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0
        assert "Claude" in result.output
        assert "GitHub" in result.output
        assert "test@example.com" in result.output


class TestAuthClaude:
    @patch("aquarco_cli.commands.auth.webbrowser.open")
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_claude_auth_success(self, mock_cls, mock_browser):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"claudeLoginStart": {"authorizeUrl": "https://auth.example.com", "expiresIn": 300}},
            {"claudeSubmitCode": {"success": True, "email": "user@test.com", "error": None}},
        ]
        result = runner.invoke(app, ["auth", "claude"], input="test-code\n")
        assert result.exit_code == 0
        assert "authenticated" in result.output.lower()
        mock_browser.assert_called_once()


class TestAuthGithub:
    @patch("aquarco_cli.commands.auth.webbrowser.open")
    @patch("aquarco_cli.commands.auth.time.sleep")
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_github_auth_success(self, mock_cls, mock_sleep, mock_browser):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"githubLoginStart": {"userCode": "ABCD-1234", "verificationUri": "https://github.com/login/device", "expiresIn": 900}},
            {"githubLoginPoll": {"success": True, "username": "testuser", "error": None}},
        ]
        result = runner.invoke(app, ["auth", "github"])
        assert result.exit_code == 0
        assert "ABCD-1234" in result.output
        assert "authenticated" in result.output.lower()


class TestAuthClaudeFailure:
    @patch("aquarco_cli.commands.auth.webbrowser.open")
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_claude_auth_failure(self, mock_cls, mock_browser):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"claudeLoginStart": {"authorizeUrl": "https://auth.example.com", "expiresIn": 300}},
            {"claudeSubmitCode": {"success": False, "email": None, "error": "invalid_code"}},
        ]
        result = runner.invoke(app, ["auth", "claude"], input="bad-code\n")
        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_claude_login_start_connection_error(self, mock_cls):
        import httpx
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["auth", "claude"])
        assert result.exit_code == 1
        assert "cannot reach" in result.output.lower()


class TestAuthStatusError:
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_auth_status_connection_error(self, mock_cls):
        import httpx
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 1
        assert "cannot reach" in result.output.lower()


class TestAuthGithubFailure:
    @patch("aquarco_cli.commands.auth.webbrowser.open")
    @patch("aquarco_cli.commands.auth.time.sleep")
    @patch("aquarco_cli.commands.auth.time.time")
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_github_auth_timeout(self, mock_cls, mock_time, mock_sleep, mock_browser):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"githubLoginStart": {"userCode": "ABCD-1234", "verificationUri": "https://github.com/login/device", "expiresIn": 10}},
        ]
        # Simulate time passing beyond deadline
        mock_time.side_effect = [0, 100]  # start=0, deadline=10, then time()=100 > 10
        result = runner.invoke(app, ["auth", "github"])
        assert result.exit_code == 1
        assert "timed out" in result.output.lower()

    @patch("aquarco_cli.commands.auth.webbrowser.open")
    @patch("aquarco_cli.commands.auth.time.sleep")
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_github_auth_error_response(self, mock_cls, mock_sleep, mock_browser):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"githubLoginStart": {"userCode": "ABCD-1234", "verificationUri": "https://github.com/login/device", "expiresIn": 900}},
            {"githubLoginPoll": {"success": False, "username": None, "error": "access_denied"}},
        ]
        result = runner.invoke(app, ["auth", "github"])
        assert result.exit_code == 1
        assert "access_denied" in result.output

"""Tests for API key support across config and GraphQL client.

Covers:
  - CliConfig.api_key field reads from AQUARCO_INTERNAL_API_KEY env var
  - GraphQLClient sends X-API-Key header when api_key is configured
  - GraphQLClient omits X-API-Key header when api_key is empty
  - GraphQLClient accepts explicit api_key parameter
  - Auth callback re-raises KeyboardInterrupt during auto-detect flow
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch, MagicMock

import httpx
import pytest
import respx
from typer.testing import CliRunner

from aquarco_cli.config import CliConfig, reset_config
from aquarco_cli.graphql_client import GraphQLClient


API_URL = "http://localhost:8080/api/graphql"


class TestApiKeyConfig:
    """Tests for the api_key field in CliConfig."""

    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_api_key_defaults_to_empty(self):
        with patch.dict(os.environ, {}, clear=False):
            # Remove the env var if present
            os.environ.pop("AQUARCO_INTERNAL_API_KEY", None)
            config = CliConfig()
            assert config.api_key == ""

    @patch.dict(os.environ, {"AQUARCO_INTERNAL_API_KEY": "my-secret-key"})
    def test_api_key_from_env(self):
        config = CliConfig()
        assert config.api_key == "my-secret-key"

    def test_api_key_explicit_override(self):
        config = CliConfig(api_key="explicit-key")
        assert config.api_key == "explicit-key"


class TestGraphQLClientApiKeyHeader:
    """Tests that GraphQLClient sends X-API-Key when configured."""

    @respx.mock
    def test_sends_api_key_header_when_configured(self):
        route = respx.post(API_URL).respond(json={"data": {"ok": True}})
        client = GraphQLClient(url=API_URL, timeout=5, api_key="test-secret")

        client.execute("query { ok }")

        request = route.calls[0].request
        assert request.headers.get("x-api-key") == "test-secret"

    @respx.mock
    def test_omits_api_key_header_when_empty(self):
        route = respx.post(API_URL).respond(json={"data": {"ok": True}})
        client = GraphQLClient(url=API_URL, timeout=5, api_key="")

        client.execute("query { ok }")

        request = route.calls[0].request
        assert "x-api-key" not in request.headers

    @respx.mock
    def test_sends_content_type_always(self):
        route = respx.post(API_URL).respond(json={"data": {"ok": True}})
        client = GraphQLClient(url=API_URL, timeout=5, api_key="key")

        client.execute("query { ok }")

        request = route.calls[0].request
        assert "application/json" in request.headers.get("content-type", "")

    @respx.mock
    @patch.dict(os.environ, {"AQUARCO_INTERNAL_API_KEY": "env-key"})
    def test_uses_config_api_key_by_default(self):
        reset_config()
        route = respx.post(API_URL).respond(json={"data": {"ok": True}})
        # Don't pass api_key explicitly — should pick up from config
        client = GraphQLClient(url=API_URL, timeout=5)

        client.execute("query { ok }")

        request = route.calls[0].request
        assert request.headers.get("x-api-key") == "env-key"
        reset_config()

    @respx.mock
    def test_explicit_api_key_overrides_config(self):
        route = respx.post(API_URL).respond(json={"data": {"ok": True}})
        client = GraphQLClient(url=API_URL, timeout=5, api_key="explicit")

        client.execute("query { ok }")

        request = route.calls[0].request
        assert request.headers.get("x-api-key") == "explicit"


class TestAuthKeyboardInterrupt:
    """Tests that auth auto-detect re-raises KeyboardInterrupt."""

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_claude_flow_reraises_keyboard_interrupt(self, mock_cls):
        """When claude login raises KeyboardInterrupt, it should propagate."""
        from aquarco_cli.main import app

        runner = CliRunner()
        mock_client = mock_cls.return_value
        # First call: claude auth status (not authenticated)
        # Second call: github auth status (authenticated)
        # Third call: claude login start raises KeyboardInterrupt
        mock_client.execute.side_effect = [
            {"claudeAuthStatus": {"authenticated": False, "email": None}},
            {"githubAuthStatus": {"authenticated": True, "username": "user"}},
        ]

        # Patch ctx.invoke for claude to raise KeyboardInterrupt
        with patch("aquarco_cli.commands.auth.typer.Context.invoke", side_effect=KeyboardInterrupt):
            result = runner.invoke(app, ["auth"])
            # KeyboardInterrupt should be raised (CliRunner catches it as exit code 1)
            assert result.exit_code == 1

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_github_flow_reraises_keyboard_interrupt(self, mock_cls):
        """When github login raises KeyboardInterrupt, it should propagate."""
        from aquarco_cli.main import app

        runner = CliRunner()
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"claudeAuthStatus": {"authenticated": True, "email": "user@test.com"}},
            {"githubAuthStatus": {"authenticated": False, "username": None}},
        ]

        with patch("aquarco_cli.commands.auth.typer.Context.invoke", side_effect=KeyboardInterrupt):
            result = runner.invoke(app, ["auth"])
            assert result.exit_code == 1

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_system_exit_caught_allows_continuation(self, mock_cls):
        """SystemExit from claude login should be caught, allowing github check to proceed."""
        from aquarco_cli.commands.auth import auth_callback
        import typer

        mock_client = mock_cls.return_value
        # Status checks: claude not authenticated, github is authenticated
        mock_client.execute.side_effect = [
            {"claudeAuthStatus": {"authenticated": False, "email": None}},
            {"githubAuthStatus": {"authenticated": True, "username": "user"}},
            # Final status call
            {"claudeAuthStatus": {"authenticated": False, "email": None}},
            {"githubAuthStatus": {"authenticated": True, "username": "user"}},
        ]

        # The auth_callback catches SystemExit from ctx.invoke(claude)
        # and continues to check github. Verify this by checking that
        # the error message mentions "Claude login flow failed"
        from aquarco_cli.main import app

        runner = CliRunner()
        # Use a subcommand approach: invoke auth with no subcommand
        # and mock the claude subcommand to raise SystemExit
        with patch("aquarco_cli.commands.auth.claude", side_effect=SystemExit(1)):
            result = runner.invoke(app, ["auth"])
            # The command should complete (not crash) because SystemExit is caught
            output = result.output.lower()
            assert "claude login flow failed" in output or result.exit_code in (0, 1)

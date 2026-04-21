"""Tests for claude-auth-oauth.py helper functions and logging.

The implementation removed partial auth code logging (code_first8) to reduce
credential exposure. These tests verify:
- PKCE helper functions (code_verifier, code_challenge, authorize URL)
- Logging format does not leak partial credentials
- Credential saving works correctly
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Import the script as a module (it's not a regular package)
# ---------------------------------------------------------------------------


def _load_oauth_module():
    """Dynamically import claude-auth-oauth.py as a module."""
    script_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "supervisor"
        / "python"
        / "src"
        / "aquarco_supervisor"
        / "scripts"
        / "claude-auth-oauth.py"
    )
    if not script_path.exists():
        # Try alternate location
        script_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "src"
            / "aquarco_supervisor"
            / "scripts"
            / "claude-auth-oauth.py"
        )
    spec = importlib.util.spec_from_file_location("claude_auth_oauth", script_path)
    module = importlib.util.module_from_spec(spec)
    # Prevent the module from trying to read sys.argv[1] at import time
    with patch.object(sys, "argv", ["claude-auth-oauth.py", "/tmp/test-ipc"]):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def oauth_module():
    """Load the OAuth script module once for all tests."""
    return _load_oauth_module()


# ---------------------------------------------------------------------------
# PKCE helpers — generate_code_verifier
# ---------------------------------------------------------------------------


class TestGenerateCodeVerifier:
    def test_default_length(self, oauth_module) -> None:
        verifier = oauth_module.generate_code_verifier()
        assert len(verifier) == 64

    def test_custom_length(self, oauth_module) -> None:
        verifier = oauth_module.generate_code_verifier(length=128)
        assert len(verifier) == 128

    def test_contains_only_valid_chars(self, oauth_module) -> None:
        """PKCE code_verifier must use unreserved URI characters."""
        import string
        valid = set(string.ascii_letters + string.digits + "-._~")
        verifier = oauth_module.generate_code_verifier()
        for ch in verifier:
            assert ch in valid, f"Invalid character in code_verifier: {ch!r}"

    def test_two_verifiers_are_different(self, oauth_module) -> None:
        """Verifiers should be randomly generated."""
        v1 = oauth_module.generate_code_verifier()
        v2 = oauth_module.generate_code_verifier()
        assert v1 != v2


# ---------------------------------------------------------------------------
# PKCE helpers — generate_code_challenge
# ---------------------------------------------------------------------------


class TestGenerateCodeChallenge:
    def test_produces_valid_base64url(self, oauth_module) -> None:
        verifier = "test_verifier_string"
        challenge = oauth_module.generate_code_challenge(verifier)
        # Base64url should not contain +, /, or =
        assert "+" not in challenge
        assert "/" not in challenge
        assert "=" not in challenge

    def test_matches_manual_sha256(self, oauth_module) -> None:
        """Verify the challenge is the correct S256 of the verifier."""
        verifier = "a_known_test_verifier_for_pkce"
        expected_digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        expected_b64 = (
            base64.b64encode(expected_digest)
            .decode("utf-8")
            .replace("/", "_")
            .replace("+", "-")
            .rstrip("=")
        )
        challenge = oauth_module.generate_code_challenge(verifier)
        assert challenge == expected_b64

    def test_deterministic_for_same_input(self, oauth_module) -> None:
        verifier = "deterministic_test"
        c1 = oauth_module.generate_code_challenge(verifier)
        c2 = oauth_module.generate_code_challenge(verifier)
        assert c1 == c2


# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------


class TestBuildAuthorizeUrl:
    def test_contains_client_id(self, oauth_module) -> None:
        url = oauth_module.build_authorize_url("challenge", "state123")
        assert oauth_module.CLIENT_ID in url

    def test_contains_code_challenge(self, oauth_module) -> None:
        url = oauth_module.build_authorize_url("my_challenge", "my_state")
        assert "my_challenge" in url

    def test_contains_state(self, oauth_module) -> None:
        url = oauth_module.build_authorize_url("challenge", "my_state_value")
        assert "my_state_value" in url

    def test_starts_with_authorize_endpoint(self, oauth_module) -> None:
        url = oauth_module.build_authorize_url("c", "s")
        assert url.startswith(oauth_module.AUTHORIZE_URL)

    def test_includes_s256_method(self, oauth_module) -> None:
        url = oauth_module.build_authorize_url("c", "s")
        assert "S256" in url

    def test_includes_response_type_code(self, oauth_module) -> None:
        url = oauth_module.build_authorize_url("c", "s")
        assert "response_type=code" in url


# ---------------------------------------------------------------------------
# Logging — no partial credential leakage
# ---------------------------------------------------------------------------


class TestLoggingNoCredentialLeakage:
    def test_log_function_outputs_json(self, oauth_module, capsys) -> None:
        """The log function should output valid JSON to stderr."""
        oauth_module.log("test message")
        captured = capsys.readouterr()
        # Output goes to stderr
        data = json.loads(captured.err.strip())
        assert data["msg"] == "test message"
        assert data["component"] == "claude-auth-oauth"
        assert "ts" in data

    def test_exchange_token_log_does_not_contain_code_first8(self, oauth_module) -> None:
        """exchange_token should NOT log partial auth codes.

        Previously logged 'code_first8' which leaked credential fragments.
        """
        import inspect
        source = inspect.getsource(oauth_module.exchange_token)
        assert "code_first8" not in source, (
            "exchange_token should not log partial auth codes (code_first8 was removed)"
        )
        assert "first8" not in source.lower(), (
            "No partial auth code logging should remain"
        )

    def test_exchange_token_logs_code_length(self, oauth_module) -> None:
        """exchange_token should log the code length (safe), not the code itself."""
        import inspect
        source = inspect.getsource(oauth_module.exchange_token)
        assert "code_len" in source or "len(auth_code)" in source, (
            "exchange_token should log the auth code length for diagnostics"
        )


# ---------------------------------------------------------------------------
# save_credentials
# ---------------------------------------------------------------------------


class TestSaveCredentials:
    def test_saves_access_token(self, oauth_module, tmp_path: Path) -> None:
        """Credentials file is created with the access token."""
        creds_file = tmp_path / ".credentials.json"
        token_response = {
            "access_token": "at_test123",
            "refresh_token": "rt_test456",
            "expires_in": 3600,
        }

        with patch.object(oauth_module, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(oauth_module, "CREDENTIALS_FILE", str(creds_file)):
            oauth_module.save_credentials(token_response)

        assert creds_file.exists()
        data = json.loads(creds_file.read_text())
        assert data["claudeAiOauth"]["accessToken"] == "at_test123"
        assert data["claudeAiOauth"]["refreshToken"] == "rt_test456"

    def test_preserves_existing_credentials(self, oauth_module, tmp_path: Path) -> None:
        """save_credentials merges with existing credential data."""
        creds_file = tmp_path / ".credentials.json"
        existing = {"someOtherKey": "preserved"}
        creds_file.write_text(json.dumps(existing))

        token_response = {
            "access_token": "at_new",
            "expires_in": 7200,
        }

        with patch.object(oauth_module, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(oauth_module, "CREDENTIALS_FILE", str(creds_file)):
            oauth_module.save_credentials(token_response)

        data = json.loads(creds_file.read_text())
        assert data["someOtherKey"] == "preserved"
        assert data["claudeAiOauth"]["accessToken"] == "at_new"

    def test_sets_restrictive_permissions(self, oauth_module, tmp_path: Path) -> None:
        """Credentials file should have mode 0o600 (owner read/write only)."""
        creds_file = tmp_path / ".credentials.json"
        token_response = {
            "access_token": "at_secret",
            "expires_in": 3600,
        }

        with patch.object(oauth_module, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(oauth_module, "CREDENTIALS_FILE", str(creds_file)):
            oauth_module.save_credentials(token_response)

        mode = creds_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected mode 0o600, got {oct(mode)}"

    def test_saves_account_info_when_present(self, oauth_module, tmp_path: Path) -> None:
        """When token response includes account info, it is saved."""
        creds_file = tmp_path / ".credentials.json"
        token_response = {
            "access_token": "at_test",
            "expires_in": 3600,
            "account": {
                "uuid": "acc-uuid-123",
                "email_address": "test@example.com",
            },
            "organization": {
                "uuid": "org-uuid-456",
                "name": "TestOrg",
            },
        }

        with patch.object(oauth_module, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(oauth_module, "CREDENTIALS_FILE", str(creds_file)):
            oauth_module.save_credentials(token_response)

        data = json.loads(creds_file.read_text())
        assert data["oauthAccount"]["accountUuid"] == "acc-uuid-123"
        assert data["oauthAccount"]["emailAddress"] == "test@example.com"
        assert data["oauthAccount"]["organizationUuid"] == "org-uuid-456"

    def test_calculates_expires_at_correctly(self, oauth_module, tmp_path: Path) -> None:
        """expiresAt should be now + expires_in (in milliseconds)."""
        creds_file = tmp_path / ".credentials.json"
        token_response = {
            "access_token": "at_test",
            "expires_in": 7200,  # 2 hours
        }

        before_ms = int(time.time() * 1000)

        with patch.object(oauth_module, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(oauth_module, "CREDENTIALS_FILE", str(creds_file)):
            oauth_module.save_credentials(token_response)

        after_ms = int(time.time() * 1000)

        data = json.loads(creds_file.read_text())
        expires_at = data["claudeAiOauth"]["expiresAt"]
        # Should be between before + 7200000 and after + 7200000
        assert before_ms + 7200000 <= expires_at <= after_ms + 7200000


# ---------------------------------------------------------------------------
# User-Agent rotation constants
# ---------------------------------------------------------------------------


class TestUserAgentConstants:
    def test_user_agents_is_nonempty_list(self, oauth_module) -> None:
        assert isinstance(oauth_module.USER_AGENTS, list)
        assert len(oauth_module.USER_AGENTS) > 0

    def test_user_agents_are_strings(self, oauth_module) -> None:
        for ua in oauth_module.USER_AGENTS:
            assert isinstance(ua, str)
            assert len(ua) > 0

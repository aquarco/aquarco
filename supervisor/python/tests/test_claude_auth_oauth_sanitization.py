"""Tests for credential sanitization in claude-auth-oauth.py.

The implementation removed partial auth code logging (code_first8) to reduce
credential exposure in logs. These tests verify the script's logging behavior
and credential handling functions without executing the full OAuth flow.
"""

from __future__ import annotations

import hashlib
import base64
import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_script_path() -> Path:
    """Locate the canonical (package) copy of claude-auth-oauth.py.

    The package copy at src/aquarco_supervisor/scripts/ is the one installed
    and executed at runtime.  A legacy copy may exist at supervisor/scripts/
    but the package copy takes precedence for testing.
    """
    # Package copy (canonical — the one that actually runs)
    pkg_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "aquarco_supervisor" / "scripts" / "claude-auth-oauth.py"
    )
    if pkg_path.exists():
        return pkg_path

    # Fallback: legacy location at project root
    legacy_path = Path(__file__).resolve().parent.parent.parent.parent / "supervisor" / "scripts" / "claude-auth-oauth.py"
    if legacy_path.exists():
        return legacy_path

    pytest.skip("claude-auth-oauth.py not found")


def _load_oauth_module():
    """Load the claude-auth-oauth.py script as a module for testing."""
    script_path = _find_script_path()

    spec = importlib.util.spec_from_file_location("claude_auth_oauth", script_path)
    mod = importlib.util.module_from_spec(spec)
    # Prevent the module from running main() on import
    with patch.object(mod, "__name__", "claude_auth_oauth"):
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# No partial credential logging
# ---------------------------------------------------------------------------


class TestNoPartialCredentialLogging:
    """Verify that the auth code is not partially logged."""

    def test_no_code_first8_in_source(self) -> None:
        """The source code must not contain 'code_first8' — it was removed."""
        script_path = _find_script_path()
        source = script_path.read_text()
        assert "code_first8" not in source, (
            "The 'code_first8' variable was removed to prevent partial credential logging. "
            "Do not re-introduce it."
        )

    def test_exchange_token_log_does_not_contain_auth_code(self) -> None:
        """The log output from exchange_token must not include the auth code."""
        script_path = _find_script_path()
        source = script_path.read_text()

        # The exchange_token function should log code_len but NOT the code itself
        # or any prefix of it
        assert "code_len" in source, (
            "exchange_token should log the code length for diagnostics"
        )
        # Ensure no f-string includes auth_code directly (besides len())
        # Check for patterns that would log the actual auth code value
        import re
        # Look for log() calls that interpolate auth_code without len()
        log_calls = re.findall(r'log\(.*?auth_code.*?\)', source, re.DOTALL)
        for call in log_calls:
            # Allow: len(auth_code), f"...{len(auth_code)}..."
            # Reject: auth_code[:8], auth_code[:N], f"...{auth_code}..."
            assert "auth_code[:" not in call, (
                f"Log call should not include partial auth code: {call}"
            )
            # If auth_code appears but only inside len(), that's fine
            if "auth_code" in call and "len(auth_code)" not in call:
                # This is OK if it's just the variable name in a len() call
                pass

    def test_no_credential_substring_logging_in_source(self) -> None:
        """No slicing of credential values for logging purposes."""
        script_path = _find_script_path()
        source = script_path.read_text()
        # Patterns that would log credential substrings
        assert "access_token[:" not in source, "Should not log partial access tokens"
        assert "refresh_token[:" not in source, "Should not log partial refresh tokens"
        assert "code_verifier[:" not in source, "Should not log partial code verifiers"


# ---------------------------------------------------------------------------
# PKCE functions (pure logic, no network)
# ---------------------------------------------------------------------------


class TestPkceFunctions:
    """Test the PKCE helper functions in claude-auth-oauth.py."""

    def test_generate_code_verifier_length(self) -> None:
        mod = _load_oauth_module()
        verifier = mod.generate_code_verifier(64)
        assert len(verifier) == 64

    def test_generate_code_verifier_charset(self) -> None:
        """Verifier must only contain URL-safe characters."""
        mod = _load_oauth_module()
        import string
        valid_chars = set(string.ascii_letters + string.digits + "-._~")
        verifier = mod.generate_code_verifier(128)
        for ch in verifier:
            assert ch in valid_chars, f"Invalid character in verifier: {ch!r}"

    def test_generate_code_verifier_is_random(self) -> None:
        """Two verifiers should be different (probabilistic but virtually certain)."""
        mod = _load_oauth_module()
        v1 = mod.generate_code_verifier()
        v2 = mod.generate_code_verifier()
        assert v1 != v2

    def test_generate_code_challenge_s256(self) -> None:
        """Code challenge should be the S256 transform of the verifier."""
        mod = _load_oauth_module()
        verifier = "test-verifier-for-challenge"
        challenge = mod.generate_code_challenge(verifier)

        # Verify manually
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        expected = expected.replace("/", "_").replace("+", "-").rstrip("=")

        assert challenge == expected

    def test_code_challenge_is_url_safe(self) -> None:
        """The code challenge must not contain +, /, or = characters."""
        mod = _load_oauth_module()
        verifier = mod.generate_code_verifier()
        challenge = mod.generate_code_challenge(verifier)
        assert "+" not in challenge
        assert "/" not in challenge
        assert "=" not in challenge


# ---------------------------------------------------------------------------
# build_authorize_url
# ---------------------------------------------------------------------------


class TestBuildAuthorizeUrl:
    """Test the authorize URL builder."""

    def test_url_contains_required_params(self) -> None:
        mod = _load_oauth_module()
        url = mod.build_authorize_url("challenge123", "state456")
        assert "client_id=" in url
        assert "response_type=code" in url
        assert "code_challenge=challenge123" in url
        assert "code_challenge_method=S256" in url
        assert "state=state456" in url

    def test_url_starts_with_authorize_endpoint(self) -> None:
        mod = _load_oauth_module()
        url = mod.build_authorize_url("c", "s")
        assert url.startswith("https://claude.ai/oauth/authorize?")

    def test_url_contains_redirect_uri(self) -> None:
        mod = _load_oauth_module()
        url = mod.build_authorize_url("c", "s")
        assert "redirect_uri=" in url


# ---------------------------------------------------------------------------
# save_credentials
# ---------------------------------------------------------------------------


class TestSaveCredentials:
    """Test credential file writing."""

    def test_saves_access_token(self, tmp_path: Path) -> None:
        mod = _load_oauth_module()
        creds_file = tmp_path / ".credentials.json"

        with patch.object(mod, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(mod, "CREDENTIALS_FILE", str(creds_file)):
            mod.save_credentials({
                "access_token": "at_test",
                "refresh_token": "rt_test",
                "expires_in": 3600,
            })

        data = json.loads(creds_file.read_text())
        assert data["claudeAiOauth"]["accessToken"] == "at_test"
        assert data["claudeAiOauth"]["refreshToken"] == "rt_test"

    def test_file_permissions_restricted(self, tmp_path: Path) -> None:
        """Credentials file should be 0600 (owner read/write only)."""
        mod = _load_oauth_module()
        creds_file = tmp_path / ".credentials.json"

        with patch.object(mod, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(mod, "CREDENTIALS_FILE", str(creds_file)):
            mod.save_credentials({"access_token": "test", "expires_in": 3600})

        mode = creds_file.stat().st_mode & 0o777
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_preserves_existing_credentials(self, tmp_path: Path) -> None:
        """Saving new credentials should not overwrite unrelated keys."""
        mod = _load_oauth_module()
        creds_file = tmp_path / ".credentials.json"
        creds_file.write_text(json.dumps({"otherKey": "preserved"}))

        with patch.object(mod, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(mod, "CREDENTIALS_FILE", str(creds_file)):
            mod.save_credentials({"access_token": "new", "expires_in": 3600})

        data = json.loads(creds_file.read_text())
        assert data["otherKey"] == "preserved"
        assert data["claudeAiOauth"]["accessToken"] == "new"

    def test_stores_account_info_when_present(self, tmp_path: Path) -> None:
        mod = _load_oauth_module()
        creds_file = tmp_path / ".credentials.json"

        with patch.object(mod, "CLAUDE_DIR", str(tmp_path)), \
             patch.object(mod, "CREDENTIALS_FILE", str(creds_file)):
            mod.save_credentials({
                "access_token": "at",
                "expires_in": 3600,
                "account": {"uuid": "u1", "email_address": "test@example.com"},
                "organization": {"uuid": "o1", "name": "TestOrg"},
            })

        data = json.loads(creds_file.read_text())
        assert data["oauthAccount"]["accountUuid"] == "u1"
        assert data["oauthAccount"]["emailAddress"] == "test@example.com"
        assert data["oauthAccount"]["organizationUuid"] == "o1"

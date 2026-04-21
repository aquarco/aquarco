"""Tests for pollers/auth_utils.py — shared GitHub auth error detection.

Validates the keyword list used to decide whether a `gh` CLI error is an
authentication failure.  The implementation was extracted from github_source.py
and github_tasks.py to eliminate duplication (DRY fix) and the overly-broad
"token" keyword was removed to prevent false-positive auth-failure pauses.
"""

from __future__ import annotations

import pytest

from aquarco_supervisor.pollers.auth_utils import (
    _AUTH_ERROR_KEYWORDS,
    is_github_auth_error,
)


# ---------------------------------------------------------------------------
# Keyword list structure
# ---------------------------------------------------------------------------


class TestAuthErrorKeywords:
    """Verify the keyword list itself — a regression guard."""

    def test_keywords_is_tuple(self) -> None:
        assert isinstance(_AUTH_ERROR_KEYWORDS, tuple)

    def test_token_not_in_keywords(self) -> None:
        """'token' was removed because it false-positives on unrelated errors."""
        assert "token" not in _AUTH_ERROR_KEYWORDS

    def test_expected_keywords_present(self) -> None:
        expected = {"401", "403", "authentication", "unauthorized", "bad credentials", "not logged in"}
        assert expected == set(_AUTH_ERROR_KEYWORDS)


# ---------------------------------------------------------------------------
# is_github_auth_error — True cases (genuine auth failures)
# ---------------------------------------------------------------------------


class TestIsGithubAuthErrorTrueCases:
    """Each keyword must trigger a True result when present in stderr."""

    def test_401_status_code(self) -> None:
        assert is_github_auth_error("HTTP 401: Unauthorized") is True

    def test_403_status_code(self) -> None:
        assert is_github_auth_error("HTTP 403: Forbidden") is True

    def test_authentication_word(self) -> None:
        assert is_github_auth_error("authentication failed for repo") is True

    def test_unauthorized_word(self) -> None:
        assert is_github_auth_error("You are unauthorized to access this resource") is True

    def test_bad_credentials(self) -> None:
        assert is_github_auth_error("Bad credentials: check your token") is True

    def test_not_logged_in(self) -> None:
        assert is_github_auth_error("You are not logged in to any GitHub hosts") is True


# ---------------------------------------------------------------------------
# is_github_auth_error — case-insensitive matching
# ---------------------------------------------------------------------------


class TestIsGithubAuthErrorCaseInsensitive:
    """Keywords must match regardless of case."""

    def test_upper_case(self) -> None:
        assert is_github_auth_error("UNAUTHORIZED ACCESS") is True

    def test_mixed_case_bad_credentials(self) -> None:
        assert is_github_auth_error("Bad Credentials") is True

    def test_upper_case_not_logged_in(self) -> None:
        assert is_github_auth_error("NOT LOGGED IN") is True

    def test_title_case_authentication(self) -> None:
        assert is_github_auth_error("Authentication required") is True


# ---------------------------------------------------------------------------
# is_github_auth_error — False cases (should NOT be flagged as auth errors)
# ---------------------------------------------------------------------------


class TestIsGithubAuthErrorFalseCases:
    """Strings that do NOT indicate auth failures must return False."""

    def test_empty_string(self) -> None:
        assert is_github_auth_error("") is False

    def test_generic_error(self) -> None:
        assert is_github_auth_error("connection refused to api.github.com") is False

    def test_token_keyword_no_longer_matches(self) -> None:
        """The bare 'token' keyword was removed to prevent false-positives."""
        assert is_github_auth_error("invalid JSON token in response body") is False

    def test_rate_limit_for_token(self) -> None:
        """Rate-limit errors mentioning 'token' must not trigger auth-broken."""
        assert is_github_auth_error("rate limit for token exceeded, retry after 60s") is False

    def test_token_usage_exceeded(self) -> None:
        assert is_github_auth_error("token usage exceeded for this billing cycle") is False

    def test_timeout_error(self) -> None:
        assert is_github_auth_error("timed out waiting for response from github.com") is False

    def test_network_error(self) -> None:
        assert is_github_auth_error("DNS resolution failed for github.com") is False

    def test_invalid_json(self) -> None:
        assert is_github_auth_error("json: cannot unmarshal string into Go value") is False

    def test_server_error_500(self) -> None:
        assert is_github_auth_error("HTTP 500: Internal Server Error") is False

    def test_api_not_found_404(self) -> None:
        assert is_github_auth_error("HTTP 404: Not Found") is False


# ---------------------------------------------------------------------------
# is_github_auth_error — edge cases
# ---------------------------------------------------------------------------


class TestIsGithubAuthErrorEdgeCases:
    """Boundary and edge-case inputs."""

    def test_keyword_embedded_in_longer_word(self) -> None:
        """'unauthorized' is a substring of 'unauthorizedxyz' — still matches (contains)."""
        assert is_github_auth_error("unauthorizedxyz") is True

    def test_multiline_stderr(self) -> None:
        """Keywords found on any line of multi-line stderr."""
        stderr = "Error: unexpected end of JSON input\nHTTP 401: Unauthorized\n"
        assert is_github_auth_error(stderr) is True

    def test_whitespace_only(self) -> None:
        assert is_github_auth_error("   \n\t  ") is False

    def test_keyword_at_end_of_text(self) -> None:
        assert is_github_auth_error("request failed: unauthorized") is True

    def test_keyword_at_start_of_text(self) -> None:
        assert is_github_auth_error("401 error occurred") is True

    def test_multiple_keywords_present(self) -> None:
        """When multiple auth keywords appear, still returns True."""
        assert is_github_auth_error("401 unauthorized bad credentials") is True


# ---------------------------------------------------------------------------
# is_github_auth_error — realistic gh CLI error messages
# ---------------------------------------------------------------------------


class TestIsGithubAuthErrorRealisticMessages:
    """Test against realistic error messages from the `gh` CLI."""

    def test_gh_auth_expired_token(self) -> None:
        msg = "error connecting to api.github.com: authentication failed: bad credentials"
        assert is_github_auth_error(msg) is True

    def test_gh_not_logged_in(self) -> None:
        msg = "You are not logged in to any GitHub hosts. Run gh auth login to authenticate."
        assert is_github_auth_error(msg) is True

    def test_gh_rate_limited_no_auth_keywords_except_403(self) -> None:
        """Rate limit 403 currently matches — documenting the known behavior."""
        msg = "HTTP 403: API rate limit exceeded for user. See https://docs.github.com/rate-limits"
        # This matches because "403" is in _AUTH_ERROR_KEYWORDS.
        # The review flagged this as a concern — this test documents current behavior.
        assert is_github_auth_error(msg) is True

    def test_gh_network_timeout(self) -> None:
        msg = "error connecting to api.github.com: dial tcp: lookup api.github.com: no such host"
        assert is_github_auth_error(msg) is False

    def test_gh_repo_not_found(self) -> None:
        msg = "GraphQL: Could not resolve to a Repository with the name 'owner/repo'."
        assert is_github_auth_error(msg) is False

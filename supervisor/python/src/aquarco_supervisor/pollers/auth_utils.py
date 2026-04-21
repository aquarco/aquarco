"""Shared authentication helpers for GitHub pollers."""

from __future__ import annotations


# Keywords that reliably indicate a GitHub CLI authentication failure.
# Deliberately excludes the bare word "token" which is too broad — it matches
# unrelated errors like "invalid JSON token" or "rate limit for token".
_AUTH_ERROR_KEYWORDS = (
    "401",
    "403",
    "authentication",
    "unauthorized",
    "bad credentials",
    "not logged in",
)


def is_github_auth_error(err_text: str) -> bool:
    """Return True if the gh CLI stderr indicates an authentication failure."""
    lower = err_text.lower()
    return any(kw in lower for kw in _AUTH_ERROR_KEYWORDS)

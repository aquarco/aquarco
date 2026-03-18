"""Tests for external triggers poller."""

from __future__ import annotations

from aifishtank_supervisor.pollers.external_triggers import (
    VALID_CATEGORIES,
)


def test_valid_categories() -> None:
    assert "review" in VALID_CATEGORIES
    assert "implementation" in VALID_CATEGORIES
    assert "test" in VALID_CATEGORIES
    assert "design" in VALID_CATEGORIES
    assert "docs" in VALID_CATEGORIES
    assert "analyze" in VALID_CATEGORIES
    assert "invalid" not in VALID_CATEGORIES

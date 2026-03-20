"""Tests for logging setup."""

from __future__ import annotations

import logging
from pathlib import Path

from aquarco_supervisor.logging import get_logger, setup_logging


def test_setup_logging_stderr() -> None:
    setup_logging(level="debug")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) >= 1


def test_setup_logging_with_file(tmp_path: Path) -> None:
    log_file = str(tmp_path / "test.log")
    setup_logging(level="info", log_file=log_file)
    root = logging.getLogger()
    assert any(
        isinstance(h, logging.FileHandler)
        for h in root.handlers
    )


def test_get_logger() -> None:
    log = get_logger("test-component")
    assert log is not None

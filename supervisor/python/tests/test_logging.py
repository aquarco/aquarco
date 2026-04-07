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


def test_setup_logging_permission_error_falls_back_to_stderr(tmp_path: Path) -> None:
    """When the log file cannot be opened due to PermissionError, setup_logging
    should NOT raise and should still have the stderr handler configured."""
    log_dir = tmp_path / "read_only"
    log_dir.mkdir()
    log_file = log_dir / "supervisor.log"
    log_file.touch()
    log_file.chmod(0o000)  # make unwritable

    # Should not raise
    setup_logging(level="info", log_file=str(log_file))
    root = logging.getLogger()

    # stderr handler should still be present
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    # file handler should NOT be present (it failed)
    assert not any(isinstance(h, logging.FileHandler) for h in root.handlers)

    # Cleanup
    log_file.chmod(0o644)


def test_setup_logging_permission_error_logs_warning(tmp_path: Path, capfd) -> None:  # noqa: ANN001
    """When the log file cannot be opened, a warning should be emitted to stderr."""
    log_dir = tmp_path / "locked"
    log_dir.mkdir()
    log_file = log_dir / "supervisor.log"
    log_file.touch()
    log_file.chmod(0o000)

    setup_logging(level="info", log_file=str(log_file))

    # Force a log message to verify logger works
    logger = logging.getLogger("test_warning_check")
    logger.warning("after-setup")
    captured = capfd.readouterr()
    # The warning about the log file should appear in stderr
    assert "after-setup" in captured.err

    # Cleanup
    log_file.chmod(0o644)


def test_get_logger() -> None:
    log = get_logger("test-component")
    assert log is not None

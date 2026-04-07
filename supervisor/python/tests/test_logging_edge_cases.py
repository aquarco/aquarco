"""Edge-case tests for logging setup — complements test_logging.py.

Covers:
- Log level variations (uppercase, mixed case, invalid)
- File handler with nested directory creation
- get_logger returns distinct loggers per component name
- setup_logging idempotency (can be called multiple times)
"""

from __future__ import annotations

import logging
from pathlib import Path

import structlog

from aquarco_supervisor.logging import get_logger, setup_logging


def test_setup_logging_level_warning() -> None:
    """setup_logging with level='warning' should set root to WARNING."""
    setup_logging(level="warning")
    root = logging.getLogger()
    assert root.level == logging.WARNING


def test_setup_logging_level_uppercase() -> None:
    """Level string should be case-insensitive."""
    setup_logging(level="DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG


def test_setup_logging_level_mixed_case() -> None:
    """Mixed case level strings should work."""
    setup_logging(level="Info")
    root = logging.getLogger()
    assert root.level == logging.INFO


def test_setup_logging_invalid_level_defaults_to_info() -> None:
    """An invalid level string should fall back to INFO."""
    setup_logging(level="nonexistent")
    root = logging.getLogger()
    assert root.level == logging.INFO


def test_setup_logging_creates_nested_directories(tmp_path: Path) -> None:
    """setup_logging should create parent directories for the log file."""
    log_file = tmp_path / "deep" / "nested" / "dir" / "app.log"
    assert not log_file.parent.exists()
    setup_logging(level="info", log_file=str(log_file))
    assert log_file.parent.exists()
    root = logging.getLogger()
    assert any(isinstance(h, logging.FileHandler) for h in root.handlers)


def test_setup_logging_clears_previous_handlers() -> None:
    """Calling setup_logging multiple times should not stack handlers."""
    setup_logging(level="info")
    count_after_first = len(logging.getLogger().handlers)
    setup_logging(level="debug")
    count_after_second = len(logging.getLogger().handlers)
    # handlers.clear() is called each time, so count should be consistent
    assert count_after_second <= count_after_first + 1


def test_setup_logging_file_handler_writes(tmp_path: Path) -> None:
    """Log messages should appear in the log file."""
    log_file = tmp_path / "write_test.log"
    setup_logging(level="info", log_file=str(log_file))
    logger = logging.getLogger("write-test")
    logger.info("hello from test")
    # Flush handlers
    for h in logging.getLogger().handlers:
        h.flush()
    content = log_file.read_text()
    assert "hello from test" in content


def test_get_logger_returns_structlog_logger() -> None:
    """get_logger should return a structlog logger (BoundLoggerLazyProxy)."""
    log = get_logger("my-component")
    # structlog.get_logger() returns a BoundLoggerLazyProxy, not BoundLogger directly
    assert hasattr(log, "info")
    assert hasattr(log, "warning")
    assert hasattr(log, "error")
    assert hasattr(log, "debug")


def test_get_logger_distinct_per_name() -> None:
    """Different component names should yield distinct logger instances."""
    log_a = get_logger("component-a")
    log_b = get_logger("component-b")
    # They should be separate bound loggers (not the same object)
    assert log_a is not log_b


def test_setup_logging_no_file_handler_when_no_log_file() -> None:
    """When log_file is None, only the stderr handler should be present."""
    setup_logging(level="info", log_file=None)
    root = logging.getLogger()
    assert not any(isinstance(h, logging.FileHandler) for h in root.handlers)
    assert any(type(h) is logging.StreamHandler for h in root.handlers)

"""Structured logging configuration using structlog."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog


def setup_logging(level: str = "info", log_file: str | None = None) -> None:
    """Configure structlog with JSON output and optional file logging."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", key="ts"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    # stderr handler (always)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(stderr_handler)
    root_logger.setLevel(log_level)

    # file handler (optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_handler = logging.FileHandler(str(log_path))
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except PermissionError as exc:
            root_logger.warning("Could not open log file %s: %s — logging to stderr only", log_path, exc)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a bound logger with the given component name."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(component=name)
    return logger

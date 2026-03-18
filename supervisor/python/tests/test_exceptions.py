"""Tests for exception hierarchy."""

from __future__ import annotations

from aifishtank_supervisor.exceptions import (
    AgentExecutionError,
    AgentTimeoutError,
    CloneError,
    ConfigError,
    ConfigFileNotFoundError,
    ConfigValidationError,
    ConnectionPoolError,
    DatabaseError,
    NoAvailableAgentError,
    PipelineError,
    StageError,
    SupervisorError,
    TaskError,
    TaskNotFoundError,
    WorkerError,
)


def test_hierarchy() -> None:
    """Verify exception inheritance chain."""
    assert issubclass(ConfigError, SupervisorError)
    assert issubclass(ConfigFileNotFoundError, ConfigError)
    assert issubclass(ConfigValidationError, ConfigError)

    assert issubclass(DatabaseError, SupervisorError)
    assert issubclass(ConnectionPoolError, DatabaseError)

    assert issubclass(TaskError, SupervisorError)
    assert issubclass(TaskNotFoundError, TaskError)

    assert issubclass(PipelineError, SupervisorError)
    assert issubclass(StageError, PipelineError)
    assert issubclass(AgentExecutionError, PipelineError)
    assert issubclass(AgentTimeoutError, AgentExecutionError)
    assert issubclass(NoAvailableAgentError, SupervisorError)

    assert issubclass(WorkerError, SupervisorError)
    assert issubclass(CloneError, WorkerError)


def test_exception_messages() -> None:
    e = ConfigValidationError("bad value")
    assert str(e) == "bad value"
    assert isinstance(e, SupervisorError)

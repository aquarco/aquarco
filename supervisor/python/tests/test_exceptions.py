"""Tests for exception hierarchy."""

from __future__ import annotations

from aquarco_supervisor.exceptions import (
    AgentExecutionError,
    AgentTimeoutError,
    CloneError,
    ConfigError,
    ConfigFileNotFoundError,
    ConfigValidationError,
    ConnectionPoolError,
    DatabaseError,
    NoAvailableAgentError,
    OverloadedError,
    PipelineError,
    RateLimitError,
    RetryableError,
    ServerError,
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


# --- Retryable error hierarchy (AC-1 through AC-5) ---


def test_retryable_error_is_agent_execution_error() -> None:
    """AC-1: RetryableError is a subclass of AgentExecutionError."""
    assert issubclass(RetryableError, AgentExecutionError)


def test_rate_limit_error_is_retryable() -> None:
    """AC-2: RateLimitError inherits from RetryableError (backward-compatible)."""
    assert issubclass(RateLimitError, RetryableError)


def test_server_error_is_retryable() -> None:
    """AC-3: ServerError inherits from RetryableError."""
    assert issubclass(ServerError, RetryableError)


def test_overloaded_error_is_retryable() -> None:
    """AC-4: OverloadedError inherits from RetryableError."""
    assert issubclass(OverloadedError, RetryableError)


def test_rate_limit_error_is_agent_execution_error() -> None:
    """AC-5: RateLimitError instance is also an AgentExecutionError (no regression)."""
    e = RateLimitError("rate limit hit")
    assert isinstance(e, AgentExecutionError)
    assert isinstance(e, RetryableError)
    assert str(e) == "rate limit hit"


def test_server_error_message() -> None:
    e = ServerError("api error 500")
    assert isinstance(e, AgentExecutionError)
    assert isinstance(e, RetryableError)
    assert str(e) == "api error 500"


def test_overloaded_error_message() -> None:
    e = OverloadedError("overloaded 529")
    assert isinstance(e, AgentExecutionError)
    assert isinstance(e, RetryableError)
    assert str(e) == "overloaded 529"


def test_retryable_subclasses_can_be_caught_as_agent_execution_error() -> None:
    """All retryable errors must be catchable by existing AgentExecutionError handlers."""
    for exc_cls in (RateLimitError, ServerError, OverloadedError):
        e = exc_cls("test")
        assert isinstance(e, AgentExecutionError), f"{exc_cls.__name__} must be AgentExecutionError"
        assert isinstance(e, PipelineError), f"{exc_cls.__name__} must be PipelineError"
        assert isinstance(e, SupervisorError), f"{exc_cls.__name__} must be SupervisorError"

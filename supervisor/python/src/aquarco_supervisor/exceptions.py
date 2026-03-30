"""Typed exception hierarchy for the supervisor."""


class SupervisorError(Exception):
    """Base exception for all supervisor errors."""


# --- Configuration ---


class ConfigError(SupervisorError):
    """Configuration loading or validation failed."""


class ConfigFileNotFoundError(ConfigError):
    """Configuration file does not exist."""


class ConfigValidationError(ConfigError):
    """Configuration values are invalid."""


# --- Database ---


class DatabaseError(SupervisorError):
    """Database operation failed."""


class ConnectionPoolError(DatabaseError):
    """Failed to establish or manage connection pool."""


class QueryError(DatabaseError):
    """A database query failed."""


# --- Task Queue ---


class TaskError(SupervisorError):
    """Task lifecycle error."""


class TaskNotFoundError(TaskError):
    """Referenced task does not exist."""


class TaskStateError(TaskError):
    """Invalid task state transition."""


# --- Pipeline ---


class PipelineError(SupervisorError):
    """Pipeline execution error."""


class StageError(PipelineError):
    """A pipeline stage failed."""


class AgentExecutionError(PipelineError):
    """Claude CLI agent invocation failed."""


class AgentTimeoutError(AgentExecutionError):
    """Agent exceeded its time limit."""


class AgentInactivityError(AgentExecutionError):
    """Claude process killed due to inactivity after emitting StructuredOutput."""


class RetryableError(AgentExecutionError):
    """Base class for transient Claude API errors that should be retried.

    Subclasses indicate that the task should be postponed and retried after a
    cooldown period, rather than being permanently failed.
    """


class RateLimitError(RetryableError):
    """Claude API rate limit (429) hit — task should be postponed."""


class ServerError(RetryableError):
    """Claude API internal server error (500) — safe to retry with backoff."""


class OverloadedError(RetryableError):
    """Claude API platform overload (529) — temporary, retry with short backoff."""


def _cooldown_for_error(e: RetryableError) -> tuple[int, int]:
    """Return ``(cooldown_minutes, max_retries)`` for a :class:`RetryableError` subtype.

    Centralised here so that both the pipeline executor and the main-loop defensive
    handler can import it without creating a cross-module coupling to executor internals.

    .. note::
        The leading underscore signals this is *package-internal*: it is intentionally
        imported by ``pipeline.executor`` and ``main`` within this package, but should
        not be considered part of the public API.

    Dispatch table:

    * :class:`OverloadedError` (529): 15 min cooldown, 24 max retries
    * :class:`ServerError` (500):     30 min cooldown, 12 max retries
    * :class:`RateLimitError` (429) / other: 60 min cooldown, 24 max retries
    """
    if isinstance(e, OverloadedError):
        return (15, 24)
    if isinstance(e, ServerError):
        return (30, 12)
    return (60, 24)


# --- Agent Registry ---


class AgentRegistryError(SupervisorError):
    """Agent registry loading or lookup failed."""


class NoAvailableAgentError(AgentRegistryError):
    """No agent with capacity is available for the requested category."""


# --- Pollers ---


class PollerError(SupervisorError):
    """A poller encountered an error."""


class GitHubAPIError(PollerError):
    """GitHub CLI or API call failed."""


# --- Workers ---


class WorkerError(SupervisorError):
    """Git worker (clone/pull) operation failed."""


class CloneError(WorkerError):
    """Repository clone failed."""


class PullError(WorkerError):
    """Repository pull failed."""

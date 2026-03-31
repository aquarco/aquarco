"""Synchronous GraphQL client using httpx."""

from __future__ import annotations

from typing import Any

import httpx

from aquarco_cli.config import get_config


class GraphQLError(Exception):
    """Raised when the GraphQL response contains errors."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        messages = "; ".join(e.get("message", str(e)) for e in errors)
        super().__init__(messages)


class GraphQLClient:
    """Thin synchronous wrapper around the Aquarco GraphQL API."""

    def __init__(self, url: str | None = None, timeout: float | None = None) -> None:
        _cfg = get_config()
        self.url = url or _cfg.api_url
        self.timeout = timeout or _cfg.http_timeout

    def execute(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Execute a GraphQL operation and return the ``data`` dict.

        Raises:
            GraphQLError: if the response contains GraphQL-level errors.
            httpx.HTTPStatusError: if the HTTP status is not 2xx.
            httpx.ConnectError: if the API is unreachable.
        """
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        resp = httpx.post(
            self.url,
            json=payload,
            timeout=self.timeout,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()

        body = resp.json()
        if "errors" in body and body["errors"]:
            raise GraphQLError(body["errors"])
        return body.get("data", {})


# ---------------------------------------------------------------------------
# Query / Mutation constants
# ---------------------------------------------------------------------------

# -- Dashboard & tasks ------------------------------------------------------

QUERY_DASHBOARD_STATS = """
query {
  dashboardStats {
    totalTasks
    pendingTasks
    executingTasks
    completedTasks
    failedTasks
    blockedTasks
    activeAgents
    totalTokensToday
    totalCostToday
    tasksByPipeline { pipeline count }
    tasksByRepository { repository count }
  }
}
"""

QUERY_TASKS = """
query Tasks($status: TaskStatus, $repository: String, $limit: Int, $offset: Int) {
  tasks(status: $status, repository: $repository, limit: $limit, offset: $offset) {
    nodes {
      id title status priority source pipeline repository { name }
      createdAt startedAt completedAt errorMessage retryCount
      branchName prNumber totalCostUsd
    }
    totalCount
  }
}
"""

QUERY_TASK = """
query Task($id: ID!) {
  task(id: $id) {
    id title status priority source sourceRef pipeline
    repository { name }
    createdAt updatedAt startedAt completedAt
    retryCount errorMessage branchName prNumber totalCostUsd
    stages {
      id stageNumber iteration run category agent status
      startedAt completedAt tokensInput tokensOutput costUsd errorMessage
    }
  }
}
"""

QUERY_PIPELINE_STATUS = """
query PipelineStatus($taskId: ID!) {
  pipelineStatus(taskId: $taskId) {
    taskId pipeline lastCompletedStageId totalStages status
    stages {
      id stageNumber category agent status startedAt completedAt
    }
  }
}
"""

# -- Repositories -----------------------------------------------------------

QUERY_REPOSITORIES = """
query {
  repositories {
    name url branch cloneStatus pollers taskCount
    lastClonedAt lastPulledAt headSha errorMessage
  }
}
"""

MUTATION_REGISTER_REPOSITORY = """
mutation RegisterRepository($input: RegisterRepositoryInput!) {
  registerRepository(input: $input) {
    repository { name url branch cloneStatus pollers }
    errors { field message }
  }
}
"""

MUTATION_REMOVE_REPOSITORY = """
mutation RemoveRepository($name: String!) {
  removeRepository(name: $name) {
    repository { name }
    errors { field message }
  }
}
"""

# -- Tasks ------------------------------------------------------------------

MUTATION_CREATE_TASK = """
mutation CreateTask($input: CreateTaskInput!) {
  createTask(input: $input) {
    task { id title status pipeline repository { name } }
    errors { field message }
  }
}
"""

# -- Auth -------------------------------------------------------------------

QUERY_CLAUDE_AUTH_STATUS = """
query { claudeAuthStatus { authenticated email } }
"""

QUERY_GITHUB_AUTH_STATUS = """
query { githubAuthStatus { authenticated username } }
"""

MUTATION_CLAUDE_LOGIN_START = """
mutation { claudeLoginStart { authorizeUrl expiresIn } }
"""

MUTATION_CLAUDE_SUBMIT_CODE = """
mutation ClaudeSubmitCode($code: String!) {
  claudeSubmitCode(code: $code) { success email error }
}
"""

MUTATION_CLAUDE_LOGIN_POLL = """
mutation { claudeLoginPoll { success email error } }
"""

MUTATION_GITHUB_LOGIN_START = """
mutation { githubLoginStart { userCode verificationUri expiresIn } }
"""

MUTATION_GITHUB_LOGIN_POLL = """
mutation { githubLoginPoll { success username error } }
"""

# -- Shared constants --------------------------------------------------------

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMEOUT", "CLOSED"}

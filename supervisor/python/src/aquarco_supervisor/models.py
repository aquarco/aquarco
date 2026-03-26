"""Pydantic models for tasks, agents, stages, and configuration."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

# --- Enums ---


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    CLOSED = "closed"


class StageStatus(str, enum.Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    RATE_LIMITED = "rate_limited"


class CloneStatus(str, enum.Enum):
    PENDING = "pending"
    CLONING = "cloning"
    READY = "ready"
    ERROR = "error"


class TaskPhase(str, enum.Enum):
    TRIGGER = "trigger"
    PLANNING = "planning"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ValidationItemStatus(str, enum.Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"


class RepoAgentScanStatus(str, enum.Enum):
    PENDING = "pending"
    SCANNING = "scanning"
    ANALYZING = "analyzing"
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"


class Complexity(str, enum.Enum):
    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EPIC = "epic"

    @property
    def _order(self) -> int:
        return list(Complexity).index(self)

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Complexity):
            return NotImplemented
        return self._order >= other._order

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Complexity):
            return NotImplemented
        return self._order > other._order

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Complexity):
            return NotImplemented
        return self._order <= other._order

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Complexity):
            return NotImplemented
        return self._order < other._order


# --- Database Models ---


class Task(BaseModel):
    id: str
    title: str
    status: TaskStatus = TaskStatus.PENDING
    phase: TaskPhase = TaskPhase.TRIGGER
    priority: int = 50
    source: str | None = ""
    source_ref: str | None = ""
    pipeline: str = "feature-pipeline"
    repository: str | None = ""
    initial_context: dict[str, Any] | None = Field(default_factory=dict)
    planned_stages: list[dict[str, Any]] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    assigned_agent: str | None = None
    current_stage: int = 0
    retry_count: int = 0
    rate_limit_count: int = 0
    error_message: str | None = None
    parent_task_id: str | None = None
    pr_number: int | None = None
    branch_name: str | None = None


class Stage(BaseModel):
    task_id: str
    stage_number: int
    category: str
    agent: str | None = None
    status: StageStatus = StageStatus.PENDING
    stage_key: str | None = None
    iteration: int = 1
    input: dict[str, Any] | None = None
    structured_output: dict[str, Any] | None = None
    validation_items_in: list[dict[str, Any]] | None = None
    validation_items_out: list[dict[str, Any]] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class Repository(BaseModel):
    name: str
    url: str
    branch: str = "main"
    clone_dir: str = ""
    clone_status: CloneStatus = CloneStatus.PENDING
    is_config_repo: bool = False
    head_sha: str | None = None
    last_cloned_at: datetime | None = None
    last_pulled_at: datetime | None = None
    error_message: str | None = None
    deploy_public_key: str | None = None


class MergeStrategy(str, enum.Enum):
    EXTEND = "extend"
    REPLACE = "replace"


class MergeConfig(BaseModel):
    agents: MergeStrategy = MergeStrategy.EXTEND
    pipelines: MergeStrategy = MergeStrategy.EXTEND


class ConfigOverlay(BaseModel):
    api_version: str = Field(default="aquarco.config/v1", alias="apiVersion")
    kind: str = "ConfigOverlay"
    merge: MergeConfig = Field(default_factory=MergeConfig)
    agents: list[dict[str, Any]] = Field(default_factory=list)
    pipelines: list[dict[str, Any]] = Field(default_factory=list)
    prompts_dir: str = Field(default="./prompts", alias="promptsDir")

    model_config = {"populate_by_name": True}


class AgentInstance(BaseModel):
    agent_name: str
    active_count: int = 0
    total_executions: int = 0
    last_execution_at: datetime | None = None


class PollState(BaseModel):
    poller_name: str
    last_poll_at: datetime | None = None
    last_successful_at: datetime | None = None
    cursor: str = ""
    state_data: dict[str, Any] = Field(default_factory=dict)


class PipelineCheckpoint(BaseModel):
    task_id: str
    last_completed_stage: int
    checkpoint_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class ValidationItem(BaseModel):
    id: int | None = None
    task_id: str
    stage_key: str | None = None
    category: str
    description: str
    status: ValidationItemStatus = ValidationItemStatus.OPEN
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime | None = None


class RepoAgentScan(BaseModel):
    id: int | None = None
    repo_name: str
    status: RepoAgentScanStatus = RepoAgentScanStatus.PENDING
    agents_found: int = 0
    agents_created: int = 0
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class PlannedStageAssignment(BaseModel):
    category: str
    agents: list[str]
    parallel: bool = False
    validation: list[str] = Field(default_factory=list)


# --- Configuration Models ---


class DatabaseConfig(BaseModel):
    url: str
    max_connections: int = Field(default=5, alias="maxConnections")

    model_config = {"populate_by_name": True}


class LoggingConfig(BaseModel):
    level: str = "info"
    file: str = "/var/log/aquarco/supervisor.log"
    max_size_mb: int = Field(default=100, alias="maxSizeMB")
    max_files: int = Field(default=5, alias="maxFiles")
    format: str = "json"

    model_config = {"populate_by_name": True}


class GlobalLimits(BaseModel):
    max_concurrent_agents: int = Field(default=3, alias="maxConcurrentAgents")
    max_tokens_per_hour: int = Field(default=1_000_000, alias="maxTokensPerHour")
    cooldown_between_tasks_seconds: int = Field(default=5, alias="cooldownBetweenTasksSeconds")
    max_retries: int = Field(default=3, alias="maxRetries")
    retry_delay_seconds: int = Field(default=60, alias="retryDelaySeconds")

    model_config = {"populate_by_name": True}


class SecretsConfig(BaseModel):
    github_token_file: str = Field(
        default="/home/agent/.ssh/github-token", alias="githubTokenFile"
    )
    anthropic_key_file: str = Field(
        default="/home/agent/.anthropic-key", alias="anthropicKeyFile"
    )

    model_config = {"populate_by_name": True}


class HealthConfig(BaseModel):
    enabled: bool = True
    report_interval_minutes: int = Field(default=30, alias="reportIntervalMinutes")
    report_destination: str = Field(default="github-issue", alias="reportDestination")
    issue_number: int = Field(default=1, alias="issueNumber")

    model_config = {"populate_by_name": True}


class StageConfig(BaseModel):
    name: str = ""
    category: str
    required: bool = True
    conditions: list[dict[str, Any]] = Field(default_factory=list)


class PipelineTrigger(BaseModel):
    labels: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)


class PipelineConfig(BaseModel):
    name: str
    version: str = "0.0.0"
    trigger: PipelineTrigger
    stages: list[StageConfig]
    categories: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PollerSourceConfig(BaseModel):
    type: str
    labels: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)


class GitHubTasksPollerConfig(BaseModel):
    repositories: str = "all"
    sources: list[PollerSourceConfig] = Field(default_factory=list)


class GitHubSourceWatchConfig(BaseModel):
    type: str
    states: list[str] = Field(default_factory=list)


class GitHubSourcePollerConfig(BaseModel):
    repositories: str = "all"
    watch: list[GitHubSourceWatchConfig] = Field(default_factory=list)
    triggers: dict[str, list[str]] = Field(default_factory=dict)


class FileWatchPollerConfig(BaseModel):
    watch_dir: str = Field(alias="watchDir")
    processed_dir: str = Field(alias="processedDir")

    model_config = {"populate_by_name": True}


class PollerDefinition(BaseModel):
    name: str
    type: str
    enabled: bool = True
    interval_seconds: int = Field(default=60, alias="intervalSeconds")
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class RepositoryConfig(BaseModel):
    name: str
    url: str
    branch: str = "main"
    clone_dir: str = Field(alias="cloneDir")
    pollers: list[str] = Field(default_factory=list)
    auth: str = "ssh"
    ports: dict[str, int] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SupervisorSpec(BaseModel):
    workdir: str
    agents_dir: str = Field(alias="agentsDir")
    prompts_dir: str = Field(alias="promptsDir")
    database: DatabaseConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    global_limits: GlobalLimits = Field(default_factory=GlobalLimits, alias="globalLimits")
    config_reload: dict[str, Any] = Field(default_factory=dict, alias="configReload")
    repo_config: dict[str, Any] = Field(default_factory=dict, alias="repoConfig")
    pipelines_file: str = Field(default="", alias="pipelinesFile")
    pollers: list[PollerDefinition] = Field(default_factory=list)
    health: HealthConfig = Field(default_factory=HealthConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)

    model_config = {"populate_by_name": True}


class SupervisorConfig(BaseModel):
    api_version: str = Field(alias="apiVersion")
    metadata: dict[str, Any] = Field(default_factory=dict)
    spec: SupervisorSpec

    model_config = {"populate_by_name": True}

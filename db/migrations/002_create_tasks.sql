-- depends: 001_create_repositories
-- Migration: 002_create_tasks.sql
-- Purpose: Central task queue.
--
-- A task is the unit of work dispatched to an agent pipeline.
-- Tasks are created by pollers (GitHub issues, PRs, external triggers)
-- and progress through a status lifecycle managed by the supervisor.
--
-- Depends on: 001_create_repositories.sql (repositories table)

SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS tasks (
    -- Stable external identifier; typically a UUID or a composite key
    -- like 'github-issue-42'. Set by the poller that creates the task.
    id                TEXT        PRIMARY KEY,

    -- Short human-readable title for the Web UI.
    title             TEXT        NOT NULL,

    -- Broad category that determines which agent handles this task.
    -- Must match one of the categories defined in an agent definition YAML.
    category          TEXT        NOT NULL,

    -- Current lifecycle status of the task.
    status            TEXT        NOT NULL DEFAULT 'pending',

    -- Scheduling priority: 0 = highest, 100 = lowest.
    priority          INTEGER     NOT NULL DEFAULT 50,

    -- Origin of the task.
    source            TEXT        NOT NULL,   -- 'github-issue' | 'github-pr' | 'external'

    -- External reference: issue number, PR number, etc.
    source_ref        TEXT,

    -- Name of the pipeline to execute; matches a pipeline definition file.
    -- Example: 'feature-pipeline', 'bugfix-pipeline'
    pipeline          TEXT,

    -- Target repository. Every task belongs to exactly one repo.
    repository        TEXT        NOT NULL REFERENCES repositories(name),

    -- Arbitrary JSON payload supplied by the poller at task creation time.
    -- Agents read this as the starting context for the first stage.
    initial_context   JSONB,

    -- Lifecycle timestamps.
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,

    -- Agent instance name currently executing this task.
    assigned_agent    TEXT,

    -- Index (0-based) of the currently executing pipeline stage.
    current_stage     INTEGER     NOT NULL DEFAULT 0,

    -- Number of times this task has been retried after failure.
    retry_count       INTEGER     NOT NULL DEFAULT 0,

    -- Last error message; populated on status = 'failed' | 'timeout'.
    error_message     TEXT,

    CONSTRAINT valid_status CHECK (
        status IN ('pending', 'queued', 'executing', 'completed', 'failed', 'timeout', 'blocked')
    ),
    CONSTRAINT valid_category CHECK (
        category IN ('review', 'implementation', 'test', 'design', 'docs', 'analyze')
    ),
    CONSTRAINT valid_priority CHECK (priority BETWEEN 0 AND 100)
);

COMMENT ON TABLE  tasks                  IS 'Central task queue; one row per unit of work dispatched to an agent pipeline.';
COMMENT ON COLUMN tasks.id               IS 'Stable external identifier set by the originating poller.';
COMMENT ON COLUMN tasks.category         IS 'Routes the task to the correct agent definition.';
COMMENT ON COLUMN tasks.status           IS 'Lifecycle: pending → queued → executing → completed | failed | timeout | blocked.';
COMMENT ON COLUMN tasks.priority         IS '0 = highest priority, 100 = lowest.';
COMMENT ON COLUMN tasks.source           IS 'Origin system: github-issue | github-pr | external.';
COMMENT ON COLUMN tasks.pipeline         IS 'Pipeline definition name; determines the sequence of stages.';
COMMENT ON COLUMN tasks.initial_context  IS 'Seed context payload supplied by the poller at task creation.';
COMMENT ON COLUMN tasks.current_stage    IS '0-based index of the stage currently executing.';

-- Indexes for the supervisor's common query patterns.

-- Status-based polling (fetch all pending/queued tasks).
CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks(status);

-- Category-based routing (find tasks for a specific agent category).
CREATE INDEX IF NOT EXISTS idx_tasks_category
    ON tasks(category);

-- Pipeline-based queries (list tasks for a given pipeline).
CREATE INDEX IF NOT EXISTS idx_tasks_pipeline
    ON tasks(pipeline);

-- Foreign key index (joins with repositories).
CREATE INDEX IF NOT EXISTS idx_tasks_repository
    ON tasks(repository);

-- Chronological listing for the Web UI.
CREATE INDEX IF NOT EXISTS idx_tasks_created
    ON tasks(created_at);

-- Partial index: priority scheduling of actionable tasks only.
-- The WHERE clause keeps this index small and selective.
CREATE INDEX IF NOT EXISTS idx_tasks_status_priority
    ON tasks(status, priority)
    WHERE status IN ('pending', 'queued');



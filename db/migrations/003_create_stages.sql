-- Migration: 003_create_stages.sql
-- Purpose: Pipeline stage execution records for each task.
--
-- A pipeline consists of an ordered sequence of stages. Each time an agent
-- executes a stage for a task, a row is written here. The supervisor uses
-- these rows to track progress, retry failed stages, and pass structured
-- output forward to the next stage.
--
-- Observability note: token counts reflect Claude Code Max session usage,
-- not API billing units. They are captured for cost-awareness dashboards.
--
-- Depends on: 002_create_tasks.sql (tasks table)

-- up

SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS stages (
    -- Surrogate PK; stages are always queried via (task_id, stage_number).
    id                  BIGSERIAL       PRIMARY KEY,

    -- Parent task.
    task_id             TEXT            NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,

    -- 0-based position in the pipeline.
    stage_number        INTEGER         NOT NULL,

    -- Agent category that handles this stage (matches agent definition).
    category            TEXT            NOT NULL,

    -- Name of the agent instance that executed (or is executing) this stage.
    agent               TEXT,

    -- Version string from the agent definition YAML at execution time.
    -- Stored here for post-incident debugging and potential rollback analysis.
    agent_version       TEXT,

    -- Execution lifecycle status.
    status              TEXT            NOT NULL DEFAULT 'pending',

    -- Execution timestamps.
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,

    -- Validated structured output; schema is agent-specific.
    -- The pipeline executor validates this against the agent's output schema
    -- before writing it here.
    structured_output   JSONB,

    -- Full raw agent output retained for debugging.
    -- Not used by the pipeline executor; retained for human inspection.
    raw_output          TEXT,

    -- Token usage for observability (Claude Code Max session, not API billing).
    tokens_input        INTEGER,
    tokens_output       INTEGER,

    -- Error detail on failure.
    error_message       TEXT,

    -- Number of execution retries for this specific stage.
    retry_count         INTEGER         NOT NULL DEFAULT 0,

    -- Enforce that (task_id, stage_number) is unique so the supervisor
    -- cannot accidentally create duplicate stage rows.
    UNIQUE (task_id, stage_number),

    CONSTRAINT valid_stage_status CHECK (
        status IN ('pending', 'executing', 'completed', 'failed', 'skipped')
    )
);

COMMENT ON TABLE  stages                    IS 'One row per pipeline stage execution; tracks agent output and observability metrics.';
COMMENT ON COLUMN stages.stage_number       IS '0-based position within the pipeline.';
COMMENT ON COLUMN stages.category           IS 'Agent category responsible for this stage.';
COMMENT ON COLUMN stages.agent_version      IS 'Agent definition version at execution time; aids debugging and rollback analysis.';
COMMENT ON COLUMN stages.structured_output  IS 'Agent output validated against the stage output schema; consumed by subsequent stages.';
COMMENT ON COLUMN stages.raw_output         IS 'Unvalidated full agent output retained for human debugging.';
COMMENT ON COLUMN stages.tokens_input       IS 'Input token count (Claude Code Max session; not API billing).';
COMMENT ON COLUMN stages.tokens_output      IS 'Output token count (Claude Code Max session; not API billing).';

-- Index for fetching all stages of a task in order.
CREATE INDEX IF NOT EXISTS idx_stages_task_id
    ON stages(task_id, stage_number);

-- Index for finding stages by status (e.g., all executing stages for watchdog).
CREATE INDEX IF NOT EXISTS idx_stages_status
    ON stages(status)
    WHERE status IN ('executing', 'failed');

-- down

-- DROP TABLE IF EXISTS aquarco.stages;

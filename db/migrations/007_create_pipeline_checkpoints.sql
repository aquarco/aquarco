-- depends: 006_create_agent_instances
-- Migration: 007_create_pipeline_checkpoints.sql
-- Purpose: Pipeline resume checkpoints for crash recovery.
--
-- When the supervisor or a pipeline executor is restarted after an
-- unexpected shutdown, it reads this table to determine the last
-- successfully completed stage for each in-flight task and resumes
-- from the next stage, avoiding re-execution of completed work.
--
-- One row per task (1:1 with tasks). The row is created when the
-- pipeline starts and updated as each stage completes. It is deleted
-- (via CASCADE) when the parent task is deleted.
--
-- checkpoint_data holds any additional state the executor needs to
-- resume cleanly (environment variables, workspace paths, branch names).
--
-- Depends on: 002_create_tasks.sql (tasks table)

SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
    -- 1:1 with tasks; CASCADE ensures cleanup when a task is removed.
    task_id                 TEXT        PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,

    -- 0-based index of the last stage that completed successfully.
    -- On resume, the executor starts from (last_completed_stage + 1).
    last_completed_stage    INTEGER     NOT NULL,

    -- Arbitrary executor state needed for clean resume:
    -- branch name, workspace directory, environment variables, etc.
    checkpoint_data         JSONB,

    -- When this checkpoint was last written.
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  pipeline_checkpoints                      IS 'Per-task resume checkpoint; enables the executor to restart cleanly after a crash.';
COMMENT ON COLUMN pipeline_checkpoints.last_completed_stage IS '0-based index of the last successfully completed stage.';
COMMENT ON COLUMN pipeline_checkpoints.checkpoint_data      IS 'Executor state needed on resume: branch, workspace, env vars, etc.';



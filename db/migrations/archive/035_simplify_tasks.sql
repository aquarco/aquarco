-- depends: 034_checkpoint_stage_fk
-- Migration: 035_simplify_tasks.sql
-- Purpose: Simplify tasks table — absorb pipeline_checkpoints, merge phase
--          into status, drop assigned_agent & current_stage, add pipeline FK.
--
-- Changes:
--   1. Add last_completed_stage, checkpoint_data, pipeline_version to tasks
--   2. Add 'planning' to valid_status CHECK
--   3. Migrate data from pipeline_checkpoints into tasks
--   4. Backfill pipeline_version from active pipeline_definitions
--   5. Map phase='planning' rows to status='planning'
--   6. Add composite FK (pipeline, pipeline_version) -> pipeline_definitions
--   7. Drop current_stage, phase, assigned_agent columns
--   8. Drop pipeline_checkpoints table
--   9. Update get_task_context() function
--
-- Depends on: 034_checkpoint_stage_fk.sql

SET search_path TO aquarco, public;

-- ---------------------------------------------------------------------------
-- 1. Add new columns to tasks
-- ---------------------------------------------------------------------------

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS last_completed_stage BIGINT
        REFERENCES stages(id) ON DELETE SET NULL;

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS checkpoint_data JSONB NOT NULL DEFAULT '{}';

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS pipeline_version TEXT;

COMMENT ON COLUMN tasks.last_completed_stage IS
    'FK to stages.id — the last stage row that completed successfully. NULL when no stage has completed.';
COMMENT ON COLUMN tasks.checkpoint_data IS
    'Executor resume state: branch name, workspace path, env vars, etc.';
COMMENT ON COLUMN tasks.pipeline_version IS
    'Pipeline definition version (semver). Together with pipeline, forms FK to pipeline_definitions.';

-- ---------------------------------------------------------------------------
-- 2. Expand status CHECK to include 'planning'
-- ---------------------------------------------------------------------------

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE tasks ADD CONSTRAINT valid_status
    CHECK (status IN (
        'pending', 'queued', 'planning', 'executing',
        'completed', 'failed', 'timeout', 'blocked', 'rate_limited', 'closed'
    ));

-- ---------------------------------------------------------------------------
-- 3. Migrate data from pipeline_checkpoints into tasks
-- ---------------------------------------------------------------------------

UPDATE tasks t
SET last_completed_stage = pc.last_completed_stage,
    checkpoint_data      = COALESCE(pc.checkpoint_data, '{}')
FROM pipeline_checkpoints pc
WHERE pc.task_id = t.id;

-- ---------------------------------------------------------------------------
-- 4. Backfill pipeline_version from active pipeline_definitions
-- ---------------------------------------------------------------------------

UPDATE tasks t
SET pipeline_version = pd.version
FROM pipeline_definitions pd
WHERE pd.name = t.pipeline
  AND pd.is_active = true;

-- ---------------------------------------------------------------------------
-- 5. Map phase='planning' to status='planning'
-- ---------------------------------------------------------------------------

UPDATE tasks
SET status = 'planning'
WHERE phase = 'planning' AND status = 'executing';

-- ---------------------------------------------------------------------------
-- 6. Add composite FK to pipeline_definitions
-- ---------------------------------------------------------------------------

ALTER TABLE tasks
    ADD CONSTRAINT fk_tasks_pipeline_definition
        FOREIGN KEY (pipeline, pipeline_version)
        REFERENCES pipeline_definitions(name, version);

-- ---------------------------------------------------------------------------
-- 7. Drop columns
-- ---------------------------------------------------------------------------

ALTER TABLE tasks DROP COLUMN IF EXISTS current_stage;
ALTER TABLE tasks DROP COLUMN IF EXISTS phase;
ALTER TABLE tasks DROP COLUMN IF EXISTS assigned_agent;

-- Drop the phase CHECK constraint (column is gone, but be explicit)
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_phase;

-- ---------------------------------------------------------------------------
-- 8. Drop pipeline_checkpoints table
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS pipeline_checkpoints;

-- ---------------------------------------------------------------------------
-- 9. Update get_task_context() function
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION get_task_context(p_task_id TEXT)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_task              JSONB;
    v_stages            JSONB;
    v_context           JSONB;
    v_validation_items  JSONB;
BEGIN
    -- Task metadata.
    SELECT jsonb_build_object(
        'id',                    t.id,
        'title',                 t.title,
        'status',                t.status,
        'pipeline',              t.pipeline,
        'pipeline_version',      t.pipeline_version,
        'repository',            t.repository,
        'last_completed_stage',  t.last_completed_stage,
        'initial_context',       t.initial_context,
        'planned_stages',        t.planned_stages,
        'created_at',            t.created_at,
        'started_at',            t.started_at
    )
    INTO v_task
    FROM tasks t
    WHERE t.id = p_task_id;

    IF v_task IS NULL THEN
        RETURN NULL;
    END IF;

    -- Only the latest run per (stage_key, iteration) — failed runs are preserved
    -- in the table but not surfaced in context to avoid confusing agents.
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'stage_number',         s.stage_number,
                'category',             s.category,
                'agent',                s.agent,
                'agent_version',        s.agent_version,
                'status',               s.status,
                'stage_key',            s.stage_key,
                'iteration',            s.iteration,
                'run',                  s.run,
                'started_at',           s.started_at,
                'completed_at',         s.completed_at,
                'structured_output',    s.structured_output,
                'validation_items_in',  s.validation_items_in,
                'validation_items_out', s.validation_items_out,
                'tokens_input',         s.tokens_input,
                'tokens_output',        s.tokens_output,
                'error_message',        s.error_message,
                'retry_count',          s.retry_count
            )
            ORDER BY s.stage_number, s.iteration
        ),
        '[]'::jsonb
    )
    INTO v_stages
    FROM (
        SELECT DISTINCT ON (COALESCE(s2.stage_key, s2.stage_number::text), s2.iteration)
               s2.*
        FROM stages s2
        WHERE s2.task_id = p_task_id
        ORDER BY COALESCE(s2.stage_key, s2.stage_number::text), s2.iteration, s2.run DESC
    ) s;

    -- Context entries.
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'id',           c.id,
                'stage_number', c.stage_number,
                'key',          c.key,
                'value_type',   c.value_type,
                'value',        CASE c.value_type
                                    WHEN 'json'     THEN c.value_json
                                    WHEN 'text'     THEN to_jsonb(c.value_text)
                                    WHEN 'file_ref' THEN to_jsonb(c.value_file_ref)
                                END,
                'created_at',   c.created_at
            )
            ORDER BY c.id
        ),
        '[]'::jsonb
    )
    INTO v_context
    FROM context c
    WHERE c.task_id = p_task_id;

    -- Validation items for this task.
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'id',           vi.id,
                'stage_key',    vi.stage_key,
                'category',     vi.category,
                'description',  vi.description,
                'status',       vi.status,
                'resolved_by',  vi.resolved_by,
                'resolved_at',  vi.resolved_at,
                'created_at',   vi.created_at
            )
            ORDER BY vi.id
        ),
        '[]'::jsonb
    )
    INTO v_validation_items
    FROM validation_items vi
    WHERE vi.task_id = p_task_id;

    RETURN jsonb_build_object(
        'task',             v_task,
        'stages',           v_stages,
        'context',          v_context,
        'validation_items', v_validation_items
    );
END;
$$;

COMMENT ON FUNCTION get_task_context(TEXT) IS
    'Returns a single JSONB document with task metadata, latest-run stage records, '
    'accumulated context entries, and all validation items for the given task_id. '
    'Returns NULL if the task does not exist. '
    'Only the latest run per (stage_key, iteration) is included — older runs are preserved in the table.';

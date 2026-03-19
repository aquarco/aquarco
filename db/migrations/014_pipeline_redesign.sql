-- Migration: 014_pipeline_redesign.sql
-- Purpose: Pipeline redesign — task as pipeline instance with planning phase.
--
-- Changes:
--   1. tasks: add phase, planned_stages columns; expand valid_category for 'planning'
--   2. stages: add input, iteration, stage_key, validation_items_in/out;
--      replace UNIQUE(task_id, stage_number) with UNIQUE(task_id, stage_key, iteration)
--   3. New table: validation_items
--   4. Updated get_task_context() to include new columns
--
-- Depends on: 002_create_tasks.sql, 003_create_stages.sql, 008_create_functions.sql

-- up

SET search_path TO aifishtank, public;

-- ---------------------------------------------------------------------------
-- 1. Tasks table changes
-- ---------------------------------------------------------------------------

-- Add phase column to track pipeline lifecycle (trigger → planning → running → completed → failed)
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT 'trigger';

ALTER TABLE tasks
    ADD CONSTRAINT valid_phase CHECK (
        phase IN ('trigger', 'planning', 'running', 'completed', 'failed')
    );

-- Planner output: agent assignments per category
ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS planned_stages JSONB;

-- Expand valid_category to include 'planning'
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_category;
ALTER TABLE tasks
    ADD CONSTRAINT valid_category CHECK (
        category IN ('review', 'implementation', 'test', 'design', 'docs', 'analyze', 'planning')
    );

-- ---------------------------------------------------------------------------
-- 2. Stages table changes
-- ---------------------------------------------------------------------------

-- Drop old unique constraint (task_id, stage_number) to allow multi-agent + iterations
ALTER TABLE stages DROP CONSTRAINT IF EXISTS stages_task_id_stage_number_key;

-- Full input context provided to agent
ALTER TABLE stages
    ADD COLUMN IF NOT EXISTS input JSONB;

-- Which run of this stage (1-based)
ALTER TABLE stages
    ADD COLUMN IF NOT EXISTS iteration INTEGER NOT NULL DEFAULT 1;

-- Disambiguates multi-agent stages: format "{stage_num}:{category}:{agent}"
ALTER TABLE stages
    ADD COLUMN IF NOT EXISTS stage_key TEXT;

-- Validation items this stage was asked to address
ALTER TABLE stages
    ADD COLUMN IF NOT EXISTS validation_items_in JSONB;

-- New validation items produced by this stage
ALTER TABLE stages
    ADD COLUMN IF NOT EXISTS validation_items_out JSONB;

-- New unique constraint: (task_id, stage_key, iteration)
-- stage_key may be NULL for legacy rows, so we use a partial unique index
CREATE UNIQUE INDEX IF NOT EXISTS idx_stages_task_stage_key_iteration
    ON stages(task_id, stage_key, iteration)
    WHERE stage_key IS NOT NULL;

-- Index for fetching stages by stage_key
CREATE INDEX IF NOT EXISTS idx_stages_stage_key
    ON stages(task_id, stage_key);

-- ---------------------------------------------------------------------------
-- 3. New table: validation_items
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS validation_items (
    id              BIGSERIAL PRIMARY KEY,

    -- Parent task
    task_id         TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,

    -- Stage that created this item (stage_key format)
    stage_key       TEXT,

    -- Which category should address this item
    category        TEXT NOT NULL,

    -- Human-readable finding, question, or fix request
    description     TEXT NOT NULL,

    -- Lifecycle status
    status          TEXT NOT NULL DEFAULT 'open',

    -- Stage that resolved this item (stage_key format)
    resolved_by     TEXT,

    -- When this item was resolved
    resolved_at     TIMESTAMPTZ,

    -- When this item was created
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_vi_status CHECK (status IN ('open', 'resolved', 'wont_fix'))
);

COMMENT ON TABLE  validation_items             IS 'Actionable findings that drive iteration loops between pipeline stages.';
COMMENT ON COLUMN validation_items.stage_key   IS 'The stage_key of the stage that created this item.';
COMMENT ON COLUMN validation_items.category    IS 'Which pipeline category should address this item.';
COMMENT ON COLUMN validation_items.status      IS 'Lifecycle: open → resolved | wont_fix.';

CREATE INDEX IF NOT EXISTS idx_validation_items_task
    ON validation_items(task_id, status);

CREATE INDEX IF NOT EXISTS idx_validation_items_category
    ON validation_items(task_id, category, status);

-- ---------------------------------------------------------------------------
-- 4. Updated get_task_context() function
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
    -- Task metadata (includes new phase and planned_stages columns).
    SELECT jsonb_build_object(
        'id',               t.id,
        'title',            t.title,
        'category',         t.category,
        'status',           t.status,
        'phase',            t.phase,
        'pipeline',         t.pipeline,
        'repository',       t.repository,
        'current_stage',    t.current_stage,
        'assigned_agent',   t.assigned_agent,
        'initial_context',  t.initial_context,
        'planned_stages',   t.planned_stages,
        'created_at',       t.created_at,
        'started_at',       t.started_at
    )
    INTO v_task
    FROM tasks t
    WHERE t.id = p_task_id;

    IF v_task IS NULL THEN
        RETURN NULL;
    END IF;

    -- All stages including new columns (input, iteration, stage_key, validation items).
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
                'input',                s.input,
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
    FROM stages s
    WHERE s.task_id = p_task_id;

    -- Context entries (unchanged).
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
    'Returns a single JSONB document with task metadata (incl. phase/planned_stages), '
    'all stage records (incl. stage_key/iteration/input/validation items), '
    'accumulated context entries, and all validation items for the given task_id. '
    'Returns NULL if the task does not exist.';

-- down

-- ALTER TABLE tasks DROP COLUMN IF EXISTS phase;
-- ALTER TABLE tasks DROP COLUMN IF EXISTS planned_stages;
-- ALTER TABLE stages DROP COLUMN IF EXISTS input;
-- ALTER TABLE stages DROP COLUMN IF EXISTS iteration;
-- ALTER TABLE stages DROP COLUMN IF EXISTS stage_key;
-- ALTER TABLE stages DROP COLUMN IF EXISTS validation_items_in;
-- ALTER TABLE stages DROP COLUMN IF EXISTS validation_items_out;
-- DROP TABLE IF EXISTS validation_items;

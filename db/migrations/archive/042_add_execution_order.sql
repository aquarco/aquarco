-- depends: 041_backfill_stage_model
-- Migration: 042_add_execution_order.sql
-- Purpose: Add execution_order column to stages for tracking actual invocation
--          sequence within a task. Replaces the previous (stageNumber, iteration, run)
--          sort for display and context purposes.
--
-- Changes:
--   1. Add nullable `execution_order` INTEGER column to stages
--   2. Create partial unique index on (task_id, execution_order) WHERE NOT NULL
--   3. Update get_task_context() to include execution_order in stage JSONB and
--      change ORDER BY to execution_order ASC NULLS LAST

SET search_path TO aquarco, public;

-- ---------------------------------------------------------------------------
-- 1. Add execution_order column
-- ---------------------------------------------------------------------------

ALTER TABLE stages
    ADD COLUMN IF NOT EXISTS execution_order INTEGER;

COMMENT ON COLUMN stages.execution_order IS
    'Actual invocation sequence within the task (1-based). '
    'NULL for PENDING stages not yet invoked. '
    'Assigned by the Python supervisor executor when a stage transitions to executing or skipped.';

-- ---------------------------------------------------------------------------
-- 2. Partial unique index to prevent duplicate assignment
-- ---------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS idx_stages_task_execution_order
    ON stages(task_id, execution_order)
    WHERE execution_order IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. Update get_task_context() to include execution_order
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
    -- Task metadata (no category — removed in 015).
    SELECT jsonb_build_object(
        'id',               t.id,
        'title',            t.title,
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

    -- Only the latest run per (stage_key, iteration) — failed runs are preserved
    -- in the table but not surfaced in context to avoid confusing agents.
    -- Now includes execution_order and sorts by it (NULLS LAST).
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
                'execution_order',      s.execution_order,
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
            ORDER BY s.execution_order ASC NULLS LAST, s.stage_number ASC, s.iteration ASC
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
    'Returns a single JSONB document with task metadata, latest-run stage records, '
    'accumulated context entries, and all validation items for the given task_id. '
    'Returns NULL if the task does not exist. '
    'Only the latest run per (stage_key, iteration) is included — older runs are preserved in the table. '
    'Stages are ordered by execution_order ASC NULLS LAST.';

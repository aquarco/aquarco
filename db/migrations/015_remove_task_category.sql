-- Migration: 015_remove_task_category.sql
-- Purpose: Remove task-level category. Pipeline defines the stages/categories;
--          task only needs a pipeline reference.
--
-- The category column on stages and validation_items is preserved — those
-- represent pipeline stage categories, not task categories.
--
-- Depends on: 002_create_tasks.sql, 014_pipeline_redesign.sql

-- up

SET search_path TO aquarco, public;

-- Drop the valid_category CHECK constraint from tasks
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_category;

-- Drop the category index
DROP INDEX IF EXISTS idx_tasks_category;

-- Make the column nullable first (for backwards compat during rollout),
-- then drop it.
ALTER TABLE tasks ALTER COLUMN category DROP NOT NULL;
ALTER TABLE tasks DROP COLUMN IF EXISTS category;

-- Make pipeline NOT NULL with a default for any existing rows
-- (existing NULL pipelines get 'feature-pipeline' as a sensible default)
UPDATE tasks SET pipeline = 'feature-pipeline' WHERE pipeline IS NULL OR pipeline = '';
ALTER TABLE tasks ALTER COLUMN pipeline SET NOT NULL;
ALTER TABLE tasks ALTER COLUMN pipeline SET DEFAULT 'feature-pipeline';

-- Update get_task_context() to remove category from task metadata
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
    -- Task metadata (no more category).
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

    -- All stages (category preserved here — it's the pipeline stage category).
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

-- down

-- ALTER TABLE tasks ADD COLUMN category TEXT;
-- ALTER TABLE tasks ALTER COLUMN pipeline DROP NOT NULL;
-- ALTER TABLE tasks ALTER COLUMN pipeline DROP DEFAULT;

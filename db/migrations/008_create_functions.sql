-- Migration: 008_create_functions.sql
-- Purpose: Database functions and triggers used by the application layer.
--
-- Functions defined here:
--   1. update_updated_at()     — trigger function that keeps tasks.updated_at current
--   2. get_task_context(TEXT)  — returns full task context as a single JSONB document

-- up

SET search_path TO aquarco, public;

-- ---------------------------------------------------------------------------
-- 1. update_updated_at
--    Generic trigger function that sets updated_at = NOW() on every UPDATE.
--    Attach to any table that has an updated_at column.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION update_updated_at() IS
    'Trigger function: sets updated_at = NOW() on every UPDATE. '
    'Attach with: CREATE TRIGGER ... BEFORE UPDATE ON <table> FOR EACH ROW EXECUTE FUNCTION update_updated_at();';

-- Attach the trigger to tasks.
-- Use CREATE OR REPLACE where possible; for triggers, drop-and-recreate is idempotent.
DROP TRIGGER IF EXISTS trg_tasks_updated_at ON tasks;
CREATE TRIGGER trg_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

-- ---------------------------------------------------------------------------
-- 2. get_task_context(p_task_id TEXT)
--    Returns a single JSONB document with everything the pipeline executor
--    needs to build an agent's context window for a given stage:
--
--    {
--      "task": {
--        "id": "...",
--        "title": "...",
--        "category": "...",
--        "pipeline": "...",
--        "current_stage": 2,
--        "initial_context": { ... }
--      },
--      "stages": [
--        {
--          "stage_number": 0,
--          "category": "analyze",
--          "agent": "analyze-agent",
--          "status": "completed",
--          "structured_output": { ... }
--        },
--        ...
--      ],
--      "context": [
--        {
--          "stage_number": 0,
--          "key": "analysis_result",
--          "value_type": "json",
--          "value": { ... }
--        },
--        ...
--      ]
--    }
--
--    Returns NULL if the task_id does not exist.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION get_task_context(p_task_id TEXT)
RETURNS JSONB
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_task      JSONB;
    v_stages    JSONB;
    v_context   JSONB;
BEGIN
    -- Task metadata.
    SELECT jsonb_build_object(
        'id',               t.id,
        'title',            t.title,
        'category',         t.category,
        'status',           t.status,
        'pipeline',         t.pipeline,
        'repository',       t.repository,
        'current_stage',    t.current_stage,
        'assigned_agent',   t.assigned_agent,
        'initial_context',  t.initial_context,
        'created_at',       t.created_at,
        'started_at',       t.started_at
    )
    INTO v_task
    FROM tasks t
    WHERE t.id = p_task_id;

    -- Return NULL early if the task does not exist.
    IF v_task IS NULL THEN
        RETURN NULL;
    END IF;

    -- All stages for this task, ordered for consumption by the executor.
    SELECT COALESCE(
        jsonb_agg(
            jsonb_build_object(
                'stage_number',      s.stage_number,
                'category',          s.category,
                'agent',             s.agent,
                'agent_version',     s.agent_version,
                'status',            s.status,
                'started_at',        s.started_at,
                'completed_at',      s.completed_at,
                'structured_output', s.structured_output,
                'tokens_input',      s.tokens_input,
                'tokens_output',     s.tokens_output,
                'error_message',     s.error_message,
                'retry_count',       s.retry_count
            )
            ORDER BY s.stage_number
        ),
        '[]'::jsonb
    )
    INTO v_stages
    FROM stages s
    WHERE s.task_id = p_task_id;

    -- All context entries for this task, ordered by creation time.
    -- The value column is normalised: the correct typed value is placed
    -- under the 'value' key regardless of which column it was stored in.
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

    RETURN jsonb_build_object(
        'task',    v_task,
        'stages',  v_stages,
        'context', v_context
    );
END;
$$;

COMMENT ON FUNCTION get_task_context(TEXT) IS
    'Returns a single JSONB document with task metadata, all stage records, '
    'and all accumulated context entries for the given task_id. '
    'Returns NULL if the task does not exist. '
    'Used by the pipeline executor to assemble an agent context window.';

-- down

-- DROP TRIGGER IF EXISTS trg_tasks_updated_at ON aquarco.tasks;
-- DROP FUNCTION IF EXISTS aquarco.update_updated_at();
-- DROP FUNCTION IF EXISTS aquarco.get_task_context(TEXT);

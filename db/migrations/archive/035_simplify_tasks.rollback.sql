-- depends: 035_simplify_tasks

SET search_path TO aquarco, public;

-- ---------------------------------------------------------------------------
-- 1. Restore pipeline_checkpoints table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
    task_id              TEXT        PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    last_completed_stage BIGINT      NOT NULL REFERENCES stages(id) ON DELETE CASCADE,
    checkpoint_data      JSONB                DEFAULT '{}',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE  pipeline_checkpoints                      IS 'Per-task resume checkpoint; enables the executor to restart cleanly after a crash.';
COMMENT ON COLUMN pipeline_checkpoints.last_completed_stage IS 'Foreign key to stages.id — the last stage row that completed successfully.';
COMMENT ON COLUMN pipeline_checkpoints.checkpoint_data      IS 'Executor state needed on resume: branch, workspace, env vars, etc.';

-- Migrate checkpoint data back from tasks
INSERT INTO pipeline_checkpoints (task_id, last_completed_stage, checkpoint_data)
SELECT id, last_completed_stage, checkpoint_data
FROM tasks
WHERE last_completed_stage IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. Restore dropped columns
-- ---------------------------------------------------------------------------

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS current_stage INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assigned_agent TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS phase TEXT NOT NULL DEFAULT 'trigger';

COMMENT ON COLUMN tasks.current_stage  IS '0-based index of the stage currently executing.';
COMMENT ON COLUMN tasks.assigned_agent IS 'Agent instance name currently executing this task.';

ALTER TABLE tasks ADD CONSTRAINT valid_phase
    CHECK (phase IN ('trigger', 'planning', 'running', 'completed', 'failed'));

-- Map planning status back to phase=planning, status=executing
UPDATE tasks SET phase = 'planning', status = 'executing'
WHERE status = 'planning';

-- Backfill current_stage from last_completed_stage
UPDATE tasks t
SET current_stage = COALESCE(
    (SELECT s.stage_number + 1 FROM stages s WHERE s.id = t.last_completed_stage),
    0
);

-- Backfill phase from status for completed/failed
UPDATE tasks SET phase = 'completed' WHERE status = 'completed';
UPDATE tasks SET phase = 'failed' WHERE status = 'failed';
UPDATE tasks SET phase = 'running' WHERE status = 'executing';

-- ---------------------------------------------------------------------------
-- 3. Drop composite FK and new columns
-- ---------------------------------------------------------------------------

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS fk_tasks_pipeline_definition;
ALTER TABLE tasks DROP COLUMN IF EXISTS last_completed_stage;
ALTER TABLE tasks DROP COLUMN IF EXISTS checkpoint_data;
ALTER TABLE tasks DROP COLUMN IF EXISTS pipeline_version;

-- ---------------------------------------------------------------------------
-- 4. Restore status CHECK (without 'planning')
-- ---------------------------------------------------------------------------

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE tasks ADD CONSTRAINT valid_status
    CHECK (status IN ('pending','queued','executing','completed','failed','timeout','blocked','rate_limited','closed'));

-- ---------------------------------------------------------------------------
-- 5. Restore get_task_context() with original columns
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

COMMENT ON FUNCTION get_task_context(TEXT) IS
    'Returns a single JSONB document with task metadata, latest-run stage records, '
    'accumulated context entries, and all validation items for the given task_id. '
    'Returns NULL if the task does not exist. '
    'Only the latest run per (stage_key, iteration) is included — older runs are preserved in the table.';

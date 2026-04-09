-- depends:
-- Migration: 000_consolidated_init.sql
-- Purpose: Consolidated database init script replacing 44 incremental migrations
--          (000_init through 043_fix_get_task_context). Creates the full as-built
--          schema for the Aquarco agent system in a single atomic migration.
--
-- This file is the canonical representation of the database schema.
-- All 11 living tables, indexes, triggers, functions, and seed data are included.
--
-- Table creation order respects FK dependencies:
--   1. repositories
--   2. pipeline_definitions
--   3. agent_definitions
--   4. tasks (without last_completed_stage FK)
--   5. stages
--   6. ALTER TABLE tasks ADD CONSTRAINT fk_tasks_last_completed_stage (deferred)
--   7. context
--   8. poll_state
--   9. agent_instances
--  10. agent_overrides
--  11. validation_items
--  12. supervisor_state

-- ═══════════════════════════════════════════════════════════════════════════════
-- Schema & extensions
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS aquarco;

-- Pin the role-level search_path so that the PostgreSQL default ("$user", public)
-- does not resolve "$user" to the aquarco schema. Without this, tools that open
-- their own connections (e.g. yoyo-migrations) would create internal tables in
-- aquarco instead of public.
ALTER ROLE aquarco SET search_path TO public;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

SET search_path TO aquarco, public;

-- ═══════════════════════════════════════════════════════════════════════════════
-- Trigger function: update_updated_at()
-- ═══════════════════════════════════════════════════════════════════════════════

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

-- ═══════════════════════════════════════════════════════════════════════════════
-- 1. repositories
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE repositories (
    name              TEXT        PRIMARY KEY,
    url               TEXT        NOT NULL,
    branch            TEXT,
    clone_dir         TEXT        NOT NULL,
    pollers           TEXT[]      NOT NULL DEFAULT '{}',
    last_cloned_at    TIMESTAMPTZ,
    last_pulled_at    TIMESTAMPTZ,
    clone_status      TEXT        NOT NULL DEFAULT 'pending',
    head_sha          TEXT,
    error_message     TEXT,
    deploy_public_key TEXT,
    original_url      TEXT,

    CONSTRAINT valid_clone_status CHECK (
        clone_status IN ('pending', 'cloning', 'ready', 'error')
    ),
    CONSTRAINT chk_repos_name_nonempty   CHECK (name != ''),
    CONSTRAINT chk_repos_url_nonempty    CHECK (url != ''),
    CONSTRAINT chk_repos_clone_dir_nonempty CHECK (clone_dir != '')
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 2. pipeline_definitions
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE pipeline_definitions (
    name            TEXT        NOT NULL,
    version         TEXT        NOT NULL,
    trigger_config  JSONB       NOT NULL DEFAULT '{}',
    stages          JSONB       NOT NULL DEFAULT '[]',
    categories      JSONB       NOT NULL DEFAULT '{}',
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (name, version),

    CONSTRAINT chk_pipeline_def_name_nonempty    CHECK (name != ''),
    CONSTRAINT chk_pipeline_def_version_nonempty CHECK (version != '')
);

CREATE INDEX idx_pipeline_definitions_active
    ON pipeline_definitions (name) WHERE is_active;

CREATE TRIGGER trg_pipeline_definitions_updated_at
    BEFORE UPDATE ON pipeline_definitions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ═══════════════════════════════════════════════════════════════════════════════
-- 3. agent_definitions
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE agent_definitions (
    name            TEXT        NOT NULL,
    version         TEXT        NOT NULL,
    description     TEXT        NOT NULL,
    labels          JSONB       DEFAULT '{}',
    spec            JSONB       NOT NULL,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    source          TEXT        NOT NULL DEFAULT 'default',
    agent_group     TEXT        NOT NULL DEFAULT 'pipeline',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (name, version),

    CONSTRAINT chk_agent_def_name_nonempty    CHECK (name != ''),
    CONSTRAINT chk_agent_def_version_nonempty CHECK (version != ''),
    CONSTRAINT chk_agent_def_group            CHECK (agent_group IN ('system', 'pipeline'))
);

CREATE INDEX idx_agent_definitions_active
    ON agent_definitions (name) WHERE is_active;

CREATE INDEX idx_agent_definitions_source
    ON agent_definitions (source) WHERE is_active;

CREATE INDEX idx_agent_definitions_group
    ON agent_definitions (agent_group);

CREATE TRIGGER trg_agent_definitions_updated_at
    BEFORE UPDATE ON agent_definitions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ═══════════════════════════════════════════════════════════════════════════════
-- 4. tasks (without last_completed_stage FK — added after stages table exists)
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE tasks (
    id                          TEXT        PRIMARY KEY,
    title                       TEXT        NOT NULL,
    status                      TEXT        NOT NULL DEFAULT 'pending',
    priority                    INTEGER     NOT NULL DEFAULT 50,
    source                      TEXT        NOT NULL,
    source_ref                  TEXT,
    pipeline                    TEXT        NOT NULL DEFAULT 'feature-pipeline',
    pipeline_version            TEXT,
    repository                  TEXT        NOT NULL REFERENCES repositories(name),
    initial_context             JSONB,
    planned_stages              JSONB,
    last_completed_stage        BIGINT,
    checkpoint_data             JSONB       NOT NULL DEFAULT '{}',
    parent_task_id              TEXT        REFERENCES tasks(id) ON DELETE SET NULL,
    pr_number                   INTEGER,
    branch_name                 TEXT,
    rate_limit_count            INTEGER     NOT NULL DEFAULT 0,
    postpone_cooldown_minutes   INTEGER     NOT NULL DEFAULT 60,
    retry_count                 INTEGER     NOT NULL DEFAULT 0,
    error_message               TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at                  TIMESTAMPTZ,
    completed_at                TIMESTAMPTZ,

    CONSTRAINT valid_status CHECK (
        status IN (
            'pending', 'queued', 'planning', 'executing',
            'completed', 'failed', 'timeout', 'blocked',
            'rate_limited', 'closed'
        )
    ),
    CONSTRAINT valid_priority                CHECK (priority BETWEEN 0 AND 100),
    CONSTRAINT chk_tasks_title_nonempty      CHECK (title != ''),
    CONSTRAINT chk_tasks_source_nonempty     CHECK (source != ''),
    CONSTRAINT chk_tasks_pipeline_nonempty   CHECK (pipeline != ''),
    CONSTRAINT chk_tasks_retry_nonneg        CHECK (retry_count >= 0),
    CONSTRAINT chk_tasks_rate_limit_nonneg   CHECK (rate_limit_count >= 0),
    CONSTRAINT chk_tasks_postpone_positive   CHECK (postpone_cooldown_minutes > 0),

    CONSTRAINT fk_tasks_pipeline_definition
        FOREIGN KEY (pipeline, pipeline_version)
        REFERENCES pipeline_definitions(name, version)
);

CREATE INDEX idx_tasks_parent
    ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;

CREATE TRIGGER trg_tasks_updated_at
    BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ═══════════════════════════════════════════════════════════════════════════════
-- 5. stages
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE stages (
    id                  BIGSERIAL       PRIMARY KEY,
    task_id             TEXT            NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage_number        INTEGER         NOT NULL,
    category            TEXT            NOT NULL,
    agent               TEXT,
    agent_version       TEXT,
    status              TEXT            NOT NULL DEFAULT 'pending',
    stage_key           TEXT,
    iteration           INTEGER         NOT NULL DEFAULT 1,
    run                 INTEGER         NOT NULL DEFAULT 1,
    execution_order     INTEGER,
    input               JSONB,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    structured_output   JSONB,
    raw_output          TEXT,
    validation_items_in JSONB,
    validation_items_out JSONB,
    tokens_input        INTEGER,
    tokens_output       INTEGER,
    error_message       TEXT,
    retry_count         INTEGER         NOT NULL DEFAULT 0,
    live_output         TEXT,
    cost_usd            NUMERIC,
    cache_read_tokens   INTEGER,
    cache_write_tokens  INTEGER,
    session_id          TEXT,
    model               VARCHAR(100),
    msg_spending_state  JSONB           DEFAULT '{}'::jsonb,

    CONSTRAINT valid_stage_status CHECK (
        status IN ('pending', 'executing', 'completed', 'failed', 'skipped', 'rate_limited', 'max_turns')
    ),
    CONSTRAINT chk_stages_stage_num_nonneg      CHECK (stage_number >= 0),
    CONSTRAINT chk_stages_iteration_positive     CHECK (iteration >= 1),
    CONSTRAINT chk_stages_run_positive           CHECK (run >= 1),
    CONSTRAINT chk_stages_retry_nonneg           CHECK (retry_count >= 0),
    CONSTRAINT chk_stages_tokens_input_nonneg    CHECK (tokens_input IS NULL OR tokens_input >= 0),
    CONSTRAINT chk_stages_tokens_output_nonneg   CHECK (tokens_output IS NULL OR tokens_output >= 0),
    CONSTRAINT chk_stages_cost_nonneg            CHECK (cost_usd IS NULL OR cost_usd >= 0)
);

-- Unique constraint for multi-agent stages (stage_key may be NULL for legacy rows)
CREATE UNIQUE INDEX idx_stages_task_stage_key_iteration_run
    ON stages(task_id, stage_key, iteration, run)
    WHERE stage_key IS NOT NULL;

-- Index for fetching stages by stage_key
CREATE INDEX idx_stages_stage_key
    ON stages(task_id, stage_key);

-- Partial unique index to prevent duplicate execution_order within a task
CREATE UNIQUE INDEX idx_stages_task_execution_order
    ON stages(task_id, execution_order)
    WHERE execution_order IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 6. Deferred FK: tasks.last_completed_stage -> stages.id
-- ═══════════════════════════════════════════════════════════════════════════════

ALTER TABLE tasks
    ADD CONSTRAINT fk_tasks_last_completed_stage
        FOREIGN KEY (last_completed_stage) REFERENCES stages(id) ON DELETE SET NULL;

-- FK indexes for delete performance
CREATE INDEX idx_tasks_last_completed_stage
    ON tasks(last_completed_stage) WHERE last_completed_stage IS NOT NULL;

CREATE INDEX idx_tasks_pipeline_version
    ON tasks(pipeline, pipeline_version);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 7. context
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE context (
    id              BIGSERIAL       PRIMARY KEY,
    task_id         TEXT            NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage_number    INTEGER,
    key             TEXT            NOT NULL,
    value_type      TEXT            NOT NULL,
    value_json      JSONB,
    value_text      TEXT,
    value_file_ref  TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    UNIQUE (task_id, key),

    CONSTRAINT valid_value_type CHECK (
        value_type IN ('json', 'text', 'file_ref')
    ),
    CONSTRAINT value_consistency CHECK (
        (value_type = 'json'     AND value_json     IS NOT NULL AND value_text IS NULL     AND value_file_ref IS NULL) OR
        (value_type = 'text'     AND value_text     IS NOT NULL AND value_json IS NULL     AND value_file_ref IS NULL) OR
        (value_type = 'file_ref' AND value_file_ref IS NOT NULL AND value_json IS NULL     AND value_text     IS NULL)
    ),
    CONSTRAINT chk_context_key_nonempty CHECK (key != '')
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 8. poll_state
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE poll_state (
    poller_name         TEXT        PRIMARY KEY,
    last_poll_at        TIMESTAMPTZ,
    last_successful_at  TIMESTAMPTZ,
    cursor              TEXT,
    state_data          JSONB
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 9. agent_instances
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE agent_instances (
    agent_name          TEXT        PRIMARY KEY,
    active_count        INTEGER     NOT NULL DEFAULT 0,
    total_executions    INTEGER     NOT NULL DEFAULT 0,
    total_tokens_used   BIGINT      NOT NULL DEFAULT 0,
    last_execution_at   TIMESTAMPTZ,

    CONSTRAINT active_count_non_negative      CHECK (active_count >= 0),
    CONSTRAINT total_executions_non_negative   CHECK (total_executions >= 0),
    CONSTRAINT total_tokens_non_negative       CHECK (total_tokens_used >= 0)
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 10. agent_overrides
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE agent_overrides (
    agent_name      TEXT        NOT NULL,
    scope           TEXT        NOT NULL DEFAULT 'global',
    is_disabled     BOOLEAN     NOT NULL DEFAULT FALSE,
    modified_spec   JSONB,
    modified_by     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    PRIMARY KEY (agent_name, scope),

    CONSTRAINT chk_agent_override_scope_nonempty CHECK (scope != '')
);

CREATE TRIGGER trg_agent_overrides_updated_at
    BEFORE UPDATE ON agent_overrides
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ═══════════════════════════════════════════════════════════════════════════════
-- 11. validation_items
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE validation_items (
    id              BIGSERIAL   PRIMARY KEY,
    task_id         TEXT        NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    stage_key       TEXT,
    category        TEXT        NOT NULL,
    description     TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'open',
    resolved_by     TEXT,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_vi_status CHECK (status IN ('open', 'resolved', 'wont_fix')),
    CONSTRAINT chk_vi_resolution_consistency CHECK (
        (status != 'resolved') OR (resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
    )
);

COMMENT ON TABLE  validation_items             IS 'Actionable findings that drive iteration loops between pipeline stages.';
COMMENT ON COLUMN validation_items.stage_key   IS 'The stage_key of the stage that created this item.';
COMMENT ON COLUMN validation_items.category    IS 'Which pipeline category should address this item.';
COMMENT ON COLUMN validation_items.status      IS 'Lifecycle: open -> resolved | wont_fix.';

CREATE INDEX idx_validation_items_task
    ON validation_items(task_id, status);

CREATE INDEX idx_validation_items_category
    ON validation_items(task_id, category, status);

-- ═══════════════════════════════════════════════════════════════════════════════
-- 12. supervisor_state
-- ═══════════════════════════════════════════════════════════════════════════════

CREATE TABLE supervisor_state (
    key         TEXT        PRIMARY KEY,
    value       TEXT        NOT NULL DEFAULT 'false',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_supervisor_key_nonempty CHECK (key != '')
);

-- ═══════════════════════════════════════════════════════════════════════════════
-- Function: get_task_context(TEXT)
-- Returns a single JSONB document with task metadata, latest-run stage records,
-- accumulated context entries, and all validation items for a given task_id.
-- Returns NULL if the task does not exist.
-- ═══════════════════════════════════════════════════════════════════════════════

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
    -- Task metadata (post-035 schema: no phase/current_stage/assigned_agent).
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
    -- Includes execution_order and sorts by it (NULLS LAST).
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
    'Only the latest run per (stage_key, iteration) is included — older runs are preserved in the table. '
    'Stages are ordered by execution_order ASC NULLS LAST.';

-- ═══════════════════════════════════════════════════════════════════════════════
-- Seed data
-- ═══════════════════════════════════════════════════════════════════════════════

INSERT INTO supervisor_state (key, value)
VALUES ('drain_mode', 'false')
ON CONFLICT (key) DO NOTHING;

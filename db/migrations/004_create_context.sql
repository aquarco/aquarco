-- Migration: 004_create_context.sql
-- Purpose: Accumulated context storage for task pipelines.
--
-- As a task moves through pipeline stages, each agent may produce outputs
-- (diffs, analysis results, file paths, JSON structures) that subsequent
-- stages need. This table stores those keyed values.
--
-- Three value types are supported:
--   json      — structured data stored in a JSONB column
--   text      — freeform text (summaries, commit messages, etc.)
--   file_ref  — path to a blob file on disk, e.g. 'blobs/abc123.patch'
--
-- Exactly one of value_json, value_text, or value_file_ref must be non-null
-- for any given row. This is enforced via a CHECK constraint.
--
-- Depends on: 002_create_tasks.sql (tasks table)

-- up

SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS context (
    id              BIGSERIAL       PRIMARY KEY,

    -- Parent task.
    task_id         TEXT            NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,

    -- The stage that produced this context entry (NULL = created before pipeline).
    stage_number    INTEGER,

    -- Lookup key; unique per (task_id, key) to allow upsert patterns.
    -- The UNIQUE constraint below backs ON CONFLICT (task_id, key) upserts.
    key             TEXT            NOT NULL,

    -- Discriminator for which value column is populated.
    value_type      TEXT            NOT NULL,

    -- Only one of these three columns should be non-null per row.
    value_json      JSONB,
    value_text      TEXT,
    -- Relative path to a blob file managed outside the database.
    value_file_ref  TEXT,

    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- One key per task; enables ON CONFLICT (task_id, key) DO UPDATE upserts.
    UNIQUE (task_id, key),

    CONSTRAINT valid_value_type CHECK (
        value_type IN ('json', 'text', 'file_ref')
    ),

    -- Ensure exactly one value column is populated to match value_type.
    CONSTRAINT value_consistency CHECK (
        (value_type = 'json'     AND value_json     IS NOT NULL AND value_text IS NULL     AND value_file_ref IS NULL) OR
        (value_type = 'text'     AND value_text     IS NOT NULL AND value_json IS NULL     AND value_file_ref IS NULL) OR
        (value_type = 'file_ref' AND value_file_ref IS NOT NULL AND value_json IS NULL     AND value_text     IS NULL)
    )
);

COMMENT ON TABLE  context                IS 'Keyed context values accumulated across pipeline stages for a task.';
COMMENT ON COLUMN context.stage_number   IS 'Stage that produced this entry; NULL if pre-pipeline.';
COMMENT ON COLUMN context.key            IS 'Lookup key for the context entry; unique per task.';
COMMENT ON COLUMN context.value_type     IS 'Discriminator: json | text | file_ref.';
COMMENT ON COLUMN context.value_file_ref IS 'Relative path to a blob on disk, e.g. blobs/abc123.patch.';

-- Efficient lookup of all context entries for a task (primary access pattern).
CREATE INDEX IF NOT EXISTS idx_context_task_id
    ON context(task_id);

-- Note: the UNIQUE (task_id, key) constraint already creates an index on
-- (task_id, key); no additional explicit index is needed for that access pattern.

-- down

-- DROP TABLE IF EXISTS aquarco.context;

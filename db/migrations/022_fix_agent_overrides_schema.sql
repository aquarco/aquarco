-- depends: 019_agent_overrides_and_source
-- Migration: 022_fix_agent_overrides_schema.sql
-- Purpose: Fix missing search_path from migration 019 and add orphan cleanup.
--
-- Migration 019 omitted SET search_path TO aquarco, public; which could
-- cause objects to be created in the wrong schema. This migration re-runs
-- the statements idempotently under the correct search_path.
--
-- Also documents why agent_overrides has no FK to agent_definitions
-- (PK is composite (name, version) and partial unique index cannot serve
-- as FK target) and cleans up any orphaned override rows.
--
-- Depends on: 019_agent_overrides_and_source.sql

SET search_path TO aquarco, public;

-- Ensure source column exists on agent_definitions
ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_agent_definitions_source
    ON agent_definitions (source) WHERE is_active;

-- Ensure agent_overrides table exists in correct schema
CREATE TABLE IF NOT EXISTS agent_overrides (
    agent_name   TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'global',
    is_disabled  BOOLEAN NOT NULL DEFAULT FALSE,
    modified_spec JSONB,
    modified_by  TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (agent_name, scope)
);

-- Clean up orphan overrides where the agent no longer exists
DELETE FROM agent_overrides
WHERE agent_name NOT IN (
    SELECT name FROM agent_definitions WHERE is_active = true
);

COMMENT ON TABLE agent_overrides IS
    'Overrides reference agent_definitions by name (not version). '
    'A FK is not possible because agent_definitions PK is (name, version) '
    'and the partial unique index on (name) WHERE is_active cannot serve as a FK target. '
    'Orphans are cleaned up by periodic application-level queries.';

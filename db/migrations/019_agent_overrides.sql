-- Migration: 019_agent_overrides.sql
-- Purpose: Add source tracking to agent_definitions and create agent_overrides table
-- for per-agent disable/modify capabilities.

-- up

SET search_path TO aquarco, public;

-- Add source tracking columns to agent_definitions
ALTER TABLE agent_definitions
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'default',
  ADD COLUMN IF NOT EXISTS source_repository TEXT;

-- Constraint: source must be one of the valid values
ALTER TABLE agent_definitions
  ADD CONSTRAINT agent_definitions_source_check
  CHECK (source IN ('default', 'global', 'repository'));

-- Agent overrides: tracks disabled state and spec modifications per agent
-- Modifications persist to DB; a separate action creates a PR to push them back.
CREATE TABLE IF NOT EXISTS agent_overrides (
  id SERIAL PRIMARY KEY,
  agent_name TEXT NOT NULL,
  agent_version TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'global',         -- 'global' or 'repository'
  scope_repository TEXT,                         -- NULL for global scope, repo name for repo scope
  is_disabled BOOLEAN NOT NULL DEFAULT false,
  modified_spec JSONB,                           -- NULL means no modification; contains full modified spec
  modified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (agent_name, scope, scope_repository)
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_agent_overrides_agent_name ON agent_overrides (agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_overrides_scope ON agent_overrides (scope, scope_repository);

-- down

-- DROP TABLE IF EXISTS agent_overrides;
-- ALTER TABLE agent_definitions DROP COLUMN IF EXISTS source;
-- ALTER TABLE agent_definitions DROP COLUMN IF EXISTS source_repository;

-- depends: 018_rename_schema
SET search_path TO aquarco, public;
-- Add source column to agent_definitions to track where each agent comes from:
--   'default'          = built-in agents from default config
--   'global:<repo>'    = agents from a global config repository
--   'repo:<repo>'      = repository-specific agents
ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'default';

-- Partial index on source for active agents (speeds up filtered queries)
CREATE INDEX IF NOT EXISTS idx_agent_definitions_source
    ON agent_definitions (source) WHERE is_active;

-- Agent overrides table: stores per-agent disable/modify state
-- Scope is 'global' for global agents or 'repo:<name>' for repository-specific agents
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

-- Auto-update updated_at on agent_overrides (reuses existing function from 008)
CREATE TRIGGER trg_agent_overrides_updated_at
    BEFORE UPDATE ON agent_overrides
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

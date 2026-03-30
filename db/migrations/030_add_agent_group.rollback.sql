-- Rollback migration 030: Remove agent_group column from agent_definitions
SET search_path TO aquarco, public;

DROP INDEX IF EXISTS idx_agent_definitions_group;

ALTER TABLE agent_definitions
  DROP COLUMN IF EXISTS agent_group;

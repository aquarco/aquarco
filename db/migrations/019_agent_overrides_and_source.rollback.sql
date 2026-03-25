SET search_path TO aquarco, public;
DROP TRIGGER IF EXISTS trg_agent_overrides_updated_at ON agent_overrides;
DROP TABLE IF EXISTS agent_overrides;
DROP INDEX IF EXISTS idx_agent_definitions_source;
ALTER TABLE agent_definitions DROP COLUMN IF EXISTS source;

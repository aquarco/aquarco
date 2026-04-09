SET search_path TO aquarco, public;
DROP TRIGGER IF EXISTS trg_pipeline_definitions_updated_at ON pipeline_definitions;
DROP TRIGGER IF EXISTS trg_agent_definitions_updated_at ON agent_definitions;
DROP INDEX IF EXISTS idx_pipeline_definitions_active;
DROP INDEX IF EXISTS idx_agent_definitions_active;
DROP TABLE IF EXISTS pipeline_definitions;
DROP TABLE IF EXISTS agent_definitions;

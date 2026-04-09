-- depends: 028_repo_agent_scans
-- Add categories JSONB column to pipeline_definitions
-- Stores the category -> outputSchema mapping from pipelines.yaml
SET search_path TO aquarco, public;
ALTER TABLE pipeline_definitions
    ADD COLUMN IF NOT EXISTS categories JSONB NOT NULL DEFAULT '{}';

-- Add categories JSONB column to pipeline_definitions
-- Stores the category -> outputSchema mapping from pipelines.yaml
ALTER TABLE pipeline_definitions
    ADD COLUMN IF NOT EXISTS categories JSONB NOT NULL DEFAULT '{}';

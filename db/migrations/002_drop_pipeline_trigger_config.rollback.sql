-- depends: 001_add_git_flow_config
-- Migration: Drop trigger_config column from pipeline_definitions

SET search_path TO aquarco, public;

-- rollback
ALTER TABLE pipeline_definitions
    ADD COLUMN IF NOT EXISTS trigger_config JSONB NOT NULL DEFAULT '{}';

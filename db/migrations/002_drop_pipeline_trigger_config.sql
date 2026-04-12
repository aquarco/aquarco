-- depends: 001_add_git_flow_config
-- Migration: Drop trigger_config column from pipeline_definitions
-- Trigger configuration has been removed from pipelines; selection is now
-- driven exclusively by repository-level git_flow_config rules.

SET search_path TO aquarco, public;

-- apply
ALTER TABLE pipeline_definitions
    DROP COLUMN IF EXISTS trigger_config;

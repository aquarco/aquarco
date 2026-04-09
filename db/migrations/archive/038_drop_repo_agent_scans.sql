-- depends: 037_add_max_turns_stage_status
-- Migration 038: Drop repo_agent_scans table (autoloading subsystem removed)
SET search_path TO aquarco, public;

DROP INDEX IF EXISTS idx_repo_agent_scans_repo;
DROP TABLE IF EXISTS repo_agent_scans;

-- Rollback migration 028: Drop repo_agent_scans table
SET search_path TO aquarco, public;
DROP TABLE IF EXISTS repo_agent_scans;

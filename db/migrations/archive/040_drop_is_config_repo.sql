-- depends: 039_add_stage_msg_spending_state
-- Migration 040: Drop is_config_repo column from repositories
--
-- The per-repo .aquarco.yaml overlay system has been removed.
-- The is_config_repo flag was used to identify the global config repo
-- that provided the overlay; it is no longer needed.
SET search_path TO aquarco, public;

ALTER TABLE repositories DROP COLUMN IF EXISTS is_config_repo;

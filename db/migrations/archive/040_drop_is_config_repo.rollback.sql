-- Rollback for 040_drop_is_config_repo
SET search_path TO aquarco, public;

ALTER TABLE repositories ADD COLUMN IF NOT EXISTS is_config_repo BOOLEAN NOT NULL DEFAULT FALSE;

SET search_path TO aquarco, public;
ALTER TABLE repositories DROP COLUMN IF EXISTS is_config_repo;

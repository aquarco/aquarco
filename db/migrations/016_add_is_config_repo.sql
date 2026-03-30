-- depends: 015_remove_task_category
SET search_path TO aquarco, public;
ALTER TABLE repositories ADD COLUMN is_config_repo BOOLEAN NOT NULL DEFAULT FALSE;

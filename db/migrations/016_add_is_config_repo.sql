-- depends: 015_remove_task_category
ALTER TABLE repositories ADD COLUMN is_config_repo BOOLEAN NOT NULL DEFAULT FALSE;

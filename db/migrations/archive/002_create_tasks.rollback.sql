SET search_path TO aquarco, public;
DROP INDEX IF EXISTS idx_tasks_status_priority;
DROP INDEX IF EXISTS idx_tasks_created;
DROP INDEX IF EXISTS idx_tasks_repository;
DROP INDEX IF EXISTS idx_tasks_pipeline;
DROP INDEX IF EXISTS idx_tasks_category;
DROP INDEX IF EXISTS idx_tasks_status;
DROP TABLE IF EXISTS tasks;

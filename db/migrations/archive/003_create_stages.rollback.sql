SET search_path TO aquarco, public;
DROP INDEX IF EXISTS idx_stages_status;
DROP INDEX IF EXISTS idx_stages_task_id;
DROP TABLE IF EXISTS stages;

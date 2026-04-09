SET search_path TO aquarco, public;
DROP INDEX IF EXISTS idx_stages_task_stage_key_iteration_run;
CREATE UNIQUE INDEX IF NOT EXISTS idx_stages_task_stage_key_iteration
    ON stages(task_id, stage_key, iteration)
    WHERE stage_key IS NOT NULL;
ALTER TABLE stages DROP COLUMN IF EXISTS run;

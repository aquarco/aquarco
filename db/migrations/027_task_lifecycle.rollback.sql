SET search_path TO aquarco, public;
DROP INDEX IF EXISTS idx_tasks_parent;
ALTER TABLE tasks DROP COLUMN IF EXISTS branch_name;
ALTER TABLE tasks DROP COLUMN IF EXISTS pr_number;
ALTER TABLE tasks DROP COLUMN IF EXISTS parent_task_id;
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE tasks ADD CONSTRAINT valid_status CHECK (
    status IN ('pending', 'queued', 'executing', 'completed', 'failed', 'timeout', 'blocked', 'rate_limited')
);

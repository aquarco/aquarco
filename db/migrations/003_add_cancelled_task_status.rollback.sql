-- depends: 002_drop_pipeline_trigger_config
-- Rollback: Remove 'cancelled' from the tasks.status CHECK constraint

SET search_path TO aquarco, public;

-- First move any cancelled tasks to failed so rollback doesn't violate the constraint
UPDATE tasks SET status = 'failed', error_message = COALESCE(error_message, 'Task was cancelled')
WHERE status = 'cancelled';

ALTER TABLE tasks
    DROP CONSTRAINT IF EXISTS valid_status;

ALTER TABLE tasks
    ADD CONSTRAINT valid_status CHECK (
        status IN (
            'pending', 'queued', 'planning', 'executing',
            'completed', 'failed', 'timeout', 'blocked',
            'rate_limited', 'closed'
        )
    );

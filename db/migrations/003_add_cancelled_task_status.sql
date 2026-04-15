-- depends: 002_drop_pipeline_trigger_config
-- Migration: Add 'cancelled' to the tasks.status CHECK constraint

SET search_path TO aquarco, public;

-- apply
ALTER TABLE tasks
    DROP CONSTRAINT IF EXISTS valid_status;

ALTER TABLE tasks
    ADD CONSTRAINT valid_status CHECK (
        status IN (
            'pending', 'queued', 'planning', 'executing',
            'completed', 'failed', 'timeout', 'blocked',
            'rate_limited', 'closed', 'cancelled'
        )
    );

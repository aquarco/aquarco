-- Rollback: 036_add_max_turns_stage_status.sql
-- Revert 'max_turns' back to 'failed' before dropping the status value.

SET search_path TO aquarco, public;

UPDATE stages SET status = 'failed' WHERE status = 'max_turns';

ALTER TABLE stages DROP CONSTRAINT IF EXISTS valid_stage_status;
ALTER TABLE stages ADD CONSTRAINT valid_stage_status CHECK (
    status IN ('pending', 'executing', 'completed', 'failed', 'skipped', 'rate_limited')
);

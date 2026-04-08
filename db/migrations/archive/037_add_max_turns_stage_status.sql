-- depends: 036_supervisor_state
-- Migration: 037_add_max_turns_stage_status.sql
-- Purpose: Add 'max_turns' status to stages table.
--
-- When the Claude CLI hits --max-turns and all continuation attempts are
-- exhausted (cost exceeded, max resume iterations reached, no session_id),
-- the stage is marked 'max_turns' instead of the misleading 'completed'.

SET search_path TO aquarco, public;

ALTER TABLE stages DROP CONSTRAINT IF EXISTS valid_stage_status;
ALTER TABLE stages ADD CONSTRAINT valid_stage_status CHECK (
    status IN ('pending', 'executing', 'completed', 'failed', 'skipped', 'rate_limited', 'max_turns')
);

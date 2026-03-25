-- Migration: 017_add_rate_limited_status.sql
-- Purpose: Add 'rate_limited' status to tasks and stages.
--
-- When the Claude API returns 429, the task/stage is marked 'rate_limited'
-- instead of 'failed'. The supervisor picks these up again after a cooldown
-- (default 1 hour).
--
-- Depends on: 003_create_stages.sql, 002_create_tasks.sql

-- up

SET search_path TO aquarco, public;

-- Tasks: drop and recreate the CHECK constraint with rate_limited
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE tasks ADD CONSTRAINT valid_status CHECK (
    status IN ('pending', 'queued', 'executing', 'completed', 'failed', 'timeout', 'blocked', 'rate_limited')
);

-- Stages: drop and recreate the CHECK constraint with rate_limited
ALTER TABLE stages DROP CONSTRAINT IF EXISTS valid_stage_status;
ALTER TABLE stages ADD CONSTRAINT valid_stage_status CHECK (
    status IN ('pending', 'executing', 'completed', 'failed', 'skipped', 'rate_limited')
);

-- Counter for how many times a task has been rate-limited (max 24 = ~1 day of retries)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS rate_limit_count INTEGER NOT NULL DEFAULT 0;

-- down
-- ALTER TABLE tasks DROP CONSTRAINT valid_status;
-- ALTER TABLE tasks ADD CONSTRAINT valid_status CHECK (
--     status IN ('pending', 'queued', 'executing', 'completed', 'failed', 'timeout', 'blocked')
-- );
-- ALTER TABLE stages DROP CONSTRAINT valid_stage_status;
-- ALTER TABLE stages ADD CONSTRAINT valid_stage_status CHECK (
--     status IN ('pending', 'executing', 'completed', 'failed', 'skipped')
-- );

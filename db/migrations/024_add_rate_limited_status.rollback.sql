SET search_path TO aquarco, public;
ALTER TABLE tasks DROP COLUMN IF EXISTS rate_limit_count;
ALTER TABLE stages DROP CONSTRAINT IF EXISTS valid_stage_status;
ALTER TABLE stages ADD CONSTRAINT valid_stage_status CHECK (
    status IN ('pending', 'executing', 'completed', 'failed', 'skipped')
);
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE tasks ADD CONSTRAINT valid_status CHECK (
    status IN ('pending', 'queued', 'executing', 'completed', 'failed', 'timeout', 'blocked')
);

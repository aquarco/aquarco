-- depends: 040_drop_is_config_repo
-- Migration 040: Add model column to stages
--
-- Stores the Claude model identifier (e.g. 'claude-sonnet-4-6') used for
-- each pipeline stage execution. Extracted from raw_output NDJSON at
-- stage completion time. NULL for stages completed before this migration.

SET search_path TO aquarco, public;

ALTER TABLE stages
  ADD COLUMN IF NOT EXISTS model VARCHAR(100);

COMMENT ON COLUMN stages.model IS 'Claude model identifier (e.g. claude-sonnet-4-6) extracted from raw_output NDJSON. NULL for pre-migration stages not yet backfilled.';

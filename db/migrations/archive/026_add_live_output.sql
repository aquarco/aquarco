-- depends: 025_add_stage_run
-- Migration: 026_add_live_output.sql
-- Purpose: Add live_output column to stages for real-time debug log tail during execution.

SET search_path TO aquarco, public;

ALTER TABLE stages ADD COLUMN IF NOT EXISTS live_output TEXT;

COMMENT ON COLUMN stages.live_output IS 'Tail of the debug log, updated periodically during execution. Cleared on completion.';

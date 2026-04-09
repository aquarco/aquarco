-- depends: 032_add_stage_spending
-- Migration: 033_add_stage_session_id.sql
-- Purpose: Store Claude CLI session_id on stage rows so that rate-limited
--          or failed stages can resume the conversation on retry instead of
--          starting a fresh session.

SET search_path TO aquarco, public;

ALTER TABLE stages ADD COLUMN IF NOT EXISTS session_id TEXT;

COMMENT ON COLUMN stages.session_id IS 'Claude CLI session_id; allows resuming the conversation on retry.';

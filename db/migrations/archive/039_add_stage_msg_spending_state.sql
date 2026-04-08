-- depends: 038_drop_repo_agent_scans
-- Migration 039: Add msg_spending_state JSONB column to stages
--
-- Tracks per-message-id max token values for incremental spending
-- deduplication. The Claude CLI streaming protocol emits the same
-- message.id multiple times with cumulative (not delta) token counts.
-- This column stores the running max per field per message ID so that
-- update_stage_live_output can compute proper deltas atomically.
--
-- Format: {"msg_id": {"i": N, "o": N, "cr": N, "cw": N}, ...}

SET search_path TO aquarco, public;

ALTER TABLE stages
  ADD COLUMN IF NOT EXISTS msg_spending_state JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN stages.msg_spending_state IS 'Per-message-id max token values for incremental spending deduplication. Format: {"msg_id": {"i": input, "o": output, "cr": cache_read, "cw": cache_write}}.';

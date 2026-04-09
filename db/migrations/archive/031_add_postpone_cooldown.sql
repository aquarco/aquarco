-- depends: 030_add_agent_group
-- Migration 031: Add postpone_cooldown_minutes column to tasks
--
-- Stores the per-task cooldown duration (in minutes) used by postpone_task().
-- This enables different cooldown durations for different retryable errors:
--   - RateLimitError (429): 60 minutes
--   - ServerError (500):    30 minutes
--   - OverloadedError (529): 15 minutes
--
-- Default of 60 preserves backward compatibility with existing rate_limited rows.
SET search_path TO aquarco, public;

ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS postpone_cooldown_minutes INTEGER NOT NULL DEFAULT 60;

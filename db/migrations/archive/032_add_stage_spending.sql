-- depends: 031_add_postpone_cooldown
-- Migration 032: Add spending columns to stages
--
-- Adds cost_usd, cache_read_tokens, and cache_write_tokens columns.
-- tokens_input and tokens_output already exist (migration 003) but were never
-- populated. This migration adds the missing cache columns so all four
-- claude-spend-style buckets are available:
--   Input (tokens_input), Cache Writes (cache_write_tokens),
--   Cache Reads (cache_read_tokens), Output (tokens_output)
--
-- cost_usd stores the authoritative total_cost_usd from the Claude CLI result.

SET search_path TO aquarco, public;

ALTER TABLE stages
  ADD COLUMN IF NOT EXISTS cost_usd           NUMERIC,
  ADD COLUMN IF NOT EXISTS cache_read_tokens  INTEGER,
  ADD COLUMN IF NOT EXISTS cache_write_tokens INTEGER;

COMMENT ON COLUMN stages.cost_usd           IS 'Authoritative billing cost (USD) from Claude CLI total_cost_usd.';
COMMENT ON COLUMN stages.cache_read_tokens  IS 'Cache read input tokens (cache_read_input_tokens from Claude usage).';
COMMENT ON COLUMN stages.cache_write_tokens IS 'Cache write/creation tokens (cache_creation_input_tokens from Claude usage).';

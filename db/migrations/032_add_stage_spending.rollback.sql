-- Rollback 032: Remove spending columns from stages

SET search_path TO aquarco, public;

ALTER TABLE stages
  DROP COLUMN IF EXISTS cost_usd,
  DROP COLUMN IF EXISTS cache_read_tokens,
  DROP COLUMN IF EXISTS cache_write_tokens;

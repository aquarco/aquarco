-- depends: 016_add_is_config_repo
-- Migration: 017_allow_null_branch.sql
-- Purpose: Allow NULL branch on repositories so git clone uses the repo's
--          actual default branch instead of hardcoding 'main'.

SET search_path TO aquarco, public;

ALTER TABLE repositories ALTER COLUMN branch DROP NOT NULL;
ALTER TABLE repositories ALTER COLUMN branch DROP DEFAULT;



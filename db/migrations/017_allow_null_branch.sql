-- Migration: 017_allow_null_branch.sql
-- Purpose: Allow NULL branch on repositories so git clone uses the repo's
--          actual default branch instead of hardcoding 'main'.

-- up

SET search_path TO aquarco, public;

ALTER TABLE repositories ALTER COLUMN branch DROP NOT NULL;
ALTER TABLE repositories ALTER COLUMN branch DROP DEFAULT;

-- down

-- ALTER TABLE repositories ALTER COLUMN branch SET NOT NULL;
-- ALTER TABLE repositories ALTER COLUMN branch SET DEFAULT 'main';

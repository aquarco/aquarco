-- Migration: 011_add_repo_original_url.sql
-- Purpose: Preserve the user-supplied URL so retries can attempt HTTPS+token before SSH+deploy key.

-- up

SET search_path TO aquarco, public;

ALTER TABLE repositories
  ADD COLUMN IF NOT EXISTS original_url TEXT;

-- Backfill: set original_url = url for existing rows
UPDATE repositories SET original_url = url WHERE original_url IS NULL;

COMMENT ON COLUMN repositories.original_url IS 'User-supplied URL at registration time. May differ from url if rewritten to SSH on clone failure.';

-- down

-- ALTER TABLE repositories DROP COLUMN IF EXISTS original_url;

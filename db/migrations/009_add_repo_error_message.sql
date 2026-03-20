-- Migration: 009_add_repo_error_message.sql
-- Purpose: Store clone/pull error messages so the UI can display actionable guidance.

-- up

SET search_path TO aquarco, public;

ALTER TABLE repositories
  ADD COLUMN IF NOT EXISTS error_message TEXT;

COMMENT ON COLUMN repositories.error_message IS 'Last error message from clone or pull operation; NULL when clone_status is not error.';

-- down

-- ALTER TABLE repositories DROP COLUMN IF EXISTS error_message;

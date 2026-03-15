-- Migration: 010_add_repo_deploy_key.sql
-- Purpose: Store per-repo SSH deploy public key so the UI can display it.

-- up

SET search_path TO aifishtank, public;

ALTER TABLE repositories
  ADD COLUMN IF NOT EXISTS deploy_public_key TEXT;

COMMENT ON COLUMN repositories.deploy_public_key IS 'SSH public key (OpenSSH format) generated for this repository. Add as deploy key on GitHub.';

-- down

-- ALTER TABLE repositories DROP COLUMN IF EXISTS deploy_public_key;

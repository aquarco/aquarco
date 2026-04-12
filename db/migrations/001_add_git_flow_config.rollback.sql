-- Migration: Add git_flow_config JSONB column to repositories
-- Task: github-issue-aquarco-118
-- Description: Stores Git Flow configuration per repository.
--   NULL = Simple Branch mode (all existing behaviour preserved).
--   Non-null JSON = Git Flow mode with branch naming patterns.
--
-- Schema example:
--   {
--     "enabled": true,
--     "branches": {
--       "stable": "main",
--       "development": "develop",
--       "release": "release/*",
--       "feature": "feature/*",
--       "bugfix": "bugfix/*",
--       "hotfix": "hotfix/*"
--     }
--   }

SET search_path TO aquarco, public;

-- rollback
ALTER TABLE repositories DROP COLUMN IF EXISTS git_flow_config;

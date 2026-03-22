-- Migration: 018_rename_schema.sql
-- Purpose: Rename schema from 'aifishtank' to 'aquarco' for existing deployed databases.
--
-- This migration handles the rebrand from ai-fishtank → aquarco.
-- Fresh installations created after the rebrand will already have an 'aquarco'
-- schema (created by 000_init.sql) and can skip this migration safely — the
-- DO $$ block is a no-op when 'aifishtank' does not exist.
--
-- For environments that were set up before the rebrand and still have the
-- 'aifishtank' schema, this migration renames it to 'aquarco' so that the
-- application's SET search_path TO aquarco continues to work after the upgrade.
--
-- Run this as a superuser or a role that owns the 'aifishtank' schema.

-- up

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.schemata WHERE schema_name = 'aifishtank'
  ) THEN
    ALTER SCHEMA aifishtank RENAME TO aquarco;
    RAISE NOTICE 'Schema renamed from aifishtank to aquarco.';
  ELSE
    RAISE NOTICE 'Schema aifishtank not found — skipping rename (already aquarco or fresh install).';
  END IF;
END
$$;

SET search_path TO aquarco, public;

-- down

-- To reverse: rename the schema back to aifishtank.
-- Only run after explicit confirmation — all application connections must be
-- updated to use search_path = aifishtank before running the down migration.
--
-- DO $$
-- BEGIN
--   IF EXISTS (
--     SELECT 1 FROM information_schema.schemata WHERE schema_name = 'aquarco'
--   ) THEN
--     ALTER SCHEMA aquarco RENAME TO aifishtank;
--   END IF;
-- END
-- $$;

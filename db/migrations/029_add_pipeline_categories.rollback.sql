-- Rollback: remove categories column from pipeline_definitions
SET search_path TO aquarco, public;
ALTER TABLE pipeline_definitions
    DROP COLUMN IF EXISTS categories;

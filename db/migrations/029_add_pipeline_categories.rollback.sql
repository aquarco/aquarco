-- Rollback: remove categories column from pipeline_definitions
ALTER TABLE pipeline_definitions
    DROP COLUMN IF EXISTS categories;

SET search_path TO aquarco, public;

-- Drop validation_items table
DROP TABLE IF EXISTS validation_items;

-- Remove new stage indexes
DROP INDEX IF EXISTS idx_stages_stage_key;
DROP INDEX IF EXISTS idx_stages_task_stage_key_iteration;

-- Remove new stage columns
ALTER TABLE stages DROP COLUMN IF EXISTS validation_items_out;
ALTER TABLE stages DROP COLUMN IF EXISTS validation_items_in;
ALTER TABLE stages DROP COLUMN IF EXISTS stage_key;
ALTER TABLE stages DROP COLUMN IF EXISTS iteration;
ALTER TABLE stages DROP COLUMN IF EXISTS input;

-- Recreate original unique constraint
ALTER TABLE stages ADD CONSTRAINT stages_task_id_stage_number_key UNIQUE (task_id, stage_number);

-- Remove task columns and constraints
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_phase;
ALTER TABLE tasks DROP COLUMN IF EXISTS planned_stages;
ALTER TABLE tasks DROP COLUMN IF EXISTS phase;

-- Restore original valid_category (without 'planning')
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_category;
ALTER TABLE tasks ADD CONSTRAINT valid_category CHECK (
    category IN ('review', 'implementation', 'test', 'design', 'docs', 'analyze')
);

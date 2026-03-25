-- depends: 026_add_live_output
-- Migration 027: Task lifecycle (RETRY, RERUN, CLOSE)
-- Add 'closed' to task status, add parent_task_id, pr_number, branch_name columns

SET search_path TO aquarco, public;

-- Extend status CHECK constraint to include 'closed'
-- Migration 017 created the constraint as 'valid_status', so drop that name.
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS valid_status;
ALTER TABLE tasks ADD CONSTRAINT valid_status
  CHECK (status IN ('pending','queued','executing','completed','failed','timeout','blocked','rate_limited','closed'));

-- Add lifecycle columns
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parent_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS pr_number INTEGER;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS branch_name TEXT;

-- Index for finding child tasks
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;

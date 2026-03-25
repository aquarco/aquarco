-- Migration 021: Task lifecycle (RETRY, RERUN, CLOSE)
-- Add 'closed' to task status, add parent_task_id, pr_number, branch_name columns

-- Extend status CHECK constraint to include 'closed'
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_status_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
  CHECK (status IN ('pending','queued','executing','completed','failed','timeout','blocked','rate_limited','closed'));

-- Add lifecycle columns
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parent_task_id TEXT REFERENCES tasks(id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS pr_number INTEGER;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS branch_name TEXT;

-- Index for finding child tasks
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;

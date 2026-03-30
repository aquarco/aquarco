-- Rollback 031: Remove postpone_cooldown_minutes column from tasks
SET search_path TO aquarco, public;

ALTER TABLE tasks
  DROP COLUMN IF EXISTS postpone_cooldown_minutes;

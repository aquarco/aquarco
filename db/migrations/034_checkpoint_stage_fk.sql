-- depends: 033_add_stage_session_id
-- Migration: 034_checkpoint_stage_fk.sql
-- Purpose: Change pipeline_checkpoints.last_completed_stage from a stage index
--          (INTEGER) to a foreign key referencing stages(id).
--
--          stage_number is not unique for a task — it is reused by
--          condition-eval system stages and go-to-stage jumps.  Referencing
--          the surrogate PK (stages.id) removes ambiguity and lets the
--          executor resume from the exact stage row that last completed.
--
-- Depends on: 033_add_stage_session_id.sql

SET search_path TO aquarco, public;

-- 1. Drop existing data — checkpoints are transient resume state;
--    any in-flight task will simply re-plan on next pickup.
DELETE FROM pipeline_checkpoints;

-- 2. Change column type from INTEGER (index) to BIGINT (stages.id FK).
ALTER TABLE pipeline_checkpoints
    ALTER COLUMN last_completed_stage SET DATA TYPE BIGINT;

ALTER TABLE pipeline_checkpoints
    ADD CONSTRAINT fk_checkpoint_last_stage
        FOREIGN KEY (last_completed_stage) REFERENCES stages(id) ON DELETE CASCADE;

-- 3. Update comments.
COMMENT ON COLUMN pipeline_checkpoints.last_completed_stage IS
    'Foreign key to stages.id — the last stage row that completed successfully.';

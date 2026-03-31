-- depends: 034_checkpoint_stage_fk

SET search_path TO aquarco, public;

DELETE FROM pipeline_checkpoints;

ALTER TABLE pipeline_checkpoints
    DROP CONSTRAINT IF EXISTS fk_checkpoint_last_stage;

ALTER TABLE pipeline_checkpoints
    ALTER COLUMN last_completed_stage SET DATA TYPE INTEGER;

COMMENT ON COLUMN pipeline_checkpoints.last_completed_stage IS
    '0-based index of the last successfully completed stage.';

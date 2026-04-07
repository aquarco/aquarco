-- depends: 040_add_stage_model
-- Rollback for 041_backfill_stage_model
--
-- The backfill is a data migration with no schema changes.
-- We cannot reliably distinguish backfilled values from values written
-- by the supervisor, so we leave model values in place on rollback.

SET search_path TO aquarco, public;

-- no-op: backfilled data is retained

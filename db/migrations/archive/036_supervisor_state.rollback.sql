-- 036: Rollback supervisor_state table
-- depends: 035_simplify_tasks

SET search_path TO aquarco, public;

DROP TABLE IF EXISTS supervisor_state;

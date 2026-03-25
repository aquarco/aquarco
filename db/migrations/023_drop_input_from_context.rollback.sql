SET search_path TO aquarco, public;
-- Rollback restores get_task_context() to the version from 015_remove_task_category
-- which includes s.input in the stages query.
-- The actual function body is too large to safely reverse here;
-- re-run migration 015 to restore the previous version.
-- No-op: the function change is forward-only.

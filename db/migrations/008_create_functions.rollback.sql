SET search_path TO aquarco, public;
DROP TRIGGER IF EXISTS trg_tasks_updated_at ON tasks;
DROP FUNCTION IF EXISTS get_task_context(TEXT);
DROP FUNCTION IF EXISTS update_updated_at();

-- depends: 003_add_cancelled_task_status
-- Migration: Add supervisor_health table for tracking supervisor status (e.g. Claude auth)

SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS supervisor_health (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT 'ok',
    message TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

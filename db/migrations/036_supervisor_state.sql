-- 036: Create supervisor_state key/value table for drain mode
-- depends: 035_simplify_tasks

CREATE TABLE IF NOT EXISTS supervisor_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT 'false',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed the drain_mode flag
INSERT INTO supervisor_state (key, value)
VALUES ('drain_mode', 'false')
ON CONFLICT (key) DO NOTHING;

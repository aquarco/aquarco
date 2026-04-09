-- depends: 027_task_lifecycle
-- Migration 028: Add repo_agent_scans table for autoloading .claude/agents
-- Tracks the status and results of agent scanning operations per repository.
SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS repo_agent_scans (
    id          SERIAL PRIMARY KEY,
    repo_name   TEXT NOT NULL REFERENCES repositories(name) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'scanning', 'analyzing', 'writing', 'completed', 'failed')),
    agents_found    INT NOT NULL DEFAULT 0,
    agents_created  INT NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_repo_agent_scans_repo
    ON repo_agent_scans (repo_name, created_at DESC);

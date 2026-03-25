-- depends: 000_init
-- Migration: 001_create_repositories.sql
-- Purpose: Registered target repositories that the supervisor monitors.
--
-- Each row represents one Git repository the system is configured to watch.
-- Pollers reference repositories by name (the primary key).
-- Tasks reference repositories via a foreign key to enforce referential integrity.

SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS repositories (
    -- Human-readable short name, used as FK target from tasks.
    -- Example: 'aquarco', 'my-app'
    name          TEXT        PRIMARY KEY,

    -- Full clone URL (SSH or HTTPS).
    url           TEXT        NOT NULL,

    -- Default branch to track.
    branch        TEXT        NOT NULL DEFAULT 'main',

    -- Absolute path on the host / VM where the repo is cloned.
    clone_dir     TEXT        NOT NULL,

    -- Names of pollers that watch this repository.
    -- Example: '{github-issues-poller, github-pr-poller}'
    pollers       TEXT[]      NOT NULL DEFAULT '{}',

    -- Timestamps for clone/pull lifecycle.
    last_cloned_at    TIMESTAMPTZ,
    last_pulled_at    TIMESTAMPTZ,

    -- Current state of the local clone.
    clone_status  TEXT        NOT NULL DEFAULT 'pending',

    -- HEAD SHA at the time of the last pull.
    head_sha      TEXT,

    CONSTRAINT valid_clone_status CHECK (
        clone_status IN ('pending', 'cloning', 'ready', 'error')
    )
);

COMMENT ON TABLE  repositories                  IS 'Registered target repositories monitored by the supervisor.';
COMMENT ON COLUMN repositories.name             IS 'Short unique identifier; used as FK in tasks.';
COMMENT ON COLUMN repositories.url              IS 'Git remote URL (SSH or HTTPS).';
COMMENT ON COLUMN repositories.clone_dir        IS 'Absolute filesystem path of the local clone.';
COMMENT ON COLUMN repositories.pollers          IS 'Array of poller names that watch this repository.';
COMMENT ON COLUMN repositories.clone_status     IS 'Lifecycle state of the local clone: pending → cloning → ready | error.';
COMMENT ON COLUMN repositories.head_sha         IS 'Full SHA of HEAD after the most recent pull.';



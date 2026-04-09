-- depends: 004_create_context
-- Migration: 005_create_poll_state.sql
-- Purpose: Poller cursor and state tracking.
--
-- Each polling loop (GitHub issues, GitHub PRs, external webhook queue, etc.)
-- maintains a single row here. The supervisor reads the cursor before each
-- poll cycle and writes it back on success, enabling resume-after-restart
-- without re-processing already-seen events.
--
-- state_data holds poller-specific metadata (rate-limit headers, pagination
-- tokens, backoff counters) that does not fit into the generic cursor column.

SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS poll_state (
    -- Unique name of the poller; matches the name used in repositories.pollers[].
    -- Example: 'github-issues-poller', 'github-pr-poller'
    poller_name             TEXT        PRIMARY KEY,

    -- Timestamp of the last poll attempt (successful or not).
    last_poll_at            TIMESTAMPTZ,

    -- Timestamp of the last poll that completed without error.
    last_successful_at      TIMESTAMPTZ,

    -- Opaque cursor value understood by the poller implementation.
    -- For GitHub pollers this is typically the since= ISO timestamp or
    -- the last seen event ID.
    cursor                  TEXT,

    -- Poller-specific state blob (rate-limit windows, backoff state, etc.).
    state_data              JSONB
);

COMMENT ON TABLE  poll_state                    IS 'Per-poller cursor and metadata to support resume-after-restart.';
COMMENT ON COLUMN poll_state.poller_name        IS 'Must match an entry in repositories.pollers[].';
COMMENT ON COLUMN poll_state.cursor             IS 'Opaque poller-specific resume cursor (timestamp, event ID, etc.).';
COMMENT ON COLUMN poll_state.state_data         IS 'Arbitrary poller state: rate-limit windows, backoff counters, pagination tokens.';

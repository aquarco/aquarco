-- depends: 005_create_poll_state
-- Migration: 006_create_agent_instances.sql
-- Purpose: Active agent execution tracking and aggregate metrics.
--
-- One row per agent definition (keyed by agent name). The supervisor
-- increments active_count when spawning an agent and decrements it on
-- completion, enforcing the maxConcurrent limit defined in the agent's
-- YAML definition. Cumulative metrics are updated on each execution
-- completion for the observability dashboard.
--
-- This table intentionally has no created_at / updated_at because it
-- models a running counter, not a historical event.

SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS agent_instances (
    -- Matches metadata.name in the agent definition YAML.
    -- Example: 'review-agent', 'implementation-agent'
    agent_name              TEXT        PRIMARY KEY,

    -- Number of currently executing instances of this agent.
    -- The supervisor must not exceed the maxConcurrent defined in the YAML.
    active_count            INTEGER     NOT NULL DEFAULT 0,

    -- Lifetime total executions completed (all statuses).
    total_executions        INTEGER     NOT NULL DEFAULT 0,

    -- Cumulative token usage across all executions (Claude Code Max sessions).
    total_tokens_used       BIGINT      NOT NULL DEFAULT 0,

    -- Timestamp of the most recent execution start.
    last_execution_at       TIMESTAMPTZ,

    CONSTRAINT active_count_non_negative CHECK (active_count >= 0),
    CONSTRAINT total_executions_non_negative CHECK (total_executions >= 0),
    CONSTRAINT total_tokens_non_negative CHECK (total_tokens_used >= 0)
);

COMMENT ON TABLE  agent_instances                   IS 'Per-agent concurrency counters and lifetime metrics.';
COMMENT ON COLUMN agent_instances.agent_name        IS 'Must match metadata.name in the agent definition YAML.';
COMMENT ON COLUMN agent_instances.active_count      IS 'Currently running instances; supervisor enforces maxConcurrent against this.';
COMMENT ON COLUMN agent_instances.total_tokens_used IS 'Cumulative token usage for cost-awareness dashboards (Claude Code Max sessions).';

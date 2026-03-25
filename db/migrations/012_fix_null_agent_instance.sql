-- depends: 011_add_repo_original_url
-- Migration: 012_fix_null_agent_instance.sql
-- Purpose: Remove bogus 'null' row from agent_instances and seed correct agent names.
-- Root cause: agent-registry.sh used wrong jq paths (.metadata.name instead of .name),
-- causing the literal string 'null' to be inserted as an agent name.

SET search_path TO aquarco, public;

DELETE FROM agent_instances WHERE agent_name = 'null';

INSERT INTO agent_instances (agent_name, active_count, total_executions, total_tokens_used)
VALUES
    ('review-agent',         0, 0, 0),
    ('implementation-agent', 0, 0, 0),
    ('test-agent',           0, 0, 0),
    ('design-agent',         0, 0, 0),
    ('docs-agent',           0, 0, 0),
    ('analyze-agent',        0, 0, 0)
ON CONFLICT (agent_name) DO NOTHING;

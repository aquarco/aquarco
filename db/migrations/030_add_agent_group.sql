-- Migration 030: Add agent_group column to agent_definitions
--
-- Distinguishes system agents (orchestration/infrastructure) from
-- pipeline agents (stage execution). Default is 'pipeline' for
-- backward compatibility with existing rows.

ALTER TABLE agent_definitions
  ADD COLUMN IF NOT EXISTS agent_group TEXT NOT NULL DEFAULT 'pipeline'
    CONSTRAINT chk_agent_group CHECK (agent_group IN ('system', 'pipeline'));

-- Tag known system agents that are stored with source='default'
UPDATE agent_definitions
  SET agent_group = 'system'
  WHERE name IN ('planner-agent', 'condition-evaluator-agent', 'repo-descriptor-agent');

CREATE INDEX IF NOT EXISTS idx_agent_definitions_group
  ON agent_definitions (agent_group);

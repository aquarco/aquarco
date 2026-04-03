"""Shared constants used across multiple supervisor modules."""

from __future__ import annotations

# Known system agent names — used to infer group when scanning flat directories.
# System agents orchestrate pipeline execution; they are never selected for
# pipeline stage assignment.
#
# MAINTENANCE: This set MUST be updated whenever a new system agent YAML is
# added to config/agents/definitions/system/.  If a new system agent name is
# not listed here, the backward-compat flat-scan path in
# sync_all_agent_definitions_to_db() will silently tag it as 'pipeline'.
SYSTEM_AGENT_NAMES: frozenset[str] = frozenset({
    "planner-agent",
    "condition-evaluator-agent",
})

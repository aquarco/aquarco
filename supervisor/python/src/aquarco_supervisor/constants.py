"""Shared constants used across multiple supervisor modules."""

from __future__ import annotations

# Known system agent names — used to infer group when scanning flat directories.
# System agents orchestrate pipeline execution; they are never selected for
# pipeline stage assignment.
SYSTEM_AGENT_NAMES: frozenset[str] = frozenset({
    "planner-agent",
    "condition-evaluator-agent",
    "repo-descriptor-agent",
})

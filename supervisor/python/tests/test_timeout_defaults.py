"""Tests for updated timeout defaults (CLI 3600s, agent registry 60 min)."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.cli.claude import execute_claude
from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry


def test_execute_claude_default_timeout_is_3600() -> None:
    """execute_claude default timeout_seconds should be 3600 (was 1800)."""
    sig = inspect.signature(execute_claude)
    default = sig.parameters["timeout_seconds"].default
    assert default == 3600, f"Expected default timeout 3600s, got {default}"


def test_agent_registry_default_timeout_is_60_minutes() -> None:
    """get_agent_timeout returns 60 minutes when agent has no explicit timeout."""
    db = AsyncMock(spec=Database)
    registry = AgentRegistry(db, "/tmp/fake-agents")
    # Empty agents dict means no agent spec found
    registry._agents = {"test-agent": {}}

    timeout = registry.get_agent_timeout("test-agent")
    assert timeout == 60, f"Expected default timeout 60 min, got {timeout}"


def test_agent_registry_explicit_timeout_overrides_default() -> None:
    """get_agent_timeout respects an explicit timeoutMinutes in the agent spec."""
    db = AsyncMock(spec=Database)
    registry = AgentRegistry(db, "/tmp/fake-agents")
    registry._agents = {
        "custom-agent": {"resources": {"timeoutMinutes": 45}},
    }

    timeout = registry.get_agent_timeout("custom-agent")
    assert timeout == 45


def test_agent_registry_unknown_agent_returns_default() -> None:
    """get_agent_timeout returns 60 for unknown agents (empty dict fallback)."""
    db = AsyncMock(spec=Database)
    registry = AgentRegistry(db, "/tmp/fake-agents")
    registry._agents = {}

    timeout = registry.get_agent_timeout("nonexistent-agent")
    assert timeout == 60

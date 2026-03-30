"""Tests for agent venv isolation and environment merging in AgentRegistry.

Covers:
- _AGENT_DEFAULT_ENV with dedicated virtualenv PATH
- get_agent_environment merges defaults with per-agent overrides
- Per-agent env overrides default PATH
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    db.fetch_all = AsyncMock(return_value=[])
    return db


# ---------------------------------------------------------------------------
# _AGENT_DEFAULT_ENV / venv isolation
# ---------------------------------------------------------------------------


def test_agent_default_env_has_virtual_env() -> None:
    """Default agent env includes VIRTUAL_ENV pointing to agent venv."""
    assert "VIRTUAL_ENV" in AgentRegistry._AGENT_DEFAULT_ENV
    assert AgentRegistry._AGENT_DEFAULT_ENV["VIRTUAL_ENV"] == "/home/agent/.agent-venv"


def test_agent_default_env_path_starts_with_venv_bin() -> None:
    """PATH in default env prioritises the agent venv bin directory."""
    path = AgentRegistry._AGENT_DEFAULT_ENV["PATH"]
    assert path.startswith("/home/agent/.agent-venv/bin")


def test_agent_default_env_path_includes_system_bins() -> None:
    """PATH includes standard system bin directories."""
    path = AgentRegistry._AGENT_DEFAULT_ENV["PATH"]
    assert "/usr/local/bin" in path
    assert "/usr/bin" in path
    assert "/bin" in path


# ---------------------------------------------------------------------------
# get_agent_environment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_environment_returns_defaults_for_unknown_agent(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Unknown agent gets the default venv environment."""
    registry = AgentRegistry(mock_db, str(tmp_path / "agents"), str(tmp_path / "prompts"))

    env = registry.get_agent_environment("nonexistent-agent")
    assert env["VIRTUAL_ENV"] == "/home/agent/.agent-venv"
    assert env["PATH"].startswith("/home/agent/.agent-venv/bin")


@pytest.mark.asyncio
async def test_get_agent_environment_merges_per_agent_env(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Per-agent environment is merged on top of defaults."""
    registry = AgentRegistry(mock_db, str(tmp_path / "agents"), str(tmp_path / "prompts"))
    registry._agents = {
        "test-agent": {
            "name": "test-agent",
            "environment": {"CUSTOM_VAR": "custom_value", "STRICT_MODE": "true"},
        }
    }

    env = registry.get_agent_environment("test-agent")
    # Defaults should still be present
    assert env["VIRTUAL_ENV"] == "/home/agent/.agent-venv"
    # Custom vars should be merged in
    assert env["CUSTOM_VAR"] == "custom_value"
    assert env["STRICT_MODE"] == "true"


@pytest.mark.asyncio
async def test_get_agent_environment_per_agent_overrides_default(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Per-agent env can override default values like PATH."""
    custom_path = "/custom/bin:/usr/bin"
    registry = AgentRegistry(mock_db, str(tmp_path / "agents"), str(tmp_path / "prompts"))
    registry._agents = {
        "custom-agent": {
            "name": "custom-agent",
            "environment": {"PATH": custom_path},
        }
    }

    env = registry.get_agent_environment("custom-agent")
    assert env["PATH"] == custom_path
    # VIRTUAL_ENV should still be present from defaults
    assert env["VIRTUAL_ENV"] == "/home/agent/.agent-venv"


@pytest.mark.asyncio
async def test_get_agent_environment_empty_agent_env(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Agent with empty environment dict gets just defaults."""
    registry = AgentRegistry(mock_db, str(tmp_path / "agents"), str(tmp_path / "prompts"))
    registry._agents = {
        "bare-agent": {
            "name": "bare-agent",
            "environment": {},
        }
    }

    env = registry.get_agent_environment("bare-agent")
    assert env == AgentRegistry._AGENT_DEFAULT_ENV


@pytest.mark.asyncio
async def test_get_agent_environment_no_environment_key(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Agent without environment key in spec gets just defaults."""
    registry = AgentRegistry(mock_db, str(tmp_path / "agents"), str(tmp_path / "prompts"))
    registry._agents = {
        "minimal-agent": {
            "name": "minimal-agent",
        }
    }

    env = registry.get_agent_environment("minimal-agent")
    assert env == AgentRegistry._AGENT_DEFAULT_ENV

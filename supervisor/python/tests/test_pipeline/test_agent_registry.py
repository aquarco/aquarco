"""Tests for agent registry."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from aifishtank_supervisor.database import Database
from aifishtank_supervisor.exceptions import AgentRegistryError, NoAvailableAgentError
from aifishtank_supervisor.pipeline.agent_registry import AgentRegistry


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    return db


@pytest.fixture
def registry_file(tmp_path: Path) -> Path:
    agents = {
        "agents": [
            {
                "name": "analyzer",
                "categories": ["analyze"],
                "priority": 10,
                "promptFile": "analyzer.md",
                "resources": {"maxConcurrent": 2, "timeoutMinutes": 45},
                "allowedTools": ["Read", "Grep"],
                "deniedTools": ["Bash"],
            },
            {
                "name": "implementer",
                "categories": ["implementation"],
                "priority": 20,
                "resources": {"maxConcurrent": 1},
            },
            {
                "name": "reviewer",
                "categories": ["review", "analyze"],
                "priority": 30,
            },
        ]
    }
    path = tmp_path / "agent-registry.json"
    path.write_text(json.dumps(agents))
    return path


@pytest.mark.asyncio
async def test_load_from_file(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))

    agents = reg.get_agents_for_category("analyze")
    assert "analyzer" in agents
    assert "reviewer" in agents


@pytest.mark.asyncio
async def test_agents_for_category_sorted(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))

    agents = reg.get_agents_for_category("analyze")
    assert agents == ["analyzer", "reviewer"]


@pytest.mark.asyncio
async def test_agents_for_unknown_category(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))

    agents = reg.get_agents_for_category("nonexistent")
    assert agents == []


@pytest.mark.asyncio
async def test_select_agent_available(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    mock_db.fetch_val.return_value = 0

    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))

    agent = await reg.select_agent("analyze")
    assert agent == "analyzer"


@pytest.mark.asyncio
async def test_select_agent_none_available(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    mock_db.fetch_val.return_value = 100

    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))

    with pytest.raises(NoAvailableAgentError):
        await reg.select_agent("analyze")


@pytest.mark.asyncio
async def test_agent_is_available(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))

    mock_db.fetch_val.return_value = 0
    assert await reg.agent_is_available("analyzer") is True

    mock_db.fetch_val.return_value = 2
    assert await reg.agent_is_available("analyzer") is False

    mock_db.fetch_val.return_value = 1
    assert await reg.agent_is_available("analyzer") is True


def test_get_agent_timeout(tmp_path: Path) -> None:
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {
        "fast": {"resources": {"timeoutMinutes": 10}},
        "default": {},
    }
    assert reg.get_agent_timeout("fast") == 10
    assert reg.get_agent_timeout("default") == 30
    assert reg.get_agent_timeout("unknown") == 30


def test_get_agent_prompt_file(tmp_path: Path) -> None:
    mock_db = AsyncMock(spec=Database)
    prompts_dir = tmp_path / "prompts"
    reg = AgentRegistry(mock_db, str(tmp_path), str(prompts_dir))
    reg._agents = {
        "custom": {"promptFile": "custom-prompt.md"},
        "default": {},
    }
    assert reg.get_agent_prompt_file("custom") == prompts_dir / "custom-prompt.md"
    assert reg.get_agent_prompt_file("default") == prompts_dir / "default.md"


def test_get_allowed_denied_tools(tmp_path: Path) -> None:
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {
        "agent1": {"allowedTools": ["Read"], "deniedTools": ["Bash"]},
        "agent2": {},
    }
    assert reg.get_allowed_tools("agent1") == ["Read"]
    assert reg.get_denied_tools("agent1") == ["Bash"]
    assert reg.get_allowed_tools("agent2") == []
    assert reg.get_denied_tools("agent2") == []


@pytest.mark.asyncio
async def test_increment_decrement(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))

    await reg.increment_agent_instances("analyzer")
    call_sql = mock_db.execute.call_args[0][0]
    assert "active_count + 1" in call_sql

    await reg.decrement_agent_instances("analyzer")
    call_sql = mock_db.execute.call_args[0][0]
    assert "GREATEST(active_count - 1, 0)" in call_sql


@pytest.mark.asyncio
async def test_select_agent_empty_registry(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Empty registry gives a clear error message."""
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    # Don't load any agents
    with pytest.raises(NoAvailableAgentError, match="registry is empty"):
        await reg.select_agent("analyze")


@pytest.mark.asyncio
async def test_select_agent_no_category_match(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """No agents for requested category gives a clear error."""
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))
    with pytest.raises(NoAvailableAgentError, match="No agents registered"):
        await reg.select_agent("nonexistent-category")


@pytest.mark.asyncio
async def test_select_agent_at_capacity(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """All agents at capacity gives a clear error."""
    mock_db.fetch_val.return_value = 9999  # way over capacity
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(registry_file))
    with pytest.raises(NoAvailableAgentError, match="at capacity"):
        await reg.select_agent("analyze")


@pytest.mark.asyncio
async def test_discover_agents_from_yaml(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """YAML agent discovery loads AgentDefinition files."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    defn = {
        "kind": "AgentDefinition",
        "metadata": {"name": "yaml-agent"},
        "spec": {"categories": ["test"], "priority": 5},
    }
    (agents_dir / "yaml-agent.yaml").write_text(yaml.dump(defn))
    # Non-agent YAML should be skipped
    (agents_dir / "config.yaml").write_text(yaml.dump({"kind": "Other"}))

    reg = AgentRegistry(mock_db, str(agents_dir), str(tmp_path / "prompts"))
    # No registry file → triggers discovery
    await reg.load(str(tmp_path / "nonexistent.json"))

    agents = reg.get_agents_for_category("test")
    assert "yaml-agent" in agents


@pytest.mark.asyncio
async def test_discover_agents_missing_dir(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Discovery with missing agents dir logs warning and loads empty."""
    reg = AgentRegistry(
        mock_db, str(tmp_path / "no-such-dir"), str(tmp_path / "prompts")
    )
    await reg.load(str(tmp_path / "nonexistent.json"))
    assert reg._agents == {}


@pytest.mark.asyncio
async def test_discover_agents_bad_yaml(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Invalid YAML files are skipped without crashing."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "bad.yaml").write_text("{{invalid yaml::")

    reg = AgentRegistry(mock_db, str(agents_dir), str(tmp_path / "prompts"))
    await reg.load(str(tmp_path / "nonexistent.json"))
    assert reg._agents == {}


@pytest.mark.asyncio
async def test_load_dict_format_registry(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Registry JSON with dict format (not list) is loaded correctly."""
    data = {
        "agent-a": {"name": "agent-a", "categories": ["cat1"], "priority": 1},
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(data))

    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    await reg.load(str(path))
    assert "agent-a" in reg._agents


@pytest.mark.asyncio
async def test_load_invalid_json_raises(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Invalid JSON in registry file raises AgentRegistryError."""
    path = tmp_path / "bad.json"
    path.write_text("{bad json")

    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    with pytest.raises(AgentRegistryError, match="Failed to parse"):
        await reg.load(str(path))


@pytest.mark.asyncio
async def test_load_default_path_no_file(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Loading without registry_file and no default file triggers discovery."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    # No schemas/agent-registry.json exists, no YAML files either
    reg = AgentRegistry(mock_db, str(agents_dir), str(tmp_path / "prompts"))
    await reg.load()  # No argument — uses default path
    assert reg._agents == {}

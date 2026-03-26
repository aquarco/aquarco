"""Tests for agent registry."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import AgentRegistryError, NoAvailableAgentError
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry


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
        "agent1": {"tools": {"allowed": ["Read"], "denied": ["Bash"]}},
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


def test_get_agent_output_schema_returns_schema(tmp_path: Path) -> None:
    """Returns the outputSchema dict when present."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    reg._agents = {"agent1": {"outputSchema": schema}}
    assert reg.get_agent_output_schema("agent1") == schema


def test_get_agent_output_schema_returns_none_when_missing(tmp_path: Path) -> None:
    """Returns None when no outputSchema is defined."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {"agent1": {}}
    assert reg.get_agent_output_schema("agent1") is None


def test_get_agent_output_schema_returns_none_for_unknown(tmp_path: Path) -> None:
    """Returns None for an agent not in the registry."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {}
    assert reg.get_agent_output_schema("nonexistent") is None


def test_get_agent_output_schema_returns_none_for_empty_schema(tmp_path: Path) -> None:
    """Returns None when outputSchema is an empty dict."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {"agent1": {"outputSchema": {}}}
    assert reg.get_agent_output_schema("agent1") is None


# ---------------------------------------------------------------------------
# Group-based filtering (system vs pipeline)
# ---------------------------------------------------------------------------


def test_get_agents_for_category_excludes_system_agents(tmp_path: Path) -> None:
    """System agents must never appear in get_agents_for_category results."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {
        "planner-agent": {"_group": "system", "categories": [], "priority": 1},
        "condition-evaluator-agent": {"_group": "system", "categories": [], "priority": 1},
        "analyze-agent": {"_group": "pipeline", "categories": ["analyze"], "priority": 10},
    }
    result = reg.get_agents_for_category("analyze")
    assert result == ["analyze-agent"]
    assert "planner-agent" not in result
    assert "condition-evaluator-agent" not in result


def test_get_agent_group_returns_system(tmp_path: Path) -> None:
    """get_agent_group returns 'system' for system agents."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {
        "planner-agent": {"_group": "system"},
        "analyze-agent": {"_group": "pipeline"},
    }
    assert reg.get_agent_group("planner-agent") == "system"
    assert reg.get_agent_group("analyze-agent") == "pipeline"


def test_get_agent_group_returns_pipeline_for_unknown(tmp_path: Path) -> None:
    """get_agent_group defaults to 'pipeline' for unknown agents."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {}
    assert reg.get_agent_group("nonexistent") == "pipeline"


def test_get_system_agent_by_role_returns_name(tmp_path: Path) -> None:
    """get_system_agent_by_role returns the agent name with the matching role."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {
        "planner-agent": {"_group": "system", "role": "planner"},
        "condition-evaluator-agent": {"_group": "system", "role": "condition-evaluator"},
        "analyze-agent": {"_group": "pipeline", "categories": ["analyze"]},
    }
    assert reg.get_system_agent_by_role("planner") == "planner-agent"
    assert reg.get_system_agent_by_role("condition-evaluator") == "condition-evaluator-agent"


def test_get_system_agent_by_role_returns_none_for_unknown(tmp_path: Path) -> None:
    """get_system_agent_by_role returns None when no match."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {
        "planner-agent": {"_group": "system", "role": "planner"},
    }
    assert reg.get_system_agent_by_role("nonexistent-role") is None


@pytest.mark.asyncio
async def test_discover_agents_from_system_and_pipeline_subdirs(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Discovery from system/ and pipeline/ subdirs tags agents with the right group."""
    agents_dir = tmp_path / "definitions"
    system_dir = agents_dir / "system"
    pipeline_dir = agents_dir / "pipeline"
    system_dir.mkdir(parents=True)
    pipeline_dir.mkdir(parents=True)

    system_defn = {
        "kind": "AgentDefinition",
        "metadata": {"name": "planner-agent"},
        "spec": {"role": "planner", "promptFile": "planner-agent.md"},
    }
    pipeline_defn = {
        "kind": "AgentDefinition",
        "metadata": {"name": "analyze-agent"},
        "spec": {"categories": ["analyze"], "priority": 10, "promptFile": "analyze-agent.md"},
    }
    (system_dir / "planner-agent.yaml").write_text(yaml.dump(system_defn))
    (pipeline_dir / "analyze-agent.yaml").write_text(yaml.dump(pipeline_defn))

    reg = AgentRegistry(mock_db, str(agents_dir), str(tmp_path / "prompts"))
    await reg.load(str(tmp_path / "nonexistent.json"))

    assert reg.get_agent_group("planner-agent") == "system"
    assert reg.get_agent_group("analyze-agent") == "pipeline"

    # Planner should not appear in category selection
    assert "planner-agent" not in reg.get_agents_for_category("analyze")
    assert "analyze-agent" in reg.get_agents_for_category("analyze")


@pytest.mark.asyncio
async def test_autoloaded_agents_tagged_as_pipeline(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Autoloaded agents from DB are always tagged as pipeline agents."""
    mock_db.fetch_all.return_value = [
        {
            "name": "repo-custom-agent",
            "spec": {"categories": ["review"], "priority": 50},
            "source": "autoload:my-repo",
        }
    ]
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    reg = AgentRegistry(mock_db, str(agents_dir), str(tmp_path / "prompts"))
    await reg.load(str(tmp_path / "nonexistent.json"))

    assert reg.get_agent_group("repo-custom-agent") == "pipeline"
    assert "repo-custom-agent" in reg.get_agents_for_category("review")

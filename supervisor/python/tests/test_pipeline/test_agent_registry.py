"""Tests for agent registry."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import AgentRegistryError, NoAvailableAgentError
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry, _parse_md_agent_file


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
                "categories": ["implement"],
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
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    agents = reg.get_agents_for_category("analyze")
    assert "analyzer" in agents
    assert "reviewer" in agents


@pytest.mark.asyncio
async def test_agents_for_category_sorted(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    agents = reg.get_agents_for_category("analyze")
    assert agents == ["analyzer", "reviewer"]


@pytest.mark.asyncio
async def test_agents_for_unknown_category(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    agents = reg.get_agents_for_category("nonexistent")
    assert agents == []


@pytest.mark.asyncio
async def test_select_agent_available(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    mock_db.fetch_val.return_value = 0

    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    agent = await reg.select_agent("analyze")
    assert agent == "analyzer"


@pytest.mark.asyncio
async def test_select_agent_none_available(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    mock_db.fetch_val.return_value = 100

    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    with pytest.raises(NoAvailableAgentError):
        await reg.select_agent("analyze")


@pytest.mark.asyncio
async def test_agent_is_available(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    mock_db.fetch_val.return_value = 0
    assert await reg.agent_is_available("analyzer") is True

    mock_db.fetch_val.return_value = 2
    assert await reg.agent_is_available("analyzer") is False

    mock_db.fetch_val.return_value = 1
    assert await reg.agent_is_available("analyzer") is True


def test_get_agent_model(tmp_path: Path) -> None:
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "with-model": {"model": "claude-sonnet-4-6"},
        "no-model": {"resources": {"timeoutMinutes": 10}},
    }
    assert reg.get_agent_model("with-model") == "claude-sonnet-4-6"
    assert reg.get_agent_model("no-model") is None
    assert reg.get_agent_model("unknown") is None


def test_get_agent_timeout(tmp_path: Path) -> None:
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "fast": {"resources": {"timeoutMinutes": 10}},
        "default": {},
    }
    assert reg.get_agent_timeout("fast") == 10
    assert reg.get_agent_timeout("default") == 30
    assert reg.get_agent_timeout("unknown") == 30


def test_get_agent_prompt_file(tmp_path: Path) -> None:
    mock_db = AsyncMock(spec=Database)
    defs_dir = tmp_path / "definitions" / "pipeline"
    defs_dir.mkdir(parents=True)
    def_file_yaml = str(defs_dir / "custom.yaml")
    def_file_md = str(defs_dir / "hybrid.md")
    agents_dir = tmp_path / "definitions"
    reg = AgentRegistry(mock_db, str(agents_dir))
    reg._agents = {
        "custom": {"promptFile": "../../prompts/custom-prompt.md", "_definition_file": def_file_yaml},
        "default": {"_definition_file": def_file_yaml},
        "hybrid": {"_definition_file": def_file_md},  # no promptFile → hybrid .md agent
    }
    assert reg.get_agent_prompt_file("custom") == (tmp_path / "prompts" / "custom-prompt.md").resolve()
    assert reg.get_agent_prompt_file("default") == (defs_dir / "default.md").resolve()
    # Hybrid .md agent without promptFile returns the definition file itself
    assert reg.get_agent_prompt_file("hybrid") == Path(def_file_md).resolve()


def test_get_allowed_denied_tools(tmp_path: Path) -> None:
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
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
    reg = AgentRegistry(mock_db, str(tmp_path))
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
    reg = AgentRegistry(mock_db, str(tmp_path))
    # Don't load any agents
    with pytest.raises(NoAvailableAgentError, match="registry is empty"):
        await reg.select_agent("analyze")


@pytest.mark.asyncio
async def test_select_agent_no_category_match(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """No agents for requested category gives a clear error."""
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))
    with pytest.raises(NoAvailableAgentError, match="No agents registered"):
        await reg.select_agent("nonexistent-category")


@pytest.mark.asyncio
async def test_select_agent_at_capacity(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """All agents at capacity gives a clear error."""
    mock_db.fetch_val.return_value = 9999  # way over capacity
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))
    with pytest.raises(NoAvailableAgentError, match="at capacity"):
        await reg.select_agent("analyze")


@pytest.mark.asyncio
async def test_discover_agents_from_md(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Hybrid .md agent discovery loads frontmatter-based files."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    md_content = (
        "---\n"
        "name: md-agent\n"
        "categories:\n"
        "  - test\n"
        "priority: 5\n"
        "---\n"
        "# Test prompt\n"
    )
    (agents_dir / "md-agent.md").write_text(md_content)
    # Non-agent file should be skipped
    (agents_dir / "config.txt").write_text("not an agent")

    reg = AgentRegistry(mock_db, str(agents_dir))
    # No registry file → triggers discovery
    await reg.load(str(tmp_path / "nonexistent.json"))

    agents = reg.get_agents_for_category("test")
    assert "md-agent" in agents


@pytest.mark.asyncio
async def test_discover_agents_missing_dir(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Discovery with missing agents dir logs warning and loads empty."""
    reg = AgentRegistry(
        mock_db, str(tmp_path / "no-such-dir")
    )
    await reg.load(str(tmp_path / "nonexistent.json"))
    assert reg._agents == {}


@pytest.mark.asyncio
async def test_discover_agents_bad_md(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Invalid .md files are skipped without crashing."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "bad.md").write_text("no frontmatter at all")

    reg = AgentRegistry(mock_db, str(agents_dir))
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

    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(path))
    assert "agent-a" in reg._agents


@pytest.mark.asyncio
async def test_load_invalid_json_raises(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Invalid JSON in registry file raises AgentRegistryError."""
    path = tmp_path / "bad.json"
    path.write_text("{bad json")

    reg = AgentRegistry(mock_db, str(tmp_path))
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
    reg = AgentRegistry(mock_db, str(agents_dir))
    await reg.load()  # No argument — uses default path
    assert reg._agents == {}


def test_get_agent_output_schema_returns_schema(tmp_path: Path) -> None:
    """Returns the outputSchema dict when present."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    reg._agents = {"agent1": {"outputSchema": schema}}
    assert reg.get_agent_output_schema("agent1") == schema


def test_get_agent_output_schema_returns_none_when_missing(tmp_path: Path) -> None:
    """Returns None when no outputSchema is defined."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {"agent1": {}}
    assert reg.get_agent_output_schema("agent1") is None


def test_get_agent_output_schema_returns_none_for_unknown(tmp_path: Path) -> None:
    """Returns None for an agent not in the registry."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {}
    assert reg.get_agent_output_schema("nonexistent") is None


def test_get_agent_output_schema_returns_none_for_empty_schema(tmp_path: Path) -> None:
    """Returns None when outputSchema is an empty dict."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {"agent1": {"outputSchema": {}}}
    assert reg.get_agent_output_schema("agent1") is None


# ---------------------------------------------------------------------------
# Group-based filtering (system vs pipeline)
# ---------------------------------------------------------------------------


def test_get_agents_for_category_excludes_system_agents(tmp_path: Path) -> None:
    """System agents must never appear in get_agents_for_category results."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
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
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "planner-agent": {"_group": "system"},
        "analyze-agent": {"_group": "pipeline"},
    }
    assert reg.get_agent_group("planner-agent") == "system"
    assert reg.get_agent_group("analyze-agent") == "pipeline"


def test_get_agent_group_returns_pipeline_for_unknown(tmp_path: Path) -> None:
    """get_agent_group defaults to 'pipeline' for unknown agents."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {}
    assert reg.get_agent_group("nonexistent") == "pipeline"


def test_get_system_agent_by_role_returns_name(tmp_path: Path) -> None:
    """get_system_agent_by_role returns the agent name with the matching role."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
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
    reg = AgentRegistry(mock_db, str(tmp_path))
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

    system_md = (
        "---\n"
        "name: planner-agent\n"
        "role: planner\n"
        "---\n"
        "# Planner prompt\n"
    )
    pipeline_md = (
        "---\n"
        "name: analyze-agent\n"
        "categories:\n"
        "  - analyze\n"
        "priority: 10\n"
        "---\n"
        "# Analyze prompt\n"
    )
    (system_dir / "planner-agent.md").write_text(system_md)
    (pipeline_dir / "analyze-agent.md").write_text(pipeline_md)

    reg = AgentRegistry(mock_db, str(agents_dir))
    await reg.load(str(tmp_path / "nonexistent.json"))

    assert reg.get_agent_group("planner-agent") == "system"
    assert reg.get_agent_group("analyze-agent") == "pipeline"

    # Planner should not appear in category selection
    assert "planner-agent" not in reg.get_agents_for_category("analyze")
    assert "analyze-agent" in reg.get_agents_for_category("analyze")


# ---------------------------------------------------------------------------
# get_all_agent_definitions_json — group field in output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_agent_definitions_json_db_path_includes_group(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """DB path returns group field uppercased from agent_group column."""
    mock_db.fetch_all.return_value = [
        {
            "name": "planner-agent",
            "version": "1.0.0",
            "description": "Planner",
            "spec": json.dumps({"role": "planner", "priority": 1}),
            "agent_group": "system",
        },
        {
            "name": "analyze-agent",
            "version": "1.0.0",
            "description": "Analyzer",
            "spec": json.dumps({"categories": ["analyze"], "priority": 10}),
            "agent_group": "pipeline",
        },
    ]
    reg = AgentRegistry(mock_db, str(tmp_path))

    result = await reg.get_all_agent_definitions_json()

    by_name = {r["name"]: r for r in result}
    assert by_name["planner-agent"]["group"] == "SYSTEM"
    assert by_name["analyze-agent"]["group"] == "PIPELINE"


@pytest.mark.asyncio
async def test_get_all_agent_definitions_json_db_path_returns_all_fields(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """DB path returns all expected fields for each agent."""
    mock_db.fetch_all.return_value = [
        {
            "name": "analyze-agent",
            "version": "2.0.0",
            "description": "Analyzes code",
            "spec": json.dumps({
                "categories": ["analyze"],
                "priority": 15,
                "resources": {"maxConcurrent": 2},
                "outputSchema": {"type": "object"},
                "conditions": {},
            }),
            "agent_group": "pipeline",
        },
    ]
    reg = AgentRegistry(mock_db, str(tmp_path))

    result = await reg.get_all_agent_definitions_json()

    assert len(result) == 1
    entry = result[0]
    assert entry["name"] == "analyze-agent"
    assert entry["version"] == "2.0.0"
    assert entry["description"] == "Analyzes code"
    assert entry["group"] == "PIPELINE"
    assert entry["categories"] == ["analyze"]
    assert entry["priority"] == 15
    assert entry["resources"] == {"maxConcurrent": 2}
    assert entry["outputSchema"] == {"type": "object"}


@pytest.mark.asyncio
async def test_get_all_agent_definitions_json_fallback_to_memory_when_db_empty(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Falls back to in-memory registry when DB returns empty rows."""
    mock_db.fetch_all.return_value = []  # DB returns nothing
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "analyze-agent": {
            "_group": "pipeline",
            "categories": ["analyze"],
            "priority": 10,
            "version": "1.0.0",
            "description": "Analyzer",
        },
    }

    result = await reg.get_all_agent_definitions_json()

    assert len(result) == 1
    assert result[0]["name"] == "analyze-agent"
    assert result[0]["group"] == "PIPELINE"


@pytest.mark.asyncio
async def test_get_all_agent_definitions_json_fallback_to_memory_when_db_fails(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Falls back to in-memory registry when DB query raises an exception."""
    mock_db.fetch_all.side_effect = Exception("DB connection error")
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "planner-agent": {
            "_group": "system",
            "role": "planner",
            "priority": 1,
            "version": "1.0.0",
            "description": "Planner",
        },
    }

    result = await reg.get_all_agent_definitions_json()

    assert len(result) == 1
    assert result[0]["name"] == "planner-agent"
    assert result[0]["group"] == "SYSTEM"


@pytest.mark.asyncio
async def test_get_all_agent_definitions_json_memory_fallback_system_group(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """In-memory fallback correctly uppercase system group from _group field."""
    mock_db.fetch_all.return_value = []
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "condition-evaluator-agent": {
            "_group": "system",
            "role": "condition-evaluator",
            "priority": 1,
        },
        "design-agent": {
            "_group": "pipeline",
            "categories": ["design"],
            "priority": 20,
        },
    }

    result = await reg.get_all_agent_definitions_json()

    by_name = {r["name"]: r for r in result}
    assert by_name["condition-evaluator-agent"]["group"] == "SYSTEM"
    assert by_name["design-agent"]["group"] == "PIPELINE"


@pytest.mark.asyncio
async def test_get_all_agent_definitions_json_db_path_handles_missing_agent_group(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """When agent_group column is missing from DB row dict entirely, defaults to 'pipeline'."""
    row: dict = {
        "name": "legacy-agent",
        "version": "1.0.0",
        "description": "Old agent",
        "spec": json.dumps({"categories": ["analyze"]}),
        # agent_group key is absent entirely — simulates old DB rows before migration
    }
    # The key must truly be absent (not None) for the .get() default to apply
    assert "agent_group" not in row
    mock_db.fetch_all.return_value = [row]
    reg = AgentRegistry(mock_db, str(tmp_path))

    result = await reg.get_all_agent_definitions_json()

    assert len(result) == 1
    assert result[0]["group"] == "PIPELINE"


# ---------------------------------------------------------------------------
# _discover_agents_from_dir — new method for structured subdir loading
# ---------------------------------------------------------------------------


def test_discover_agents_from_dir_skips_invalid_md(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Files with invalid frontmatter are silently skipped in structured scan."""
    # Arrange
    agents_dir = tmp_path / "definitions"
    pipeline_dir = agents_dir / "pipeline"
    system_dir = agents_dir / "system"
    pipeline_dir.mkdir(parents=True)
    system_dir.mkdir(parents=True)

    # A valid pipeline agent
    valid_md = (
        "---\n"
        "name: analyze-agent\n"
        "categories:\n"
        "  - analyze\n"
        "---\n"
        "# Analyze prompt\n"
    )
    # An .md file with missing frontmatter delimiters — should be ignored
    invalid_md = "This is not a valid agent definition file.\n"
    (pipeline_dir / "analyze-agent.md").write_text(valid_md)
    (pipeline_dir / "not-an-agent.md").write_text(invalid_md)

    # Act
    reg = AgentRegistry(mock_db, str(agents_dir))
    reg._discover_agents_from_dir(pipeline_dir, group="pipeline")

    # Assert — only the valid .md file is loaded
    assert "analyze-agent" in reg._agents
    assert "not-an-agent" not in reg._agents


def test_discover_agents_from_dir_handles_parse_error(
    mock_db: AsyncMock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A parse error in an .md file within a structured subdir is handled gracefully."""
    import logging

    # Arrange
    agents_dir = tmp_path / "definitions"
    system_dir = agents_dir / "system"
    system_dir.mkdir(parents=True)

    bad_md = "---\nkey: [unclosed bracket\n---\n# Prompt\n"
    (system_dir / "bad-agent.md").write_text(bad_md)

    # Act — must not raise
    reg = AgentRegistry(mock_db, str(agents_dir))
    with caplog.at_level(logging.WARNING):
        reg._discover_agents_from_dir(system_dir, group="system")

    # Assert — no agents loaded from the bad file
    assert "bad-agent" not in reg._agents


def test_discover_agents_from_dir_tags_correct_group(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Agents discovered via _discover_agents_from_dir carry the group tag passed in."""
    # Arrange
    agents_dir = tmp_path / "definitions"
    system_dir = agents_dir / "system"
    pipeline_dir = agents_dir / "pipeline"
    system_dir.mkdir(parents=True)
    pipeline_dir.mkdir(parents=True)

    sys_md = (
        "---\n"
        "name: planner-agent\n"
        "role: planner\n"
        "---\n"
        "# Planner prompt\n"
    )
    pipe_md = (
        "---\n"
        "name: test-agent\n"
        "categories:\n"
        "  - test\n"
        "---\n"
        "# Test prompt\n"
    )
    (system_dir / "planner-agent.md").write_text(sys_md)
    (pipeline_dir / "test-agent.md").write_text(pipe_md)

    # Act
    reg = AgentRegistry(mock_db, str(agents_dir))
    reg._discover_agents_from_dir(system_dir, group="system")
    reg._discover_agents_from_dir(pipeline_dir, group="pipeline")

    # Assert — group tags are set correctly
    assert reg._agents["planner-agent"]["_group"] == "system"
    assert reg._agents["test-agent"]["_group"] == "pipeline"


def test_discover_agents_from_dir_non_dict_frontmatter_skipped(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Non-dict YAML frontmatter (e.g. a plain list) in an .md file is silently skipped."""
    # Arrange
    agents_dir = tmp_path / "definitions"
    pipeline_dir = agents_dir / "pipeline"
    pipeline_dir.mkdir(parents=True)

    (pipeline_dir / "list-file.md").write_text("---\n- item1\n- item2\n---\n# Prompt\n")

    # Act
    reg = AgentRegistry(mock_db, str(agents_dir))
    reg._discover_agents_from_dir(pipeline_dir, group="pipeline")

    # Assert — registry is empty, no exception raised
    assert len(reg._agents) == 0


# ---------------------------------------------------------------------------
# should_skip_planning — system agents excluded from category counts
# ---------------------------------------------------------------------------


def test_should_skip_planning_excludes_system_agents(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """should_skip_planning uses get_agents_for_category which excludes system agents.

    Even if a system agent overlaps with a pipeline category name, it should not
    be counted when deciding whether to skip planning.
    """
    # Arrange — one pipeline agent for 'analyze', plus a system agent also tagged
    # with an internal spec that would match 'analyze' if categories were checked.
    # System agent must NOT be counted.
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "analyze-agent": {
            "_group": "pipeline",
            "categories": ["analyze"],
            "priority": 10,
        },
        "planner-agent": {
            "_group": "system",
            # System agents don't have categories; verifying they are excluded
            "categories": ["analyze"],  # would match if not filtered
            "priority": 1,
        },
    }

    # Act — only analyze-agent should count; exactly 1 pipeline agent for 'analyze'
    result = reg.should_skip_planning(["analyze"])

    # Assert — True because exactly one pipeline agent handles 'analyze'
    assert result is True


def test_should_skip_planning_returns_false_when_multiple_pipeline_agents(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """should_skip_planning returns False when a category has multiple pipeline agents."""
    # Arrange
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "analyze-agent-a": {"_group": "pipeline", "categories": ["analyze"], "priority": 10},
        "analyze-agent-b": {"_group": "pipeline", "categories": ["analyze"], "priority": 20},
    }

    # Act
    result = reg.should_skip_planning(["analyze"])

    # Assert
    assert result is False


def test_should_skip_planning_returns_false_when_no_agent_for_category(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """should_skip_planning returns False when no pipeline agent handles a category."""
    # Arrange
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "analyze-agent": {"_group": "pipeline", "categories": ["analyze"], "priority": 10},
    }

    # Act
    result = reg.should_skip_planning(["design"])  # no agent for 'design'

    # Assert
    assert result is False


def test_should_skip_planning_returns_true_for_empty_categories(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """should_skip_planning returns True for an empty categories list (vacuous truth)."""
    # Arrange
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {}

    # Act
    result = reg.should_skip_planning([])

    # Assert
    assert result is True


# ---------------------------------------------------------------------------
# get_agent_environment — new feature: agents can define env vars
# ---------------------------------------------------------------------------


def test_get_agent_environment_returns_defined_env(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """get_agent_environment returns the environment dict from agent spec."""
    # Arrange
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "condition-evaluator-agent": {
            "_group": "system",
            "role": "condition-evaluator",
            "environment": {"AGENT_MODE": "condition-evaluation", "LOG_LEVEL": "debug"},
        },
    }

    # Act
    env = reg.get_agent_environment("condition-evaluator-agent")

    # Assert — per-agent env is merged on top of the default agent venv env
    assert env["AGENT_MODE"] == "condition-evaluation"
    assert env["LOG_LEVEL"] == "debug"
    assert env["VIRTUAL_ENV"] == AgentRegistry._AGENT_VENV
    assert env["PATH"].startswith(f"{AgentRegistry._AGENT_VENV}/bin:")


def test_get_agent_environment_returns_defaults_when_no_env(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """get_agent_environment returns the default agent venv env when agent has no environment block."""
    # Arrange
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "analyze-agent": {
            "_group": "pipeline",
            "categories": ["analyze"],
            # No 'environment' key
        },
    }

    # Act
    env = reg.get_agent_environment("analyze-agent")

    # Assert — still gets the default agent venv environment
    assert env == AgentRegistry._AGENT_DEFAULT_ENV


def test_get_agent_environment_returns_defaults_for_unknown_agent(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """get_agent_environment returns default agent venv env for an agent not in the registry."""
    # Arrange
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {}

    # Act
    env = reg.get_agent_environment("nonexistent-agent")

    # Assert
    assert env == AgentRegistry._AGENT_DEFAULT_ENV


# ---------------------------------------------------------------------------
# get_default_agents
# ---------------------------------------------------------------------------


def test_get_default_agents_returns_copy(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """get_default_agents returns a copy of the in-memory agent registry."""
    # Arrange
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "planner-agent": {"_group": "system", "role": "planner"},
        "analyze-agent": {"_group": "pipeline", "categories": ["analyze"]},
    }

    # Act
    result = reg.get_default_agents()

    # Assert — returns all agents
    assert set(result.keys()) == {"planner-agent", "analyze-agent"}
    # Modifying the copy should not affect the registry
    result["new-agent"] = {}
    assert "new-agent" not in reg._agents


# ---------------------------------------------------------------------------
# get_agent_group — system agents always excluded from category selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_agents_never_compete_for_pipeline_slots_in_structured_scan(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """System agents discovered from system/ subdir never appear in get_agents_for_category.

    This is the primary acceptance criterion: after the directory split, pipeline
    stage selection must only return pipeline agents.
    """
    # Arrange
    agents_dir = tmp_path / "definitions"
    system_dir = agents_dir / "system"
    pipeline_dir = agents_dir / "pipeline"
    system_dir.mkdir(parents=True)
    pipeline_dir.mkdir(parents=True)

    system_md = (
        "---\n"
        "name: planner-agent\n"
        "role: planner\n"
        "---\n"
        "# Planner prompt\n"
    )
    pipeline_md_a = (
        "---\n"
        "name: review-agent\n"
        "categories:\n"
        "  - review\n"
        "priority: 50\n"
        "---\n"
        "# Review prompt\n"
    )
    pipeline_md_b = (
        "---\n"
        "name: review-agent-alt\n"
        "categories:\n"
        "  - review\n"
        "priority: 30\n"
        "---\n"
        "# Alt review prompt\n"
    )
    (system_dir / "planner-agent.md").write_text(system_md)
    (pipeline_dir / "review-agent.md").write_text(pipeline_md_a)
    (pipeline_dir / "review-agent-alt.md").write_text(pipeline_md_b)

    # Act
    reg = AgentRegistry(mock_db, str(agents_dir))
    await reg.load(str(tmp_path / "nonexistent.json"))

    candidates = reg.get_agents_for_category("review")

    # Assert — system agent is excluded; only pipeline agents returned
    assert "planner-agent" not in candidates
    assert "review-agent" in candidates
    assert "review-agent-alt" in candidates
    # Sorted by priority ascending: alt (30) < review (50) → review-agent-alt first
    assert candidates[0] == "review-agent-alt"


# ---------------------------------------------------------------------------
# dict() shallow-copy isolation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_agents_from_dir_spec_is_shallow_copy(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Mutating a spec in _agents must not affect the on-disk file content.

    The ``dict(frontmatter)`` shallow copy introduced in
    _discover_agents_from_dir ensures the registry's spec dict is isolated
    from the parsed frontmatter so that in-memory mutations do not
    propagate back to re-reads of the file.
    """
    # Arrange: write a valid pipeline agent .md file
    agents_dir = tmp_path / "pipeline"
    agents_dir.mkdir(parents=True)
    md_content = (
        "---\n"
        "name: canary-agent\n"
        "categories:\n"
        "  - canary\n"
        "priority: 5\n"
        "---\n"
        "# Canary prompt\n"
    )
    md_file = agents_dir / "canary-agent.md"
    md_file.write_text(md_content)

    reg = AgentRegistry(mock_db, str(tmp_path))

    # Act: load via _discover_agents_from_dir
    reg._discover_agents_from_dir(agents_dir, group="pipeline")

    # Assert: the agent was loaded
    assert "canary-agent" in reg._agents

    # Mutate the in-registry spec dict
    reg._agents["canary-agent"]["categories"] = ["mutated"]
    reg._agents["canary-agent"]["_injected_key"] = "should_not_appear_on_disk"

    # Re-read the file from disk — frontmatter must be unchanged
    reloaded_fm, _ = _parse_md_agent_file(md_file)
    assert reloaded_fm["categories"] == ["canary"]
    assert "_injected_key" not in reloaded_fm


@pytest.mark.asyncio
async def test_discover_agents_flat_scan_spec_is_shallow_copy(
    mock_db: AsyncMock, tmp_path: Path
) -> None:
    """Flat-scan path also isolates registry spec from the parsed frontmatter.

    The flat scan in ``_discover_agents`` (used when system/ and pipeline/
    subdirectories do NOT exist) applies the same ``dict()`` shallow copy.
    """
    # Arrange: write an .md agent directly in agents_dir (flat layout)
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    md_content = (
        "---\n"
        "name: flat-agent\n"
        "categories:\n"
        "  - flat\n"
        "priority: 1\n"
        "---\n"
        "# Flat agent prompt\n"
    )
    md_file = agents_dir / "flat-agent.md"
    md_file.write_text(md_content)

    mock_db.fetch_all.return_value = []
    mock_db.execute.return_value = None

    reg = AgentRegistry(mock_db, str(agents_dir))

    # Use a non-existent registry file so load() falls through to _discover_agents
    non_existent = str(tmp_path / "no-registry.json")
    await reg.load(non_existent)

    # Assert: agent was loaded
    assert "flat-agent" in reg._agents

    # Mutate in-registry spec
    reg._agents["flat-agent"]["categories"] = ["mutated"]
    reg._agents["flat-agent"]["_injected_key"] = "should_not_appear_on_disk"

    # Re-read file from disk — frontmatter must be unchanged
    reloaded_fm, _ = _parse_md_agent_file(md_file)
    assert reloaded_fm["categories"] == ["flat"]
    assert "_injected_key" not in reloaded_fm


# ---------------------------------------------------------------------------
# _sync_agent_instances — active_count reset on startup (issue #56)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_agent_instances_resets_active_count_on_startup(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """On load, _sync_agent_instances issues ON CONFLICT DO UPDATE SET active_count = 0.

    This ensures stale active_count values from a previous supervisor run
    (where agents may have been killed mid-execution) are reset to 0.
    """
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    # _sync_agent_instances is called during load; check the SQL used
    execute_calls = [
        call for call in mock_db.execute.call_args_list
        if "agent_instances" in str(call)
    ]
    assert len(execute_calls) >= 1, "Expected at least one agent_instances INSERT"

    for call in execute_calls:
        sql = call[0][0]
        assert "ON CONFLICT" in sql
        assert "SET active_count = 0" in sql, (
            "Expected ON CONFLICT to SET active_count = 0 (not DO NOTHING)"
        )


@pytest.mark.asyncio
async def test_sync_agent_instances_called_for_each_agent(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """_sync_agent_instances must issue one INSERT per registered agent."""
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    # Registry file has 3 agents: analyzer, implementer, reviewer
    sync_calls = [
        call for call in mock_db.execute.call_args_list
        if "agent_instances" in str(call) and "INSERT" in str(call)
    ]
    assert len(sync_calls) == 3

    synced_names = {call[0][1]["name"] for call in sync_calls}
    assert synced_names == {"analyzer", "implementer", "reviewer"}


@pytest.mark.asyncio
async def test_sync_agent_instances_preserves_total_executions(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """The ON CONFLICT clause must NOT reset total_executions or last_execution_at.

    Only active_count should be set to 0; the other columns should be preserved.
    """
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    sync_calls = [
        call for call in mock_db.execute.call_args_list
        if "agent_instances" in str(call) and "ON CONFLICT" in str(call)
    ]

    for call in sync_calls:
        sql = call[0][0]
        # Must NOT reset total_executions
        assert "total_executions" not in sql.split("DO UPDATE")[1], (
            "ON CONFLICT clause should not modify total_executions"
        )
        # Must NOT reset last_execution_at
        assert "last_execution_at" not in sql.split("DO UPDATE")[1], (
            "ON CONFLICT clause should not modify last_execution_at"
        )


@pytest.mark.asyncio
async def test_sync_agent_instances_inserts_new_agents_with_zero_counts(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """New agents (no conflict) get active_count=0 and total_executions=0."""
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    sync_calls = [
        call for call in mock_db.execute.call_args_list
        if "agent_instances" in str(call) and "INSERT" in str(call)
    ]

    for call in sync_calls:
        sql = call[0][0]
        # The INSERT VALUES should include 0 for both active_count and total_executions
        assert "VALUES" in sql
        # Normalize whitespace and check the VALUES clause contains 0, 0
        values_section = sql.split("VALUES")[1].split("ON CONFLICT")[0]
        normalized = " ".join(values_section.split())
        assert "0, 0)" in normalized, (
            f"INSERT VALUES should end with 0, 0 for active_count and total_executions, "
            f"got: {normalized}"
        )


@pytest.mark.asyncio
async def test_sync_does_not_use_do_nothing(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """Regression test: ON CONFLICT must NOT use DO NOTHING (the old buggy behavior).

    The old code used DO NOTHING which left stale active_count values from
    previous runs, causing agents to appear at capacity and blocking dispatch.
    """
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    sync_calls = [
        call for call in mock_db.execute.call_args_list
        if "agent_instances" in str(call) and "ON CONFLICT" in str(call)
    ]

    for call in sync_calls:
        sql = call[0][0]
        assert "DO NOTHING" not in sql, (
            "Must NOT use DO NOTHING — stale active_count must be reset to 0"
        )


@pytest.mark.asyncio
async def test_sync_agent_instances_sql_uses_named_parameter(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """The INSERT should use %(name)s parameter, not string interpolation.

    This guards against SQL injection and ensures parameterized queries.
    """
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    sync_calls = [
        call for call in mock_db.execute.call_args_list
        if "agent_instances" in str(call) and "INSERT" in str(call)
    ]

    for call in sync_calls:
        sql = call[0][0]
        params = call[0][1]
        # Must use parameterized query, not string formatting
        assert "%(name)s" in sql, "SQL must use %(name)s parameter binding"
        assert "name" in params, "Params dict must contain 'name' key"
        # Agent name must not appear literally in the SQL
        agent_name = params["name"]
        assert agent_name not in sql, (
            f"Agent name '{agent_name}' should not be interpolated into SQL"
        )


@pytest.mark.asyncio
async def test_sync_agent_instances_uses_on_conflict_agent_name(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """ON CONFLICT must target (agent_name) as the unique constraint."""
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    sync_calls = [
        call for call in mock_db.execute.call_args_list
        if "agent_instances" in str(call) and "ON CONFLICT" in str(call)
    ]
    assert len(sync_calls) >= 1

    for call in sync_calls:
        sql = call[0][0]
        assert re.search(r"ON\s+CONFLICT\s*\(\s*agent_name\s*\)", sql), (
            "ON CONFLICT must target exactly (agent_name) column"
        )


@pytest.mark.asyncio
async def test_sync_agent_instances_only_sets_active_count_in_update(
    mock_db: AsyncMock, registry_file: Path, tmp_path: Path
) -> None:
    """The DO UPDATE clause should ONLY set active_count, nothing else.

    This is a stricter version of the preservation test — it verifies that
    exactly one column is modified in the update clause.
    """
    reg = AgentRegistry(mock_db, str(tmp_path))
    await reg.load(str(registry_file))

    sync_calls = [
        call for call in mock_db.execute.call_args_list
        if "agent_instances" in str(call) and "DO UPDATE" in str(call)
    ]

    for call in sync_calls:
        sql = call[0][0]
        # Extract the SET clause and find all "column =" pairs within it
        after_set = re.split(r"\bSET\b", sql.split("DO UPDATE")[1], maxsplit=1)[1]
        set_columns = re.findall(r"(\w+)\s*=", after_set)
        assert set_columns == ["active_count"], (
            f"Expected exactly ['active_count'] in ON CONFLICT UPDATE SET, "
            f"got {set_columns}"
        )


# ---------------------------------------------------------------------------
# Regex assertion correctness tests
# ---------------------------------------------------------------------------


class TestOnConflictRegex:
    """Verify the ON CONFLICT regex used in sync assertions handles edge cases."""

    @pytest.mark.parametrize(
        "sql",
        [
            "ON CONFLICT (agent_name) DO UPDATE SET active_count = 0",
            "ON  CONFLICT  (  agent_name  ) DO UPDATE SET active_count = 0",
            "ON CONFLICT(agent_name) DO UPDATE SET active_count = 0",
            "ON\n CONFLICT\n(\nagent_name\n) DO UPDATE SET active_count = 0",
        ],
        ids=["standard", "extra-spaces", "no-space-before-paren", "newlines"],
    )
    def test_on_conflict_regex_matches_valid_sql(self, sql: str) -> None:
        """Regex matches ON CONFLICT (agent_name) with varying whitespace."""
        assert re.search(r"ON\s+CONFLICT\s*\(\s*agent_name\s*\)", sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "ON CONFLICT (other_col) DO UPDATE SET active_count = 0",
            "ON CONFLICT (agent_name, repo_name) DO UPDATE SET active_count = 0",
        ],
        ids=["wrong-column", "multi-column-conflict"],
    )
    def test_on_conflict_regex_rejects_wrong_target(self, sql: str) -> None:
        """Regex must not match when conflict target is wrong."""
        assert not re.search(r"ON\s+CONFLICT\s*\(\s*agent_name\s*\)", sql)


class TestSetColumnExtraction:
    """Verify the SET-column extraction regex captures all assigned columns."""

    def _extract_set_columns(self, sql: str) -> list[str]:
        """Mirror the extraction logic from the test assertions."""
        after_set = re.split(r"\bSET\b", sql.split("DO UPDATE")[1], maxsplit=1)[1]
        return re.findall(r"(\w+)\s*=", after_set)

    def test_single_column(self) -> None:
        sql = "ON CONFLICT (agent_name) DO UPDATE SET active_count = 0"
        assert self._extract_set_columns(sql) == ["active_count"]

    def test_multiple_columns_detected(self) -> None:
        """Ensure regex catches ALL columns, not just the first after SET."""
        sql = (
            "ON CONFLICT (agent_name) DO UPDATE "
            "SET active_count = 0, model = excluded.model"
        )
        columns = self._extract_set_columns(sql)
        assert columns == ["active_count", "model"]

    def test_columns_with_extra_whitespace(self) -> None:
        sql = "ON CONFLICT (agent_name) DO UPDATE SET  active_count  =  0"
        assert self._extract_set_columns(sql) == ["active_count"]

    def test_columns_with_newlines(self) -> None:
        sql = (
            "ON CONFLICT (agent_name) DO UPDATE\n"
            "SET active_count = 0,\n"
            "    total_executions = total_executions + 1"
        )
        columns = self._extract_set_columns(sql)
        assert columns == ["active_count", "total_executions"]

    def test_excluded_dot_notation_not_captured_as_extra_column(self) -> None:
        """excluded.col should not add spurious column names."""
        sql = "ON CONFLICT (agent_name) DO UPDATE SET active_count = excluded.active_count"
        columns = self._extract_set_columns(sql)
        # 'active_count' from SET and 'active_count' from excluded.active_count
        # The regex captures both because 'active_count = excluded.active_count'
        # has two word=patterns — but only the first is a real SET target.
        # This test documents the current behavior.
        assert "active_count" in columns


# ---------------------------------------------------------------------------
# _parse_md_agent_file — hybrid .md parsing
# ---------------------------------------------------------------------------


class TestParseMdAgentFile:
    """Tests for the _parse_md_agent_file() utility function."""

    def test_valid_md_file(self, tmp_path: Path) -> None:
        """Parses a valid hybrid .md file into frontmatter dict and prompt body."""
        md = (
            "---\n"
            "name: test-agent\n"
            "version: \"1.0.0\"\n"
            "categories:\n"
            "  - test\n"
            "---\n"
            "# Test Agent\n"
            "\n"
            "This is the prompt body.\n"
        )
        f = tmp_path / "test-agent.md"
        f.write_text(md)

        frontmatter, prompt = _parse_md_agent_file(f)

        assert frontmatter["name"] == "test-agent"
        assert frontmatter["version"] == "1.0.0"
        assert frontmatter["categories"] == ["test"]
        assert "# Test Agent" in prompt
        assert "This is the prompt body." in prompt

    def test_missing_opening_delimiter(self, tmp_path: Path) -> None:
        """Raises ValueError when the file is missing the opening '---'."""
        f = tmp_path / "bad.md"
        f.write_text("name: test-agent\n---\n# Prompt\n")

        with pytest.raises(ValueError, match="Missing opening '---'"):
            _parse_md_agent_file(f)

    def test_missing_closing_delimiter(self, tmp_path: Path) -> None:
        """Raises ValueError when the file has opening '---' but no closing '---'."""
        f = tmp_path / "bad.md"
        f.write_text("---\nname: test-agent\n# No closing delimiter\n")

        with pytest.raises(ValueError, match="Missing closing '---'"):
            _parse_md_agent_file(f)

    def test_non_dict_yaml(self, tmp_path: Path) -> None:
        """Raises ValueError when the frontmatter YAML parses to a non-dict type."""
        f = tmp_path / "bad.md"
        f.write_text("---\n- item1\n- item2\n---\n# Prompt\n")

        with pytest.raises(ValueError, match="did not parse as a dict"):
            _parse_md_agent_file(f)

    def test_triple_dash_in_prompt_body(self, tmp_path: Path) -> None:
        """Triple dashes in the prompt body do not interfere with parsing."""
        md = (
            "---\n"
            "name: safe-agent\n"
            "---\n"
            "# Prompt\n"
            "\n"
            "---\n"
            "This section has a horizontal rule.\n"
        )
        f = tmp_path / "safe.md"
        f.write_text(md)

        frontmatter, prompt = _parse_md_agent_file(f)
        assert frontmatter["name"] == "safe-agent"
        assert "---" in prompt
        assert "horizontal rule" in prompt


# ---------------------------------------------------------------------------
# get_agent_prompt_content — new method for hybrid .md agents
# ---------------------------------------------------------------------------


def test_get_agent_prompt_content_returns_body(tmp_path: Path) -> None:
    """get_agent_prompt_content returns the prompt body from a hybrid .md file."""
    mock_db = AsyncMock(spec=Database)
    agents_dir = tmp_path / "definitions" / "pipeline"
    agents_dir.mkdir(parents=True)

    md = (
        "---\n"
        "name: my-agent\n"
        "categories:\n"
        "  - test\n"
        "---\n"
        "# My Agent Prompt\n"
        "\n"
        "This is the body.\n"
    )
    md_file = agents_dir / "my-agent.md"
    md_file.write_text(md)

    reg = AgentRegistry(mock_db, str(tmp_path / "definitions"))
    reg._agents = {
        "my-agent": {
            "_definition_file": str(md_file),
            "categories": ["test"],
        }
    }

    content = reg.get_agent_prompt_content("my-agent")
    assert content is not None
    assert "# My Agent Prompt" in content
    assert "This is the body." in content


def test_get_agent_prompt_content_returns_none_for_json_agent(tmp_path: Path) -> None:
    """get_agent_prompt_content returns None for agents loaded from JSON (no .md file)."""
    mock_db = AsyncMock(spec=Database)
    reg = AgentRegistry(mock_db, str(tmp_path))
    reg._agents = {
        "json-agent": {
            "name": "json-agent",
            "categories": ["test"],
            # No _definition_file
        }
    }
    assert reg.get_agent_prompt_content("json-agent") is None


def test_get_agent_prompt_file_returns_md_path_for_hybrid_agent(tmp_path: Path) -> None:
    """get_agent_prompt_file returns the .md definition file path for hybrid agents."""
    mock_db = AsyncMock(spec=Database)
    agents_dir = tmp_path / "definitions" / "pipeline"
    agents_dir.mkdir(parents=True)

    md_file = agents_dir / "my-agent.md"
    md_file.write_text("---\nname: my-agent\n---\n# Prompt\n")

    reg = AgentRegistry(mock_db, str(tmp_path / "definitions"))
    reg._agents = {
        "my-agent": {
            "_definition_file": str(md_file),
            "categories": ["test"],
            # No promptFile key — hybrid .md agent
        }
    }

    result = reg.get_agent_prompt_file("my-agent")
    assert result == md_file.resolve()

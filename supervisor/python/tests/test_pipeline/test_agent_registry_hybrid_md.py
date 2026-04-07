"""Tests for hybrid .md agent file parsing and discovery.

Covers the _parse_md_agent_file function, _discover_agents_from_dir,
and related agent registry behaviour introduced by the merge of agent
definition YAML + prompt Markdown into a single hybrid .md format.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock

import pytest
import yaml

from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry, _parse_md_agent_file


# ---------------------------------------------------------------------------
# _parse_md_agent_file — unit tests
# ---------------------------------------------------------------------------


class TestParseMdAgentFile:
    """Unit tests for the _parse_md_agent_file function."""

    def test_valid_hybrid_file(self, tmp_path: Path) -> None:
        """Parses a well-formed hybrid .md file into frontmatter dict + prompt body."""
        md = tmp_path / "agent.md"
        md.write_text(dedent("""\
            ---
            name: test-agent
            version: "1.0.0"
            description: "A test agent"
            model: sonnet
            categories:
              - test
            priority: 10
            tools:
              allowed:
                - Read
                - Grep
              denied:
                - Write
            resources:
              maxTokens: 50000
              timeoutMinutes: 15
              maxConcurrent: 3
            ---
            # Test Agent Prompt

            You are a test agent.
        """))

        frontmatter, prompt = _parse_md_agent_file(md)

        assert frontmatter["name"] == "test-agent"
        assert frontmatter["version"] == "1.0.0"
        assert frontmatter["model"] == "sonnet"
        assert frontmatter["categories"] == ["test"]
        assert frontmatter["priority"] == 10
        assert frontmatter["tools"]["allowed"] == ["Read", "Grep"]
        assert frontmatter["tools"]["denied"] == ["Write"]
        assert frontmatter["resources"]["maxTokens"] == 50000
        assert "# Test Agent Prompt" in prompt
        assert "You are a test agent." in prompt

    def test_missing_opening_delimiter(self, tmp_path: Path) -> None:
        """Raises ValueError when the file does not start with ---."""
        md = tmp_path / "bad.md"
        md.write_text("name: test-agent\n---\n# Prompt\n")

        with pytest.raises(ValueError, match="Missing opening '---'"):
            _parse_md_agent_file(md)

    def test_missing_closing_delimiter(self, tmp_path: Path) -> None:
        """Raises ValueError when there is no closing --- delimiter."""
        md = tmp_path / "bad.md"
        md.write_text("---\nname: test-agent\n# No closing delimiter\n")

        with pytest.raises(ValueError, match="Missing closing '---'"):
            _parse_md_agent_file(md)

    def test_invalid_yaml_frontmatter(self, tmp_path: Path) -> None:
        """Raises yaml.YAMLError for malformed YAML in frontmatter."""
        md = tmp_path / "bad.md"
        md.write_text("---\nkey: [unclosed bracket\n---\n# Prompt\n")

        with pytest.raises(yaml.YAMLError):
            _parse_md_agent_file(md)

    def test_non_dict_frontmatter_raises(self, tmp_path: Path) -> None:
        """Raises ValueError when frontmatter parses as a non-dict (e.g., list)."""
        md = tmp_path / "list.md"
        md.write_text("---\n- item1\n- item2\n---\n# Prompt\n")

        with pytest.raises(ValueError, match="did not parse as a dict"):
            _parse_md_agent_file(md)

    def test_scalar_frontmatter_raises(self, tmp_path: Path) -> None:
        """Raises ValueError when frontmatter parses as a scalar."""
        md = tmp_path / "scalar.md"
        md.write_text("---\njust a string\n---\n# Prompt\n")

        with pytest.raises(ValueError, match="did not parse as a dict"):
            _parse_md_agent_file(md)

    def test_empty_frontmatter_raises(self, tmp_path: Path) -> None:
        """Raises ValueError when frontmatter is empty (None from yaml.safe_load)."""
        md = tmp_path / "empty.md"
        md.write_text("---\n\n---\n# Prompt\n")

        with pytest.raises(ValueError, match="did not parse as a dict"):
            _parse_md_agent_file(md)

    def test_minimal_valid_frontmatter(self, tmp_path: Path) -> None:
        """Accepts a minimal frontmatter with just a name key."""
        md = tmp_path / "minimal.md"
        md.write_text("---\nname: minimal-agent\n---\n# Prompt\n")

        frontmatter, prompt = _parse_md_agent_file(md)
        assert frontmatter["name"] == "minimal-agent"
        assert "# Prompt" in prompt

    def test_prompt_body_with_multiple_sections(self, tmp_path: Path) -> None:
        """Correctly separates long multi-section prompts from frontmatter."""
        prompt_content = dedent("""\
            # Agent Prompt

            ## Section 1
            Content here.

            ## Section 2
            More content.

            ---

            A horizontal rule inside prompt body should not confuse the parser.
        """)
        md = tmp_path / "agent.md"
        md.write_text(f"---\nname: multi-section\n---\n{prompt_content}")

        frontmatter, prompt = _parse_md_agent_file(md)
        assert frontmatter["name"] == "multi-section"
        # The horizontal rule inside the prompt body is preserved
        assert "A horizontal rule inside prompt body" in prompt

    def test_frontmatter_with_complex_nested_yaml(self, tmp_path: Path) -> None:
        """Handles complex nested YAML in frontmatter."""
        md = tmp_path / "complex.md"
        md.write_text(dedent("""\
            ---
            name: complex-agent
            tools:
              allowed:
                - Read
                - Grep
              denied:
                - Write
            resources:
              maxTokens: 50000
              timeoutMinutes: 15
            environment:
              AGENT_MODE: "analyze"
              STRICT_MODE: "true"
            healthCheck:
              enabled: true
              intervalSeconds: 300
            ---
            # Complex Agent
        """))

        frontmatter, _prompt = _parse_md_agent_file(md)
        assert frontmatter["environment"]["AGENT_MODE"] == "analyze"
        assert frontmatter["healthCheck"]["enabled"] is True
        assert frontmatter["healthCheck"]["intervalSeconds"] == 300

    def test_system_agent_with_role_field(self, tmp_path: Path) -> None:
        """System agents use 'role' instead of 'categories'."""
        md = tmp_path / "system.md"
        md.write_text(dedent("""\
            ---
            name: planner-agent
            version: "1.0.0"
            role: planner
            model: sonnet
            ---
            # Planner Agent
        """))

        frontmatter, _prompt = _parse_md_agent_file(md)
        assert frontmatter["role"] == "planner"
        assert "categories" not in frontmatter

    def test_pipeline_agent_with_categories_field(self, tmp_path: Path) -> None:
        """Pipeline agents use 'categories' instead of 'role'."""
        md = tmp_path / "pipeline.md"
        md.write_text(dedent("""\
            ---
            name: analyze-agent
            categories:
              - analyze
            priority: 1
            ---
            # Analyzer
        """))

        frontmatter, _prompt = _parse_md_agent_file(md)
        assert frontmatter["categories"] == ["analyze"]
        assert "role" not in frontmatter


# ---------------------------------------------------------------------------
# Discovery — structured subdir scanning with hybrid .md files
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    return db


def _write_agent_md(path: Path, name: str, extra_frontmatter: str = "", prompt: str = "# Prompt\n") -> None:
    """Helper to write a hybrid .md agent file."""
    path.write_text(f"---\nname: {name}\n{extra_frontmatter}---\n{prompt}")


class TestDiscoverAgentsFromDir:
    """Tests for _discover_agents_from_dir with hybrid .md files."""

    def test_loads_all_md_files(self, mock_db: AsyncMock, tmp_path: Path) -> None:
        """All valid .md files in directory are loaded."""
        agents_dir = tmp_path / "definitions"
        pipeline_dir = agents_dir / "pipeline"
        pipeline_dir.mkdir(parents=True)

        _write_agent_md(pipeline_dir / "agent-a.md", "agent-a", "categories:\n  - test\n")
        _write_agent_md(pipeline_dir / "agent-b.md", "agent-b", "categories:\n  - review\n")

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._discover_agents_from_dir(pipeline_dir, group="pipeline")

        assert "agent-a" in reg._agents
        assert "agent-b" in reg._agents

    def test_ignores_non_md_files(self, mock_db: AsyncMock, tmp_path: Path) -> None:
        """Non-.md files (e.g., .yaml, .txt) are not loaded."""
        agents_dir = tmp_path / "definitions"
        pipeline_dir = agents_dir / "pipeline"
        pipeline_dir.mkdir(parents=True)

        _write_agent_md(pipeline_dir / "valid.md", "valid-agent", "categories:\n  - test\n")
        (pipeline_dir / "legacy.yaml").write_text("name: old-agent\n")
        (pipeline_dir / "readme.txt").write_text("Not an agent")

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._discover_agents_from_dir(pipeline_dir, group="pipeline")

        assert "valid-agent" in reg._agents
        assert len(reg._agents) == 1

    def test_sets_definition_file_metadata(self, mock_db: AsyncMock, tmp_path: Path) -> None:
        """Each loaded agent stores the _definition_file path."""
        agents_dir = tmp_path / "definitions"
        pipeline_dir = agents_dir / "pipeline"
        pipeline_dir.mkdir(parents=True)

        md_path = pipeline_dir / "test-agent.md"
        _write_agent_md(md_path, "test-agent", "categories:\n  - test\n")

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._discover_agents_from_dir(pipeline_dir, group="pipeline")

        assert reg._agents["test-agent"]["_definition_file"] == str(md_path)

    def test_uses_name_from_frontmatter_over_filename(self, mock_db: AsyncMock, tmp_path: Path) -> None:
        """Agent name comes from frontmatter 'name' key, not the filename."""
        agents_dir = tmp_path / "definitions"
        pipeline_dir = agents_dir / "pipeline"
        pipeline_dir.mkdir(parents=True)

        _write_agent_md(pipeline_dir / "filename-agent.md", "frontmatter-name", "categories:\n  - test\n")

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._discover_agents_from_dir(pipeline_dir, group="pipeline")

        assert "frontmatter-name" in reg._agents
        assert "filename-agent" not in reg._agents

    def test_falls_back_to_stem_when_name_missing(self, mock_db: AsyncMock, tmp_path: Path) -> None:
        """When frontmatter lacks 'name', the file stem is used."""
        agents_dir = tmp_path / "definitions"
        pipeline_dir = agents_dir / "pipeline"
        pipeline_dir.mkdir(parents=True)

        (pipeline_dir / "fallback-agent.md").write_text(
            "---\ncategories:\n  - test\n---\n# Prompt\n"
        )

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._discover_agents_from_dir(pipeline_dir, group="pipeline")

        assert "fallback-agent" in reg._agents

    def test_empty_directory(self, mock_db: AsyncMock, tmp_path: Path) -> None:
        """Empty directory results in no agents loaded."""
        agents_dir = tmp_path / "definitions"
        empty_dir = agents_dir / "pipeline"
        empty_dir.mkdir(parents=True)

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._discover_agents_from_dir(empty_dir, group="pipeline")

        assert len(reg._agents) == 0


class TestDiscoverAgentsFullWorkflow:
    """Integration tests for the full discovery workflow via load()."""

    @pytest.mark.asyncio
    async def test_discovers_system_and_pipeline_agents_from_subdirs(
        self, mock_db: AsyncMock, tmp_path: Path
    ) -> None:
        """Full discovery from system/ + pipeline/ subdirs tags agents correctly."""
        agents_dir = tmp_path / "definitions"
        system_dir = agents_dir / "system"
        pipeline_dir = agents_dir / "pipeline"
        system_dir.mkdir(parents=True)
        pipeline_dir.mkdir(parents=True)

        _write_agent_md(system_dir / "planner-agent.md", "planner-agent", "role: planner\n")
        _write_agent_md(system_dir / "condition-evaluator-agent.md", "condition-evaluator-agent", "role: condition-evaluator\n")
        _write_agent_md(pipeline_dir / "analyze-agent.md", "analyze-agent", "categories:\n  - analyze\npriority: 1\n")
        _write_agent_md(pipeline_dir / "design-agent.md", "design-agent", "categories:\n  - design\npriority: 10\n")

        reg = AgentRegistry(mock_db, str(agents_dir))
        await reg.load(str(tmp_path / "nonexistent.json"))

        # System agents
        assert reg.get_agent_group("planner-agent") == "system"
        assert reg.get_agent_group("condition-evaluator-agent") == "system"
        assert reg.get_system_agent_by_role("planner") == "planner-agent"
        assert reg.get_system_agent_by_role("condition-evaluator") == "condition-evaluator-agent"

        # Pipeline agents
        assert reg.get_agent_group("analyze-agent") == "pipeline"
        assert reg.get_agent_group("design-agent") == "pipeline"
        assert "analyze-agent" in reg.get_agents_for_category("analyze")
        assert "design-agent" in reg.get_agents_for_category("design")

        # System agents excluded from category selection
        assert "planner-agent" not in reg.get_agents_for_category("analyze")

    @pytest.mark.asyncio
    async def test_flat_scan_fallback_when_no_subdirs(
        self, mock_db: AsyncMock, tmp_path: Path
    ) -> None:
        """When no system/ and pipeline/ subdirs exist, falls back to flat scan."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        _write_agent_md(agents_dir / "my-agent.md", "my-agent", "categories:\n  - test\npriority: 5\n")

        reg = AgentRegistry(mock_db, str(agents_dir))
        await reg.load(str(tmp_path / "nonexistent.json"))

        assert "my-agent" in reg._agents
        assert reg.get_agent_group("my-agent") == "pipeline"

    @pytest.mark.asyncio
    async def test_full_hybrid_file_round_trip(
        self, mock_db: AsyncMock, tmp_path: Path
    ) -> None:
        """An agent file with all fields is discovered and all accessors work."""
        agents_dir = tmp_path / "definitions"
        pipeline_dir = agents_dir / "pipeline"
        system_dir = agents_dir / "system"
        pipeline_dir.mkdir(parents=True)
        system_dir.mkdir(parents=True)

        (pipeline_dir / "analyze-agent.md").write_text(dedent("""\
            ---
            name: analyze-agent
            version: "1.0.0"
            description: "Triages issues"
            model: sonnet
            categories:
              - analyze
            priority: 1
            tools:
              allowed:
                - Read
                - Grep
              denied:
                - Write
            resources:
              maxTokens: 50000
              timeoutMinutes: 15
              maxConcurrent: 3
              maxTurns: 20
              maxCost: 1.0
            environment:
              AGENT_MODE: "analyze"
            ---
            # Analyze Agent

            You are an analysis agent.
        """))

        reg = AgentRegistry(mock_db, str(agents_dir))
        await reg.load(str(tmp_path / "nonexistent.json"))

        assert reg.get_agent_model("analyze-agent") == "sonnet"
        assert reg.get_agent_timeout("analyze-agent") == 15
        assert reg.get_agent_max_turns("analyze-agent") == 20
        assert reg.get_agent_max_cost("analyze-agent") == 1.0
        assert reg.get_allowed_tools("analyze-agent") == ["Read", "Grep"]
        assert reg.get_denied_tools("analyze-agent") == ["Write"]
        env = reg.get_agent_environment("analyze-agent")
        assert env["AGENT_MODE"] == "analyze"
        assert "VIRTUAL_ENV" in env  # default env merged


# ---------------------------------------------------------------------------
# get_agent_prompt_file and get_agent_prompt_content — hybrid .md support
# ---------------------------------------------------------------------------


class TestAgentPromptFileHybrid:
    """Tests for prompt file resolution with hybrid .md agents."""

    def test_hybrid_md_returns_definition_file_itself(self, tmp_path: Path) -> None:
        """For hybrid .md agents (no promptFile), the definition file IS the prompt."""
        mock_db = AsyncMock(spec=Database)
        agents_dir = tmp_path / "definitions"
        agents_dir.mkdir()
        md_path = agents_dir / "test-agent.md"
        md_path.write_text("---\nname: test-agent\n---\n# Prompt\n")

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._agents = {
            "test-agent": {"_definition_file": str(md_path)},
        }

        result = reg.get_agent_prompt_file("test-agent")
        assert result == md_path.resolve()

    def test_legacy_agent_with_promptFile_resolves_path(self, tmp_path: Path) -> None:
        """Legacy agents with explicit promptFile resolve relative to definition file."""
        mock_db = AsyncMock(spec=Database)
        agents_dir = tmp_path / "definitions"
        pipeline_dir = agents_dir / "pipeline"
        pipeline_dir.mkdir(parents=True)
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        def_file = pipeline_dir / "custom.yaml"
        def_file.touch()

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._agents = {
            "custom": {
                "promptFile": "../../prompts/custom-prompt.md",
                "_definition_file": str(def_file),
            },
        }

        result = reg.get_agent_prompt_file("custom")
        assert result == (prompts_dir / "custom-prompt.md").resolve()

    def test_prompt_file_path_traversal_blocked(self, tmp_path: Path) -> None:
        """Path traversal via promptFile outside config dir is rejected."""
        from aquarco_supervisor.exceptions import AgentRegistryError

        mock_db = AsyncMock(spec=Database)
        agents_dir = tmp_path / "definitions"
        pipeline_dir = agents_dir / "pipeline"
        pipeline_dir.mkdir(parents=True)

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._agents = {
            "evil-agent": {
                "promptFile": "../../../../etc/passwd",
                "_definition_file": str(pipeline_dir / "evil.yaml"),
            },
        }

        with pytest.raises(AgentRegistryError, match="escapes config directory"):
            reg.get_agent_prompt_file("evil-agent")


class TestAgentPromptContent:
    """Tests for get_agent_prompt_content with hybrid .md files."""

    def test_returns_prompt_body_from_hybrid_md(self, tmp_path: Path) -> None:
        """Returns the embedded Markdown prompt body from a hybrid .md file."""
        mock_db = AsyncMock(spec=Database)
        agents_dir = tmp_path / "definitions"
        agents_dir.mkdir()
        md_path = agents_dir / "test-agent.md"
        md_path.write_text("---\nname: test-agent\n---\n# Test Agent\n\nYou are a test agent.\n")

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._agents = {
            "test-agent": {"_definition_file": str(md_path)},
        }

        content = reg.get_agent_prompt_content("test-agent")
        assert content is not None
        assert "# Test Agent" in content
        assert "You are a test agent." in content

    def test_returns_none_for_non_md_agent(self, tmp_path: Path) -> None:
        """Returns None for agents not loaded from .md files."""
        mock_db = AsyncMock(spec=Database)
        reg = AgentRegistry(mock_db, str(tmp_path))
        reg._agents = {
            "json-agent": {"_definition_file": "/some/path/agent.json"},
        }

        assert reg.get_agent_prompt_content("json-agent") is None

    def test_returns_none_for_agent_without_definition_file(self, tmp_path: Path) -> None:
        """Returns None for DB-loaded agents (no _definition_file)."""
        mock_db = AsyncMock(spec=Database)
        reg = AgentRegistry(mock_db, str(tmp_path))
        reg._agents = {
            "db-agent": {"categories": ["test"]},
        }

        assert reg.get_agent_prompt_content("db-agent") is None

    def test_returns_none_for_unknown_agent(self, tmp_path: Path) -> None:
        """Returns None for an agent not in the registry."""
        mock_db = AsyncMock(spec=Database)
        reg = AgentRegistry(mock_db, str(tmp_path))
        reg._agents = {}

        assert reg.get_agent_prompt_content("nonexistent") is None

    def test_returns_none_when_prompt_body_is_empty(self, tmp_path: Path) -> None:
        """Returns None when the prompt body is empty/whitespace-only."""
        mock_db = AsyncMock(spec=Database)
        agents_dir = tmp_path / "definitions"
        agents_dir.mkdir()
        md_path = agents_dir / "empty-prompt.md"
        md_path.write_text("---\nname: empty-prompt\n---\n\n  \n")

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._agents = {
            "empty-prompt": {"_definition_file": str(md_path)},
        }

        assert reg.get_agent_prompt_content("empty-prompt") is None

    def test_returns_none_on_parse_error(self, tmp_path: Path) -> None:
        """Returns None (not exception) when .md file has a parse error."""
        mock_db = AsyncMock(spec=Database)
        agents_dir = tmp_path / "definitions"
        agents_dir.mkdir()
        md_path = agents_dir / "bad-agent.md"
        md_path.write_text("no frontmatter delimiters")

        reg = AgentRegistry(mock_db, str(agents_dir))
        reg._agents = {
            "bad-agent": {"_definition_file": str(md_path)},
        }

        assert reg.get_agent_prompt_content("bad-agent") is None


# ---------------------------------------------------------------------------
# Real config file integration — verify actual agent .md files parse
# ---------------------------------------------------------------------------


class TestRealConfigFiles:
    """Integration tests that parse the actual agent definition files."""

    @pytest.fixture
    def config_agents_dir(self) -> Path:
        """Path to the real config/agents/definitions directory."""
        repo_root = Path(__file__).resolve().parents[4]
        agents_dir = repo_root / "config" / "agents" / "definitions"
        if not agents_dir.exists():
            pytest.skip("Real config/agents/definitions not found")
        return agents_dir

    def test_all_pipeline_agents_parse(self, config_agents_dir: Path) -> None:
        """All pipeline agent .md files parse without error."""
        pipeline_dir = config_agents_dir / "pipeline"
        if not pipeline_dir.exists():
            pytest.skip("pipeline/ subdir not found")

        for md_file in sorted(pipeline_dir.glob("*.md")):
            frontmatter, prompt = _parse_md_agent_file(md_file)
            assert isinstance(frontmatter, dict), f"{md_file.name}: frontmatter not a dict"
            assert "name" in frontmatter, f"{md_file.name}: missing 'name'"
            assert "categories" in frontmatter, f"{md_file.name}: missing 'categories'"
            assert len(prompt.strip()) > 0, f"{md_file.name}: empty prompt body"

    def test_all_system_agents_parse(self, config_agents_dir: Path) -> None:
        """All system agent .md files parse without error."""
        system_dir = config_agents_dir / "system"
        if not system_dir.exists():
            pytest.skip("system/ subdir not found")

        for md_file in sorted(system_dir.glob("*.md")):
            frontmatter, prompt = _parse_md_agent_file(md_file)
            assert isinstance(frontmatter, dict), f"{md_file.name}: frontmatter not a dict"
            assert "name" in frontmatter, f"{md_file.name}: missing 'name'"
            assert "role" in frontmatter, f"{md_file.name}: system agent missing 'role'"

    def test_no_old_yaml_files_remain(self, config_agents_dir: Path) -> None:
        """Verify that old .yaml definition files have been removed."""
        for yaml_file in config_agents_dir.rglob("*.yaml"):
            pytest.fail(f"Old YAML definition file still exists: {yaml_file}")
        for yml_file in config_agents_dir.rglob("*.yml"):
            pytest.fail(f"Old YML definition file still exists: {yml_file}")

    @pytest.mark.asyncio
    async def test_full_registry_load_from_real_files(self, config_agents_dir: Path) -> None:
        """Load the real agent files into an AgentRegistry and verify counts."""
        mock_db = AsyncMock(spec=Database)
        mock_db.fetch_all.return_value = []  # No autoloaded agents

        reg = AgentRegistry(mock_db, str(config_agents_dir))
        await reg.load(str(config_agents_dir.parent.parent / "schemas" / "nonexistent.json"))

        # We should have at least 6 pipeline + 2 system agents
        all_agents = reg._agents
        assert len(all_agents) >= 8, f"Expected >=8 agents, got {len(all_agents)}"

        # Verify system agents exist
        assert reg.get_system_agent_by_role("planner") is not None
        assert reg.get_system_agent_by_role("condition-evaluator") is not None

        # Verify pipeline agents exist via category
        for category in ["analyze", "design", "implement", "review", "test"]:
            agents = reg.get_agents_for_category(category)
            assert len(agents) >= 1, f"No agent for category '{category}'"

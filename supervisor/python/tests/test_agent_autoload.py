"""Tests for agent autoloader module."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.agent_autoloader import (
    MAX_AGENT_PROMPTS,
    MAX_PROMPT_SIZE_BYTES,
    analyze_agent_prompt,
    autoload_repo_agents,
    generate_agent_definition,
    has_claude_agents,
    scan_repo_agents,
    write_aquarco_config,
)


# ---------------------------------------------------------------------------
# scan_repo_agents
# ---------------------------------------------------------------------------


def test_scan_repo_agents_empty_dir(tmp_path: Path):
    """Scanning a repo without .claude/agents/ returns empty list."""
    result = scan_repo_agents(tmp_path)
    assert result == []


def test_scan_repo_agents_no_md_files(tmp_path: Path):
    """Scanning a repo with .claude/agents/ but no .md files returns empty."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    result = scan_repo_agents(tmp_path)
    assert result == []


def test_scan_repo_agents_valid_files(tmp_path: Path):
    """Scanning finds valid .md files."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "test-agent.md").write_text("# Test Agent\nDoes testing.")
    (agents_dir / "review-agent.md").write_text("# Review Agent\nDoes reviews.")

    result = scan_repo_agents(tmp_path)
    assert len(result) == 2
    assert result[0].name == "review-agent.md"  # sorted alphabetically
    assert result[1].name == "test-agent.md"


def test_scan_repo_agents_skips_invalid_filenames(tmp_path: Path):
    """Files with invalid names are skipped."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "valid-agent.md").write_text("# Valid")
    (agents_dir / "has spaces.md").write_text("# Invalid name")
    (agents_dir / "special!chars.md").write_text("# Invalid name")

    result = scan_repo_agents(tmp_path)
    assert len(result) == 1
    assert result[0].name == "valid-agent.md"


def test_scan_repo_agents_skips_oversized_files(tmp_path: Path):
    """Files exceeding the size limit are skipped."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "small-agent.md").write_text("# Small agent")
    (agents_dir / "large-agent.md").write_text("x" * (MAX_PROMPT_SIZE_BYTES + 1))

    result = scan_repo_agents(tmp_path)
    assert len(result) == 1
    assert result[0].name == "small-agent.md"


def test_scan_repo_agents_respects_max_limit(tmp_path: Path):
    """Only MAX_AGENT_PROMPTS files are returned."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    for i in range(MAX_AGENT_PROMPTS + 5):
        (agents_dir / f"agent-{i:03d}.md").write_text(f"# Agent {i}")

    result = scan_repo_agents(tmp_path)
    assert len(result) == MAX_AGENT_PROMPTS


# ---------------------------------------------------------------------------
# analyze_agent_prompt
# ---------------------------------------------------------------------------


def test_analyze_agent_prompt_basic():
    """Basic analysis extracts name and description."""
    content = textwrap.dedent("""\
        # Test Agent
        This agent handles testing and QA.
    """)
    result = analyze_agent_prompt(content, "test-agent.md")
    assert result["name"] == "test-agent"
    assert "Test Agent" in result["description"]
    assert result["category"] == "test"


def test_analyze_agent_prompt_implementation():
    """Agent with implementation-related content gets implementation category."""
    content = "This agent writes code and implements features."
    result = analyze_agent_prompt(content, "coder.md")
    assert result["category"] == "implementation"


def test_analyze_agent_prompt_security():
    """Agent with security-related content gets security category."""
    content = "Audits code for security vulnerabilities and OWASP issues."
    result = analyze_agent_prompt(content, "sec-scanner.md")
    assert result["category"] == "security"


def test_analyze_agent_prompt_default_tools():
    """Default tools are always included."""
    content = "A simple agent."
    result = analyze_agent_prompt(content, "simple.md")
    assert "Read" in result["tools"]
    assert "Grep" in result["tools"]
    assert "Glob" in result["tools"]


def test_analyze_agent_prompt_infers_bash_tool():
    """Agent mentioning bash/shell gets Bash tool."""
    content = "This agent runs bash commands to build the project."
    result = analyze_agent_prompt(content, "builder.md")
    assert "Bash" in result["tools"]


# ---------------------------------------------------------------------------
# generate_agent_definition
# ---------------------------------------------------------------------------


def test_generate_agent_definition():
    """Generated definition has correct structure."""
    analysis = {
        "name": "test-agent",
        "description": "Test agent description",
        "category": "test",
        "tools": ["Read", "Grep", "Glob", "Bash"],
    }
    defn = generate_agent_definition(analysis, "my-repo", "# Test prompt")

    assert defn["apiVersion"] == "aquarco.agents/v1"
    assert defn["kind"] == "AgentDefinition"
    assert defn["metadata"]["name"] == "my-repo-test-agent"
    assert defn["metadata"]["version"] == "1.0.0"
    assert defn["metadata"]["labels"]["source"] == "autoloaded"
    assert defn["metadata"]["labels"]["repository"] == "my-repo"
    assert defn["spec"]["categories"] == ["test"]
    assert defn["spec"]["promptInline"] == "# Test prompt"
    assert "Bash" in defn["spec"]["tools"]["allowed"]


# ---------------------------------------------------------------------------
# write_aquarco_config
# ---------------------------------------------------------------------------


def test_write_aquarco_config(tmp_path: Path):
    """Writes YAML files to aquarco-config/agents/."""
    definitions = [
        {
            "apiVersion": "aquarco.agents/v1",
            "kind": "AgentDefinition",
            "metadata": {"name": "my-repo-agent-a", "version": "1.0.0"},
            "spec": {"categories": ["test"]},
        },
        {
            "apiVersion": "aquarco.agents/v1",
            "kind": "AgentDefinition",
            "metadata": {"name": "my-repo-agent-b", "version": "1.0.0"},
            "spec": {"categories": ["review"]},
        },
    ]

    count = write_aquarco_config(tmp_path, definitions)
    assert count == 2

    config_dir = tmp_path / "aquarco-config" / "agents"
    assert (config_dir / "my-repo-agent-a.yaml").exists()
    assert (config_dir / "my-repo-agent-b.yaml").exists()


def test_write_aquarco_config_creates_directory(tmp_path: Path):
    """The target directory is created if it doesn't exist."""
    definitions = [
        {
            "metadata": {"name": "test-agent", "version": "1.0.0"},
            "spec": {},
        },
    ]

    count = write_aquarco_config(tmp_path, definitions)
    assert count == 1
    assert (tmp_path / "aquarco-config" / "agents").is_dir()


# ---------------------------------------------------------------------------
# has_claude_agents
# ---------------------------------------------------------------------------


def test_has_claude_agents_true(tmp_path: Path):
    """Returns True when .claude/agents/ has .md files."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent.md").write_text("# Agent")

    assert has_claude_agents(tmp_path) is True


def test_has_claude_agents_false_no_dir(tmp_path: Path):
    """Returns False when .claude/agents/ doesn't exist."""
    assert has_claude_agents(tmp_path) is False


def test_has_claude_agents_false_empty_dir(tmp_path: Path):
    """Returns False when .claude/agents/ exists but has no .md files."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    assert has_claude_agents(tmp_path) is False


# ---------------------------------------------------------------------------
# autoload_repo_agents (integration with mocked DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autoload_repo_agents_no_agents_dir(tmp_path: Path):
    """Autoloading a repo without .claude/agents/ completes with 0 agents."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    result = await autoload_repo_agents(tmp_path, "test-repo", db, scan_id=1)

    assert result["agents_found"] == 0
    assert result["agents_created"] == 0
    assert result["error"] is None


@pytest.mark.asyncio
async def test_autoload_repo_agents_with_agents(tmp_path: Path):
    """Autoloading a repo with .claude/agents/ discovers and stores agents."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "test-agent.md").write_text("# Test Agent\nRuns tests.")
    (agents_dir / "review-agent.md").write_text("# Review Agent\nReviews code.")

    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"count": 0})
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    result = await autoload_repo_agents(tmp_path, "my-repo", db, scan_id=1)

    assert result["agents_found"] == 2
    assert result["agents_created"] == 2
    assert result["error"] is None

    # Verify aquarco-config was written
    config_dir = tmp_path / "aquarco-config" / "agents"
    assert config_dir.is_dir()
    yaml_files = list(config_dir.glob("*.yaml"))
    assert len(yaml_files) == 2


@pytest.mark.asyncio
async def test_autoload_repo_agents_updates_scan_status(tmp_path: Path):
    """Scan status transitions are recorded in the database."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent.md").write_text("# Agent")

    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"count": 0})
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    await autoload_repo_agents(tmp_path, "repo", db, scan_id=42)

    # Check that status was updated through the phases
    execute_calls = db.execute.call_args_list
    status_updates = [
        call for call in execute_calls
        if "repo_agent_scans" in str(call)
    ]
    assert len(status_updates) >= 3  # scanning, analyzing, writing, completed

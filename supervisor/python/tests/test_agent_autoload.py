"""Tests for agent autoloader module.

Covers acceptance criteria for the 'Autoload .claude agents' feature (issue #14).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import yaml

from aquarco_supervisor.agent_autoloader import (
    DEFAULT_TOOLS,
    FILENAME_PATTERN,
    MAX_AGENT_PROMPTS,
    MAX_PROMPT_SIZE_BYTES,
    RATE_LIMIT_SECONDS,
    analyze_agent_prompt,
    autoload_repo_agents,
    check_rate_limit,
    create_scan_record,
    deactivate_autoloaded_agents,
    generate_agent_definition,
    get_latest_scan,
    has_claude_agents,
    is_scan_in_progress,
    scan_repo_agents,
    store_autoloaded_agents,
    update_scan_status,
    write_aquarco_config,
)
from aquarco_supervisor.models import RepoAgentScanStatus


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
    """AC: Scanning a repo with .claude/agents/ containing 3 valid .md files
    creates 3 agent definitions."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "test-agent.md").write_text("# Test Agent\nDoes testing.")
    (agents_dir / "review-agent.md").write_text("# Review Agent\nDoes reviews.")
    (agents_dir / "build-agent.md").write_text("# Build Agent\nBuilds stuff.")

    result = scan_repo_agents(tmp_path)
    assert len(result) == 3
    names = [f.name for f in result]
    assert "build-agent.md" in names
    assert "review-agent.md" in names
    assert "test-agent.md" in names


def test_scan_repo_agents_sorted_alphabetically(tmp_path: Path):
    """Returned files are sorted alphabetically."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "zeta.md").write_text("# Z")
    (agents_dir / "alpha.md").write_text("# A")

    result = scan_repo_agents(tmp_path)
    assert result[0].name == "alpha.md"
    assert result[1].name == "zeta.md"


def test_scan_repo_agents_skips_invalid_filenames(tmp_path: Path):
    """AC: Agent prompt filenames not matching ^[a-zA-Z0-9_-]+\\.md$ are skipped."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "valid-agent.md").write_text("# Valid")
    (agents_dir / "has spaces.md").write_text("# Invalid name")
    (agents_dir / "special!chars.md").write_text("# Invalid name")
    (agents_dir / ".hidden.md").write_text("# Hidden")
    (agents_dir / "dots.in.name.md").write_text("# Dots")

    result = scan_repo_agents(tmp_path)
    assert len(result) == 1
    assert result[0].name == "valid-agent.md"


def test_scan_repo_agents_skips_oversized_files(tmp_path: Path):
    """AC: Agent prompt files larger than 50KB are skipped and logged as warnings."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "small-agent.md").write_text("# Small agent")
    (agents_dir / "large-agent.md").write_text("x" * (MAX_PROMPT_SIZE_BYTES + 1))

    result = scan_repo_agents(tmp_path)
    assert len(result) == 1
    assert result[0].name == "small-agent.md"


def test_scan_repo_agents_exact_50kb_is_allowed(tmp_path: Path):
    """Files at exactly 50KB are allowed (<=, not <)."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "exact.md").write_text("x" * MAX_PROMPT_SIZE_BYTES)

    result = scan_repo_agents(tmp_path)
    assert len(result) == 1


def test_scan_repo_agents_respects_max_limit(tmp_path: Path):
    """AC: Maximum 20 agent prompts per scan; additional files are skipped."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    for i in range(MAX_AGENT_PROMPTS + 5):
        (agents_dir / f"agent-{i:03d}.md").write_text(f"# Agent {i}")

    result = scan_repo_agents(tmp_path)
    assert len(result) == MAX_AGENT_PROMPTS


def test_scan_repo_agents_ignores_non_md_files(tmp_path: Path):
    """Non-.md files in agents dir are ignored."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "agent.md").write_text("# Agent")
    (agents_dir / "readme.txt").write_text("Not an agent")
    (agents_dir / "config.yaml").write_text("key: value")

    result = scan_repo_agents(tmp_path)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# FILENAME_PATTERN validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("valid-agent.md", True),
    ("my_agent.md", True),
    ("Agent123.md", True),
    ("a.md", True),
    ("has spaces.md", False),
    ("special!.md", False),
    (".hidden.md", False),
    ("dots.in.name.md", False),
    ("path/traversal.md", False),
])
def test_filename_pattern_matching(name: str, expected: bool):
    """FILENAME_PATTERN correctly validates agent filenames."""
    assert bool(FILENAME_PATTERN.match(name)) is expected


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
    content = "Checks for security vulnerabilities and OWASP issues."
    result = analyze_agent_prompt(content, "sec-scanner.md")
    assert result["category"] == "security"


def test_analyze_agent_prompt_design_category():
    """Agent with design/architect content gets design category."""
    content = "This agent designs the system architecture."
    result = analyze_agent_prompt(content, "architect.md")
    assert result["category"] == "design"


def test_analyze_agent_prompt_review_category():
    """Agent with review/QA content gets review category."""
    content = "This agent performs quality reviews and linting."
    result = analyze_agent_prompt(content, "reviewer.md")
    assert result["category"] == "review"


def test_analyze_agent_prompt_docs_category():
    """Agent with docs content gets docs category."""
    content = "This agent maintains documentation and README files."
    result = analyze_agent_prompt(content, "documenter.md")
    assert result["category"] == "docs"


def test_analyze_agent_prompt_analyze_category():
    """Agent with analyze/triage content gets analyze category."""
    content = "This agent triages and analyzes incoming issues."
    result = analyze_agent_prompt(content, "triager.md")
    assert result["category"] == "analyze"


def test_analyze_agent_prompt_default_category():
    """Agent with no specific hints defaults to implementation."""
    content = "A helper for everyday tasks."
    result = analyze_agent_prompt(content, "helper.md")
    assert result["category"] == "implementation"


def test_analyze_agent_prompt_default_tools():
    """AC: Autoloaded agents use conservative default tools (Read, Grep, Glob)."""
    content = "A simple agent."
    result = analyze_agent_prompt(content, "simple.md")
    assert "Read" in result["tools"]
    assert "Grep" in result["tools"]
    assert "Glob" in result["tools"]
    # No extra tools when content is simple
    assert set(result["tools"]) == set(DEFAULT_TOOLS)


def test_analyze_agent_prompt_infers_bash_tool():
    """Agent mentioning bash/shell gets Bash tool."""
    content = "This agent runs bash commands to build the project."
    result = analyze_agent_prompt(content, "builder.md")
    assert "Bash" in result["tools"]


def test_analyze_agent_prompt_infers_write_tool():
    """Agent mentioning write/create gets Write tool."""
    content = "This agent writes new files and generates code."
    result = analyze_agent_prompt(content, "writer.md")
    assert "Write" in result["tools"]


def test_analyze_agent_prompt_infers_edit_tool():
    """Agent mentioning edit/modify gets Edit tool."""
    content = "This agent edits and modifies existing files."
    result = analyze_agent_prompt(content, "editor.md")
    assert "Edit" in result["tools"]


def test_analyze_agent_prompt_description_capped_at_200():
    """Description is capped at 200 characters."""
    long_line = "A" * 300
    content = f"# {long_line}"
    result = analyze_agent_prompt(content, "long.md")
    assert len(result["description"]) <= 200


def test_analyze_agent_prompt_strips_md_suffix():
    """Filename .md suffix is removed for name."""
    result = analyze_agent_prompt("content", "my-agent.md")
    assert result["name"] == "my-agent"


def test_analyze_agent_prompt_empty_content():
    """Empty content returns defaults."""
    result = analyze_agent_prompt("", "empty.md")
    assert result["name"] == "empty"
    assert result["description"] == ""
    assert result["category"] == "implementation"
    assert set(result["tools"]) == set(DEFAULT_TOOLS)


def test_analyze_agent_prompt_category_from_name():
    """Category can be inferred from agent name alone."""
    content = "No hints in content."
    result = analyze_agent_prompt(content, "test-runner.md")
    assert result["category"] == "test"


# ---------------------------------------------------------------------------
# generate_agent_definition
# ---------------------------------------------------------------------------


def test_generate_agent_definition():
    """AC: Generated agent definitions have valid apiVersion, kind, metadata, spec."""
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
    assert defn["metadata"]["labels"]["original-name"] == "test-agent"
    assert defn["spec"]["categories"] == ["test"]
    assert defn["spec"]["promptInline"] == "# Test prompt"
    assert "Bash" in defn["spec"]["tools"]["allowed"]
    assert defn["spec"]["tools"]["denied"] == []
    assert defn["spec"]["priority"] == 50
    assert defn["spec"]["resources"]["timeoutMinutes"] == 30
    assert defn["spec"]["resources"]["maxConcurrent"] == 1
    assert defn["spec"]["resources"]["maxTurns"] == 30
    assert defn["spec"]["resources"]["maxCost"] == 5.0


def test_generate_agent_definition_name_prefix():
    """Agent name is prefixed with repo_name."""
    analysis = {"name": "my-agent", "description": "", "category": "test", "tools": []}
    defn = generate_agent_definition(analysis, "cool-repo", "prompt")
    assert defn["metadata"]["name"] == "cool-repo-my-agent"


def test_generate_agent_definition_preserves_prompt():
    """Full prompt content is embedded in promptInline."""
    prompt = "# Full Prompt\n\nThis is a multi-line prompt.\n\n## Details\nSome details."
    analysis = {"name": "agent", "description": "", "category": "test", "tools": []}
    defn = generate_agent_definition(analysis, "repo", prompt)
    assert defn["spec"]["promptInline"] == prompt


def test_generate_agent_definition_default_tools_fallback():
    """When tools are missing from analysis, DEFAULT_TOOLS are used."""
    analysis = {"name": "agent", "description": "", "category": "test"}
    defn = generate_agent_definition(analysis, "repo", "prompt")
    assert defn["spec"]["tools"]["allowed"] == DEFAULT_TOOLS


# ---------------------------------------------------------------------------
# write_aquarco_config
# ---------------------------------------------------------------------------


def test_write_aquarco_config(tmp_path: Path):
    """AC: Scanning creates agent definition YAML files in <repo>/aquarco-config/agents/."""
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


def test_write_aquarco_config_valid_yaml(tmp_path: Path):
    """Written YAML files can be parsed back correctly."""
    defn = {
        "apiVersion": "aquarco.agents/v1",
        "kind": "AgentDefinition",
        "metadata": {"name": "roundtrip", "version": "1.0.0"},
        "spec": {"categories": ["test"], "tools": {"allowed": ["Read"]}},
    }

    write_aquarco_config(tmp_path, [defn])

    written = yaml.safe_load(
        (tmp_path / "aquarco-config" / "agents" / "roundtrip.yaml").read_text()
    )
    assert written["apiVersion"] == "aquarco.agents/v1"
    assert written["kind"] == "AgentDefinition"
    assert written["metadata"]["name"] == "roundtrip"


def test_write_aquarco_config_skips_nameless(tmp_path: Path):
    """Definitions without a name are skipped."""
    definitions = [
        {"metadata": {}, "spec": {}},
        {"metadata": {"name": "valid", "version": "1.0.0"}, "spec": {}},
    ]

    count = write_aquarco_config(tmp_path, definitions)
    assert count == 1


def test_write_aquarco_config_empty_list(tmp_path: Path):
    """Empty definitions list writes 0 files."""
    count = write_aquarco_config(tmp_path, [])
    assert count == 0


# ---------------------------------------------------------------------------
# has_claude_agents
# ---------------------------------------------------------------------------


def test_has_claude_agents_true(tmp_path: Path):
    """AC: Repository.hasClaudeAgents=true when .claude/agents/ exists."""
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


def test_has_claude_agents_false_non_md_files(tmp_path: Path):
    """Returns False when .claude/agents/ has only non-.md files."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "readme.txt").write_text("Not an agent")
    assert has_claude_agents(tmp_path) is False


# ---------------------------------------------------------------------------
# DB helpers - create_scan_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_scan_record():
    """Creates a pending scan record and returns its ID."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"id": 42})

    scan_id = await create_scan_record(db, "my-repo")

    assert scan_id == 42
    db.fetch_one.assert_called_once()
    call_args = db.fetch_one.call_args
    assert "INSERT INTO repo_agent_scans" in call_args[0][0]
    assert call_args[0][1]["repo_name"] == "my-repo"


# ---------------------------------------------------------------------------
# DB helpers - update_scan_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_scan_status_scanning():
    """Scanning status sets started_at."""
    db = AsyncMock()
    await update_scan_status(db, 1, "scanning")

    sql = db.execute.call_args[0][0]
    assert "started_at = NOW()" in sql
    assert "status = %(status)s" in sql


@pytest.mark.asyncio
async def test_update_scan_status_completed():
    """Completed status sets completed_at and agents counts."""
    db = AsyncMock()
    await update_scan_status(
        db, 1, "completed", agents_found=5, agents_created=3,
    )

    sql = db.execute.call_args[0][0]
    assert "completed_at = NOW()" in sql
    assert "agents_found = %(agents_found)s" in sql
    assert "agents_created = %(agents_created)s" in sql


@pytest.mark.asyncio
async def test_update_scan_status_failed_with_error():
    """Failed status sets completed_at and error_message."""
    db = AsyncMock()
    await update_scan_status(
        db, 1, "failed", error_message="Something went wrong",
    )

    sql = db.execute.call_args[0][0]
    params = db.execute.call_args[0][1]
    assert "completed_at = NOW()" in sql
    assert "error_message = %(error_message)s" in sql
    assert params["error_message"] == "Something went wrong"


# ---------------------------------------------------------------------------
# DB helpers - get_latest_scan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_latest_scan_exists():
    """Returns the most recent scan record."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"id": 5, "status": "completed"})

    result = await get_latest_scan(db, "my-repo")
    assert result is not None
    assert result["id"] == 5


@pytest.mark.asyncio
async def test_get_latest_scan_none():
    """Returns None when no scans exist."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)

    result = await get_latest_scan(db, "my-repo")
    assert result is None


# ---------------------------------------------------------------------------
# DB helpers - is_scan_in_progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_scan_in_progress_true():
    """AC: Returns error if a scan is already in progress."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"id": 1})

    assert await is_scan_in_progress(db, "my-repo") is True


@pytest.mark.asyncio
async def test_is_scan_in_progress_false():
    """Returns False when no scan is in progress."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)

    assert await is_scan_in_progress(db, "my-repo") is False


@pytest.mark.asyncio
async def test_is_scan_in_progress_checks_correct_statuses():
    """Checks for pending/scanning/analyzing/writing statuses."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)

    await is_scan_in_progress(db, "repo")

    sql = db.fetch_one.call_args[0][0]
    assert "'pending'" in sql
    assert "'scanning'" in sql
    assert "'analyzing'" in sql
    assert "'writing'" in sql


# ---------------------------------------------------------------------------
# DB helpers - check_rate_limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_rate_limit_recent_scan():
    """AC: Rate limiting returns error if called within 5 minutes of last scan."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"id": 1})

    assert await check_rate_limit(db, "my-repo") is True


@pytest.mark.asyncio
async def test_check_rate_limit_no_recent_scan():
    """Returns False when no recent scan exists."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)

    assert await check_rate_limit(db, "my-repo") is False


def test_rate_limit_is_5_minutes():
    """RATE_LIMIT_SECONDS is 300 (5 minutes)."""
    assert RATE_LIMIT_SECONDS == 5 * 60


# ---------------------------------------------------------------------------
# DB helpers - deactivate_autoloaded_agents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_autoloaded_agents():
    """AC: A rescan deactivates all previously autoloaded agents for that repo."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"count": 3})

    count = await deactivate_autoloaded_agents(db, "my-repo")

    assert count == 3
    sql = db.fetch_one.call_args[0][0]
    assert "is_active = false" in sql
    params = db.fetch_one.call_args[0][1]
    assert params["source"] == "autoload:my-repo"


@pytest.mark.asyncio
async def test_deactivate_autoloaded_agents_none():
    """Returns 0 when no agents were deactivated."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)

    count = await deactivate_autoloaded_agents(db, "empty-repo")
    assert count == 0


# ---------------------------------------------------------------------------
# DB helpers - store_autoloaded_agents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_autoloaded_agents():
    """AC: Agents stored with source='autoload:<repo_name>' and is_active=true."""
    db = AsyncMock()
    definitions = [
        {
            "metadata": {"name": "repo-agent-a", "version": "1.0.0", "description": "A"},
            "spec": {"categories": ["test"]},
        },
        {
            "metadata": {"name": "repo-agent-b", "version": "1.0.0", "description": "B"},
            "spec": {"categories": ["review"]},
        },
    ]

    count = await store_autoloaded_agents(db, definitions, "my-repo")

    assert count == 2
    # Should have 2 deactivate + 2 upsert calls = 4 execute calls
    assert db.execute.call_count == 4

    # Check that source is set correctly
    upsert_calls = [
        c for c in db.execute.call_args_list
        if "INSERT INTO agent_definitions" in str(c)
    ]
    for c in upsert_calls:
        params = c[0][1]
        assert params["source"] == "autoload:my-repo"


@pytest.mark.asyncio
async def test_store_autoloaded_agents_skips_nameless():
    """Definitions without a name are skipped."""
    db = AsyncMock()
    definitions = [
        {"metadata": {}, "spec": {}},
        {"metadata": {"name": "valid", "version": "1.0.0"}, "spec": {}},
    ]

    count = await store_autoloaded_agents(db, definitions, "repo")
    assert count == 1


@pytest.mark.asyncio
async def test_store_autoloaded_agents_deactivates_old_versions():
    """Each agent upsert first deactivates previous versions."""
    db = AsyncMock()
    definitions = [
        {"metadata": {"name": "agent-a", "version": "2.0.0"}, "spec": {}},
    ]

    await store_autoloaded_agents(db, definitions, "repo")

    # First call should be the deactivation
    first_call = db.execute.call_args_list[0]
    assert "is_active = false" in first_call[0][0]
    assert first_call[0][1]["name"] == "agent-a"
    assert first_call[0][1]["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# autoload_repo_agents (integration with mocked DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autoload_repo_agents_no_agents_dir(tmp_path: Path):
    """AC: Scanning a repo without .claude/agents/ returns agents_found=0 and
    completes successfully without errors."""
    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value=None)
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    result = await autoload_repo_agents(tmp_path, "test-repo", db, scan_id=1)

    assert result["agents_found"] == 0
    assert result["agents_created"] == 0
    assert result["error"] is None


@pytest.mark.asyncio
async def test_autoload_repo_agents_with_3_agents(tmp_path: Path):
    """AC: Scanning a repo with 3 valid .md files creates 3 agent definitions."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "test-agent.md").write_text("# Test Agent\nRuns tests.")
    (agents_dir / "review-agent.md").write_text("# Review Agent\nReviews code.")
    (agents_dir / "build-agent.md").write_text("# Build Agent\nBuilds things.")

    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"count": 0})
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    result = await autoload_repo_agents(tmp_path, "my-repo", db, scan_id=1)

    assert result["agents_found"] == 3
    assert result["agents_created"] == 3
    assert result["error"] is None

    # Verify aquarco-config was written
    config_dir = tmp_path / "aquarco-config" / "agents"
    yaml_files = list(config_dir.glob("*.yaml"))
    assert len(yaml_files) == 3


@pytest.mark.asyncio
async def test_autoload_repo_agents_status_progression(tmp_path: Path):
    """AC: Scan status progresses: pending → scanning → analyzing → writing → completed."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent.md").write_text("# Agent")

    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"count": 0})
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    await autoload_repo_agents(tmp_path, "repo", db, scan_id=42)

    # Collect status updates from execute calls
    status_updates = []
    for c in db.execute.call_args_list:
        sql = c[0][0]
        if "repo_agent_scans" in sql and "status" in sql:
            params = c[0][1]
            status_updates.append(params["status"])

    assert "scanning" in status_updates
    assert "analyzing" in status_updates
    assert "writing" in status_updates
    assert "completed" in status_updates

    # Verify order
    scanning_idx = status_updates.index("scanning")
    analyzing_idx = status_updates.index("analyzing")
    writing_idx = status_updates.index("writing")
    completed_idx = status_updates.index("completed")
    assert scanning_idx < analyzing_idx < writing_idx < completed_idx


@pytest.mark.asyncio
async def test_autoload_repo_agents_no_scan_id(tmp_path: Path):
    """When scan_id is None, no DB status updates are made for scans."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent.md").write_text("# Agent")

    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"count": 0})
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    result = await autoload_repo_agents(tmp_path, "repo", db, scan_id=None)

    assert result["agents_found"] == 1
    assert result["error"] is None

    # No scan status update calls (only agent deactivation and storage calls)
    scan_updates = [
        c for c in db.execute.call_args_list
        if "repo_agent_scans" in str(c)
    ]
    assert len(scan_updates) == 0


@pytest.mark.asyncio
async def test_autoload_repo_agents_db_error_sets_failed(tmp_path: Path):
    """AC: Failed scans set status=failed with a descriptive error_message."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent.md").write_text("# Agent")

    db = AsyncMock()
    # Make deactivate_autoloaded_agents fail
    db.fetch_one = AsyncMock(side_effect=RuntimeError("DB connection lost"))
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    result = await autoload_repo_agents(tmp_path, "repo", db, scan_id=1)

    assert result["error"] is not None
    assert "DB connection lost" in result["error"]

    # The last execute call should be updating status to failed
    failed_calls = [
        c for c in db.execute.call_args_list
        if "repo_agent_scans" in str(c) and "failed" in str(c[0][1].get("status", ""))
    ]
    assert len(failed_calls) >= 1


@pytest.mark.asyncio
async def test_autoload_repo_agents_deactivates_before_storing(tmp_path: Path):
    """AC: A rescan deactivates all previously autoloaded agents before storing new ones."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "agent.md").write_text("# Agent")

    call_order: list[str] = []

    db = AsyncMock()

    async def track_fetch_one(sql, params=None):
        if "UPDATE agent_definitions" in sql:
            call_order.append("deactivate")
            return {"count": 2}
        return None

    async def track_execute(sql, params=None):
        if "UPDATE agent_definitions" in sql and "is_active = false" in sql:
            call_order.append("deactivate_exec")
        elif "INSERT INTO agent_definitions" in sql:
            call_order.append("insert")

    db.fetch_one = AsyncMock(side_effect=track_fetch_one)
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock(side_effect=track_execute)

    await autoload_repo_agents(tmp_path, "repo", db, scan_id=None)

    # Deactivation should happen before insertion
    deactivate_idx = next(
        (i for i, op in enumerate(call_order) if "deactivate" in op), -1
    )
    insert_idx = next(
        (i for i, op in enumerate(call_order) if op == "insert"), -1
    )
    if insert_idx >= 0 and deactivate_idx >= 0:
        assert deactivate_idx < insert_idx


@pytest.mark.asyncio
async def test_autoload_repo_agents_with_oversized_and_invalid(tmp_path: Path):
    """Mixed valid/invalid files: only valid ones are processed."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "valid-agent.md").write_text("# Valid Agent")
    (agents_dir / "large-agent.md").write_text("x" * (MAX_PROMPT_SIZE_BYTES + 1))
    (agents_dir / "has spaces.md").write_text("# Invalid name")

    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"count": 0})
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    result = await autoload_repo_agents(tmp_path, "repo", db, scan_id=1)

    assert result["agents_found"] == 1
    assert result["agents_created"] == 1


# ---------------------------------------------------------------------------
# RepoAgentScanStatus enum
# ---------------------------------------------------------------------------


def test_repo_agent_scan_status_values():
    """AC: Scan statuses include all expected values."""
    expected = {"pending", "scanning", "analyzing", "writing", "completed", "failed"}
    actual = {s.value for s in RepoAgentScanStatus}
    assert actual == expected


# ---------------------------------------------------------------------------
# RepoAgentScan model
# ---------------------------------------------------------------------------


def test_repo_agent_scan_model():
    """RepoAgentScan model has correct defaults."""
    from aquarco_supervisor.models import RepoAgentScan

    scan = RepoAgentScan(repo_name="test-repo")
    assert scan.repo_name == "test-repo"
    assert scan.status == RepoAgentScanStatus.PENDING
    assert scan.agents_found == 0
    assert scan.agents_created == 0
    assert scan.error_message is None
    assert scan.started_at is None
    assert scan.completed_at is None


# ---------------------------------------------------------------------------
# End-to-end: scan → analyze → generate → write → store pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_3_agents(tmp_path: Path):
    """AC: Full end-to-end pipeline with 3 agents produces correct output."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "analyzer.md").write_text(
        "# Code Analyzer\nThis agent analyzes code for issues."
    )
    (agents_dir / "tester.md").write_text(
        "# Test Runner\nThis agent runs tests and checks coverage."
    )
    (agents_dir / "deployer.md").write_text(
        "# Deployer\nThis agent runs bash commands to deploy."
    )

    db = AsyncMock()
    db.fetch_one = AsyncMock(return_value={"count": 0})
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock()

    result = await autoload_repo_agents(tmp_path, "acme", db, scan_id=10)

    assert result["agents_found"] == 3
    assert result["agents_created"] == 3
    assert result["error"] is None

    # Verify YAML files
    config_dir = tmp_path / "aquarco-config" / "agents"
    for yaml_file in config_dir.glob("*.yaml"):
        doc = yaml.safe_load(yaml_file.read_text())
        assert doc["apiVersion"] == "aquarco.agents/v1"
        assert doc["kind"] == "AgentDefinition"
        assert doc["metadata"]["name"].startswith("acme-")
        assert doc["metadata"]["labels"]["repository"] == "acme"
        assert doc["metadata"]["labels"]["source"] == "autoloaded"
        assert "categories" in doc["spec"]
        assert "tools" in doc["spec"]


@pytest.mark.asyncio
async def test_full_pipeline_categories_inferred(tmp_path: Path):
    """Agents are categorized correctly based on content analysis."""
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)

    (agents_dir / "sec-checker.md").write_text(
        "# Security Checker\nChecks for security vulnerabilities."
    )
    (agents_dir / "docs-gen.md").write_text(
        "# Documentation Generator\nGenerates documentation from source."
    )

    # Scan and analyze
    files = scan_repo_agents(tmp_path)
    assert len(files) == 2

    analyses = {}
    for f in files:
        content = f.read_text()
        analysis = analyze_agent_prompt(content, f.name)
        analyses[analysis["name"]] = analysis

    assert analyses["sec-checker"]["category"] == "security"
    assert analyses["docs-gen"]["category"] == "docs"

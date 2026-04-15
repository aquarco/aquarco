"""Tests for the analyze-bug agent definition and pipeline registration.

Validates that the new analyze-bug category is properly registered:
- Agent definition file exists and parses correctly
- Agent is read-only (no Write/Edit tools)
- Pipeline references the analyze-bug category
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_PATH = REPO_ROOT / "config" / "agents" / "definitions" / "pipeline" / "analyze-bug-agent.md"
PIPELINES_PATH = REPO_ROOT / "config" / "pipelines.yaml"


def _parse_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter from a hybrid .md file."""
    text = path.read_text()
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    assert match, f"No YAML frontmatter found in {path}"
    return yaml.safe_load(match.group(1))


@pytest.fixture
def agent_meta() -> dict:
    return _parse_frontmatter(AGENT_PATH)


@pytest.fixture
def pipelines_data() -> dict:
    return yaml.safe_load(PIPELINES_PATH.read_text())


class TestAnalyzeBugAgentDefinition:
    """Validate the analyze-bug agent definition file."""

    def test_file_exists(self) -> None:
        assert AGENT_PATH.exists(), "analyze-bug-agent.md must exist"

    def test_name(self, agent_meta: dict) -> None:
        assert agent_meta["name"] == "analyze-bug-agent"

    def test_has_version(self, agent_meta: dict) -> None:
        assert "version" in agent_meta
        assert agent_meta["version"]

    def test_category_is_analyze_bug(self, agent_meta: dict) -> None:
        assert "analyze-bug" in agent_meta["categories"]

    def test_has_priority(self, agent_meta: dict) -> None:
        assert "priority" in agent_meta
        assert isinstance(agent_meta["priority"], int)

    def test_read_only_no_write(self, agent_meta: dict) -> None:
        """analyze-bug must NOT have Write in allowed tools."""
        allowed = agent_meta.get("tools", {}).get("allowed", [])
        assert "Write" not in allowed

    def test_read_only_no_edit(self, agent_meta: dict) -> None:
        """analyze-bug must NOT have Edit in allowed tools."""
        allowed = agent_meta.get("tools", {}).get("allowed", [])
        assert "Edit" not in allowed

    def test_write_edit_in_denied(self, agent_meta: dict) -> None:
        """Write and Edit must be explicitly denied."""
        denied = agent_meta.get("tools", {}).get("denied", [])
        assert "Write" in denied
        assert "Edit" in denied

    def test_has_read_tool(self, agent_meta: dict) -> None:
        allowed = agent_meta.get("tools", {}).get("allowed", [])
        assert "Read" in allowed

    def test_has_grep_tool(self, agent_meta: dict) -> None:
        allowed = agent_meta.get("tools", {}).get("allowed", [])
        assert "Grep" in allowed

    def test_has_resource_limits(self, agent_meta: dict) -> None:
        resources = agent_meta.get("resources", {})
        assert "maxTokens" in resources
        assert "timeoutMinutes" in resources
        assert "maxCost" in resources


class TestAnalyzeBugPipelineRegistration:
    """Validate that pipelines.yaml references the analyze-bug category."""

    def test_category_defined(self, pipelines_data: dict) -> None:
        """analyze-bug must be a defined category in pipelines.yaml."""
        categories = pipelines_data.get("categories", [])
        cat_names = [c["name"] for c in categories if isinstance(c, dict)]
        assert "analyze-bug" in cat_names, "analyze-bug category must be defined"

    def test_regression_aware_pipeline_exists(self, pipelines_data: dict) -> None:
        """A pipeline using the analyze-bug category must exist."""
        pipelines = pipelines_data.get("pipelines", [])
        found = False
        for pipeline in pipelines:
            stages = pipeline.get("stages", [])
            for stage in stages:
                if stage.get("category") == "analyze-bug":
                    found = True
                    break
            if found:
                break
        assert found, "No pipeline references the analyze-bug category"

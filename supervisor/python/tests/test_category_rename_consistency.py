"""Tests for category rename consistency (docs→document, implementation→implement).

Validates that all components agree on the canonical category names after the
refactoring in commits e9bbd9a and 46870e2a.

Acceptance criteria from review:
  - Schema enums in pipeline-agent-v1.json and agent-definition-v1.json use new names
  - VALID_CATEGORIES in cli/agents.py uses new names
  - Autoloader category hints and fallback default use new names
  - generate_agent_definition fallback uses new names
  - Pipeline categories in pipelines.yaml use new names
  - No source code references old category names 'implementation' or 'docs' as category values
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aquarco_supervisor.agent_autoloader import (
    analyze_agent_prompt,
    generate_agent_definition,
)
from aquarco_supervisor.cli.agents import VALID_CATEGORIES


# ---------------------------------------------------------------------------
# Canonical category set
# ---------------------------------------------------------------------------

CANONICAL_CATEGORIES = {"analyze", "design", "document", "implement", "review", "test"}
OLD_CATEGORY_NAMES = {"docs", "implementation"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests → python → supervisor → repo-root


def _load_json(relpath: str) -> dict:
    return json.loads((_REPO_ROOT / relpath).read_text())


def _load_yaml(relpath: str) -> dict:
    return yaml.safe_load((_REPO_ROOT / relpath).read_text())


# ---------------------------------------------------------------------------
# VALID_CATEGORIES in cli/agents.py
# ---------------------------------------------------------------------------


class TestValidCategoriesSet:
    """Verify VALID_CATEGORIES matches the canonical set exactly."""

    def test_valid_categories_equals_canonical(self) -> None:
        assert VALID_CATEGORIES == CANONICAL_CATEGORIES

    def test_no_old_names_in_valid_categories(self) -> None:
        assert VALID_CATEGORIES.isdisjoint(OLD_CATEGORY_NAMES)


# ---------------------------------------------------------------------------
# Schema enum consistency
# ---------------------------------------------------------------------------


class TestSchemaEnums:
    """Verify JSON schema enum arrays contain exactly the canonical categories."""

    @pytest.fixture(params=[
        "config/schemas/pipeline-agent-v1.json",
        "config/schemas/agent-definition-v1.json",
    ])
    def schema_categories_enum(self, request: pytest.FixtureRequest) -> list[str]:
        doc = _load_json(request.param)
        items = doc["properties"]["spec"]["properties"]["categories"]["items"]
        return items["enum"]

    def test_schema_enum_matches_canonical(self, schema_categories_enum: list[str]) -> None:
        assert set(schema_categories_enum) == CANONICAL_CATEGORIES

    def test_schema_enum_has_no_old_names(self, schema_categories_enum: list[str]) -> None:
        assert not OLD_CATEGORY_NAMES.intersection(schema_categories_enum)


# ---------------------------------------------------------------------------
# Pipeline categories in pipelines.yaml
# ---------------------------------------------------------------------------


class TestPipelineCategories:
    """Verify pipelines.yaml category names match canonical set."""

    @pytest.fixture()
    def pipeline_category_names(self) -> set[str]:
        doc = _load_yaml("config/pipelines.yaml")
        return {c["name"] for c in doc["categories"]}

    def test_pipeline_categories_match_canonical(self, pipeline_category_names: set[str]) -> None:
        assert pipeline_category_names == CANONICAL_CATEGORIES

    def test_no_old_names_in_pipelines(self, pipeline_category_names: set[str]) -> None:
        assert pipeline_category_names.isdisjoint(OLD_CATEGORY_NAMES)


# ---------------------------------------------------------------------------
# Autoloader category inference
# ---------------------------------------------------------------------------


class TestAutoloaderCategoryInference:
    """Verify autoloader maps content to new category names only."""

    def test_docs_content_returns_document(self) -> None:
        """Content about documentation returns 'document', not 'docs'."""
        result = analyze_agent_prompt(
            "This agent maintains documentation and README files.", "docs-writer.md"
        )
        assert result["category"] == "document"

    def test_implementation_content_returns_implement(self) -> None:
        """Content about implementation returns 'implement', not 'implementation'."""
        result = analyze_agent_prompt(
            "This agent implements new features and develops code.", "coder.md"
        )
        assert result["category"] == "implement"

    def test_fallback_default_is_implement(self) -> None:
        """Unrecognized content falls back to 'implement'."""
        result = analyze_agent_prompt(
            "A mysterious agent with no category hints.", "mystery.md"
        )
        assert result["category"] == "implement"

    def test_changelog_content_returns_document(self) -> None:
        """Content mentioning changelog maps to 'document'."""
        result = analyze_agent_prompt("Maintains the CHANGELOG.", "changelog.md")
        assert result["category"] == "document"

    @pytest.mark.parametrize("content,expected", [
        ("Analyzes and triages issues", "analyze"),
        ("Designs system architecture", "design"),
        ("Runs unit tests and e2e specs", "test"),
        ("Performs quality reviews and linting", "review"),
        ("Writes documentation and README", "document"),
        ("Implements features and writes code", "implement"),
    ])
    def test_all_categories_reachable(self, content: str, expected: str) -> None:
        """Every canonical category is reachable through content hints."""
        result = analyze_agent_prompt(content, "agent.md")
        assert result["category"] == expected

    def test_no_inferred_category_is_old_name(self) -> None:
        """None of the hint mappings produce old category names."""
        test_contents = [
            "documentation and docs",
            "implement code",
            "build features",
            "write code",
            "changelog maintenance",
            "readme updates",
            "",  # empty → default
        ]
        for content in test_contents:
            result = analyze_agent_prompt(content, "agent.md")
            assert result["category"] not in OLD_CATEGORY_NAMES, (
                f"Content {content!r} produced old category {result['category']!r}"
            )


# ---------------------------------------------------------------------------
# generate_agent_definition fallback
# ---------------------------------------------------------------------------


class TestGenerateAgentDefinition:
    """Verify generate_agent_definition uses new category names."""

    def test_default_category_when_missing(self) -> None:
        """When analysis has no 'category' key, default is 'implement'."""
        analysis = {"name": "test-agent", "description": "desc", "tools": ["Read"]}
        defn = generate_agent_definition(analysis, "repo", "content")
        assert defn["spec"]["categories"] == ["implement"]

    def test_explicit_category_preserved(self) -> None:
        """When analysis specifies a category, it is preserved."""
        analysis = {
            "name": "doc-agent",
            "description": "docs",
            "category": "document",
            "tools": ["Read"],
        }
        defn = generate_agent_definition(analysis, "repo", "content")
        assert defn["spec"]["categories"] == ["document"]

    def test_definition_never_contains_old_names(self) -> None:
        """Generated definitions should never reference old category names."""
        for cat in CANONICAL_CATEGORIES:
            analysis = {
                "name": f"{cat}-agent",
                "description": f"{cat} agent",
                "category": cat,
                "tools": ["Read"],
            }
            defn = generate_agent_definition(analysis, "repo", "content")
            cats = defn["spec"]["categories"]
            assert not OLD_CATEGORY_NAMES.intersection(cats)


# ---------------------------------------------------------------------------
# Agent definition YAML files
# ---------------------------------------------------------------------------


class TestAgentDefinitionFiles:
    """Verify agent YAML definitions use only canonical category names."""

    @pytest.fixture()
    def pipeline_agent_files(self) -> list[Path]:
        agents_dir = _REPO_ROOT / "config" / "agents" / "definitions" / "pipeline"
        return list(agents_dir.glob("*.yaml")) + list(agents_dir.glob("*.yml"))

    def test_all_agent_categories_are_canonical(self, pipeline_agent_files: list[Path]) -> None:
        """Every pipeline agent's categories list uses only new names."""
        for agent_file in pipeline_agent_files:
            doc = yaml.safe_load(agent_file.read_text())
            categories = doc.get("spec", {}).get("categories", [])
            for cat in categories:
                assert cat in CANONICAL_CATEGORIES, (
                    f"{agent_file.name}: category {cat!r} is not a canonical name"
                )

    def test_no_agent_uses_old_category_names(self, pipeline_agent_files: list[Path]) -> None:
        """No pipeline agent uses the deprecated 'docs' or 'implementation' names."""
        for agent_file in pipeline_agent_files:
            doc = yaml.safe_load(agent_file.read_text())
            categories = set(doc.get("spec", {}).get("categories", []))
            assert categories.isdisjoint(OLD_CATEGORY_NAMES), (
                f"{agent_file.name}: still uses old category names {categories & OLD_CATEGORY_NAMES}"
            )

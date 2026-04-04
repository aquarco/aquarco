"""Tests for category rename consistency (docs→document, implementation→implement).

Validates that all components agree on the canonical category names after the
refactoring in commits e9bbd9a and 46870e2a.

Acceptance criteria from review:
  - Schema enums in pipeline-agent-v1.json and agent-definition-v1.json use new names
  - VALID_CATEGORIES in cli/agents.py uses new names
  - Pipeline categories in pipelines.yaml use new names
  - No source code references old category names 'implementation' or 'docs' as category values
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

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
        # Flat frontmatter schema — categories is a top-level property
        if "categories" in doc.get("properties", {}):
            items = doc["properties"]["categories"]["items"]
        elif "oneOf" in doc:
            # agent-definition-v1.json uses oneOf; extract from the categories branch
            cats_branch = [b for b in doc["oneOf"] if "categories" in b.get("required", [])][0]
            items = cats_branch["properties"]["categories"]["items"]
        else:
            raise KeyError("Cannot find categories enum in schema")
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
# Agent definition YAML files
# ---------------------------------------------------------------------------


class TestAgentDefinitionFiles:
    """Verify agent hybrid .md definitions use only canonical category names."""

    @pytest.fixture()
    def pipeline_agent_files(self) -> list[Path]:
        agents_dir = _REPO_ROOT / "config" / "agents" / "definitions" / "pipeline"
        return list(agents_dir.glob("*.md"))

    def test_all_agent_categories_are_canonical(self, pipeline_agent_files: list[Path]) -> None:
        """Every pipeline agent's categories list uses only new names."""
        from aquarco_supervisor.pipeline.agent_registry import _parse_md_agent_file

        for agent_file in pipeline_agent_files:
            frontmatter, _ = _parse_md_agent_file(agent_file)
            categories = frontmatter.get("categories", [])
            for cat in categories:
                assert cat in CANONICAL_CATEGORIES, (
                    f"{agent_file.name}: category {cat!r} is not a canonical name"
                )

    def test_no_agent_uses_old_category_names(self, pipeline_agent_files: list[Path]) -> None:
        """No pipeline agent uses the deprecated 'docs' or 'implementation' names."""
        from aquarco_supervisor.pipeline.agent_registry import _parse_md_agent_file

        for agent_file in pipeline_agent_files:
            frontmatter, _ = _parse_md_agent_file(agent_file)
            categories = set(frontmatter.get("categories", []))
            assert categories.isdisjoint(OLD_CATEGORY_NAMES), (
                f"{agent_file.name}: still uses old category names {categories & OLD_CATEGORY_NAMES}"
            )

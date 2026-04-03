"""Extended tests for category rename (docs→document, implementation→implement).

Covers gaps not addressed by test_category_rename_consistency.py:
  - Pipeline *stage* category references (not just top-level category definitions)
  - CLI validate_definition rejects old category names
  - _infer_category edge cases (name-based matching, security→review, mixed content)
  - AGENT_MODE env vars divergence detection in agent YAML files
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from aquarco_supervisor.agent_autoloader import analyze_agent_prompt
from aquarco_supervisor.cli.agents import VALID_CATEGORIES, validate_definition

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CANONICAL_CATEGORIES = {"analyze", "design", "document", "implement", "review", "test"}
OLD_CATEGORY_NAMES = {"docs", "implementation"}

_REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_md(
    tmp_path: Path,
    *,
    categories: list[str] | None = None,
    name: str = "test-agent",
) -> Path:
    """Create a minimal valid hybrid .md agent definition for testing."""
    if categories is None:
        categories = ["implement"]

    cats_yaml = "\n".join(f"  - {c}" for c in categories)
    content = (
        "---\n"
        f"name: {name}\n"
        'version: "1.0.0"\n'
        'description: "A test agent for validation testing"\n'
        f"categories:\n{cats_yaml}\n"
        "---\n"
        "# Agent prompt\n\n"
        "Agent prompt content here.\n"
    )

    agent_file = tmp_path / f"{name}.md"
    agent_file.write_text(content)
    return agent_file


# ---------------------------------------------------------------------------
# Pipeline stage category references
# ---------------------------------------------------------------------------


class TestPipelineStageCategories:
    """Verify that every stage in every pipeline uses a canonical category."""

    @pytest.fixture()
    def pipelines_doc(self) -> dict:
        return yaml.safe_load((_REPO_ROOT / "config" / "pipelines.yaml").read_text())

    def test_all_stage_categories_are_canonical(self, pipelines_doc: dict) -> None:
        """Every stage.category in pipelines.yaml must be a canonical name."""
        pipelines = pipelines_doc.get("pipelines", [])
        for pipeline_def in pipelines:
            pipeline_name = pipeline_def.get("name", "<unnamed>")
            stages = pipeline_def.get("stages", [])
            for idx, stage in enumerate(stages):
                cat = stage.get("category")
                assert cat in CANONICAL_CATEGORIES, (
                    f"Pipeline '{pipeline_name}' stage {idx}: "
                    f"category '{cat}' is not canonical"
                )

    def test_no_stage_uses_old_names(self, pipelines_doc: dict) -> None:
        """No pipeline stage references deprecated category names."""
        pipelines = pipelines_doc.get("pipelines", [])
        for pipeline_def in pipelines:
            pipeline_name = pipeline_def.get("name", "<unnamed>")
            stages = pipeline_def.get("stages", [])
            for idx, stage in enumerate(stages):
                cat = stage.get("category")
                assert cat not in OLD_CATEGORY_NAMES, (
                    f"Pipeline '{pipeline_name}' stage {idx}: "
                    f"still uses old name '{cat}'"
                )

    def test_pipeline_category_defs_cover_all_stage_categories(self, pipelines_doc: dict) -> None:
        """Every category referenced in stages has a top-level definition."""
        defined = {c["name"] for c in pipelines_doc.get("categories", [])}
        used: set[str] = set()
        for pipeline_def in pipelines_doc.get("pipelines", []):
            for stage in pipeline_def.get("stages", []):
                used.add(stage["category"])
        assert used <= defined, f"Undefined categories used in stages: {used - defined}"


# ---------------------------------------------------------------------------
# CLI validate_definition rejects old category names
# ---------------------------------------------------------------------------


class TestValidateDefinitionCategories:
    """Verify validate_definition flags old category names as invalid."""

    def test_old_name_docs_rejected(self, tmp_path: Path) -> None:
        """Category 'docs' should produce a validation error."""
        agent_file = _make_agent_md(tmp_path, categories=["docs"])
        errors, record = validate_definition(agent_file)
        assert record is None
        cat_errors = [e for e in errors if "categories" in e.field]
        assert len(cat_errors) == 1
        assert "docs" in cat_errors[0].message

    def test_old_name_implementation_rejected(self, tmp_path: Path) -> None:
        """Category 'implementation' should produce a validation error."""
        agent_file = _make_agent_md(tmp_path, categories=["implementation"])
        errors, record = validate_definition(agent_file)
        assert record is None
        cat_errors = [e for e in errors if "categories" in e.field]
        assert len(cat_errors) == 1
        assert "implementation" in cat_errors[0].message

    def test_new_canonical_names_accepted(self, tmp_path: Path) -> None:
        """All canonical category names pass validation."""
        for cat in CANONICAL_CATEGORIES:
            agent_file = _make_agent_md(
                tmp_path, categories=[cat], name=f"agent-{cat}"
            )
            errors, record = validate_definition(agent_file)
            cat_errors = [e for e in errors if "categories" in e.field]
            assert not cat_errors, (
                f"Canonical category '{cat}' unexpectedly rejected: {cat_errors}"
            )

    def test_multiple_old_names_all_flagged(self, tmp_path: Path) -> None:
        """Using both old names at once produces two errors."""
        agent_file = _make_agent_md(
            tmp_path, categories=["docs", "implementation"]
        )
        errors, record = validate_definition(agent_file)
        assert record is None
        cat_errors = [e for e in errors if "categories" in e.field]
        assert len(cat_errors) == 2

    def test_mixed_old_and_new_rejects_old(self, tmp_path: Path) -> None:
        """Mixing a valid and an old category name still flags the old one."""
        agent_file = _make_agent_md(
            tmp_path, categories=["review", "docs"]
        )
        errors, record = validate_definition(agent_file)
        assert record is None
        cat_errors = [e for e in errors if "categories" in e.field]
        assert len(cat_errors) == 1
        assert "docs" in cat_errors[0].message


# ---------------------------------------------------------------------------
# _infer_category edge cases
# ---------------------------------------------------------------------------


class TestInferCategoryEdgeCases:
    """Edge cases in _infer_category not covered by consistency tests."""

    def test_security_content_maps_to_review(self) -> None:
        """Security-focused content should map to 'review', not a separate category."""
        result = analyze_agent_prompt(
            "Performs OWASP vulnerability scanning and security checks.",
            "security-scanner.md",
        )
        assert result["category"] == "review"

    def test_name_based_matching_analyze(self) -> None:
        """Category can be inferred from agent filename alone."""
        result = analyze_agent_prompt(
            "This agent does things.",  # No content hints
            "analyzer-bot.md",
        )
        assert result["category"] == "analyze"

    def test_name_based_matching_test(self) -> None:
        """Agent named with 'test' in filename maps to test."""
        result = analyze_agent_prompt("Generic agent.", "test-runner.md")
        assert result["category"] == "test"

    def test_name_based_matching_document(self) -> None:
        """Agent named with 'document' in filename maps to document."""
        result = analyze_agent_prompt("Generic agent.", "document-writer.md")
        assert result["category"] == "document"

    def test_empty_content_falls_back_to_implement(self) -> None:
        """Empty content with no name hints returns 'implement'."""
        result = analyze_agent_prompt("", "generic.md")
        assert result["category"] == "implement"

    def test_first_matching_hint_wins(self) -> None:
        """When content matches multiple categories, first in dict order wins."""
        # 'analyze' hints are checked before 'implement' hints
        result = analyze_agent_prompt(
            "Analyzes code then implements changes.", "multi.md"
        )
        assert result["category"] == "analyze"

    def test_playwright_maps_to_test(self) -> None:
        """Content mentioning Playwright maps to test category."""
        result = analyze_agent_prompt(
            "Runs Playwright end-to-end browser tests.", "e2e.md"
        )
        assert result["category"] == "test"

    def test_qa_lint_maps_to_review(self) -> None:
        """Quality assurance and linting content maps to review."""
        result = analyze_agent_prompt(
            "Performs QA checks and linting on pull requests.", "qa.md"
        )
        assert result["category"] == "review"

    def test_architect_maps_to_design(self) -> None:
        """Architecture-related content maps to design."""
        result = analyze_agent_prompt(
            "Architects the system and creates design plans.", "architect.md"
        )
        assert result["category"] == "design"

    def test_case_insensitive_matching(self) -> None:
        """Category matching is case-insensitive."""
        result = analyze_agent_prompt(
            "RUNS PLAYWRIGHT E2E TESTS", "TEST-AGENT.md"
        )
        assert result["category"] == "test"


# ---------------------------------------------------------------------------
# generate_agent_definition structure
# ---------------------------------------------------------------------------


class TestGenerateAgentDefinitionStructure:
    """Verify generate_agent_definition outputs correct API structure."""

    def test_api_version_present(self) -> None:
        from aquarco_supervisor.agent_autoloader import generate_agent_definition

        analysis = {"name": "x", "description": "desc", "tools": ["Read"]}
        defn = generate_agent_definition(analysis, "repo", "content")
        assert defn["apiVersion"] == "aquarco.agents/v1"

    def test_kind_is_agent_definition(self) -> None:
        from aquarco_supervisor.agent_autoloader import generate_agent_definition

        analysis = {"name": "x", "description": "desc", "tools": ["Read"]}
        defn = generate_agent_definition(analysis, "repo", "content")
        assert defn["kind"] == "AgentDefinition"

    def test_metadata_name_includes_repo(self) -> None:
        from aquarco_supervisor.agent_autoloader import generate_agent_definition

        analysis = {"name": "my-agent", "description": "desc", "tools": ["Read"]}
        defn = generate_agent_definition(analysis, "my-repo", "content")
        assert defn["metadata"]["name"] == "my-repo-my-agent"

    def test_autoloaded_label_set(self) -> None:
        from aquarco_supervisor.agent_autoloader import generate_agent_definition

        analysis = {"name": "x", "description": "desc", "tools": ["Read"]}
        defn = generate_agent_definition(analysis, "repo", "content")
        assert defn["metadata"]["labels"]["source"] == "autoloaded"


# ---------------------------------------------------------------------------
# Schema cross-validation
# ---------------------------------------------------------------------------


class TestSchemaCrossValidation:
    """Verify both JSON schemas define identical category enums."""

    def test_pipeline_and_agent_schemas_have_same_categories(self) -> None:
        pipeline_schema = json.loads(
            (_REPO_ROOT / "config" / "schemas" / "pipeline-agent-v1.json").read_text()
        )
        agent_schema = json.loads(
            (_REPO_ROOT / "config" / "schemas" / "agent-definition-v1.json").read_text()
        )

        pipeline_enum = set(
            pipeline_schema["properties"]["categories"]["items"]["enum"]
        )
        # agent-definition-v1 uses oneOf; extract categories from the first branch
        agent_cats_branch = [
            branch for branch in agent_schema["oneOf"]
            if "categories" in branch.get("required", [])
        ][0]
        agent_enum = set(
            agent_cats_branch["properties"]["categories"]["items"]["enum"]
        )
        assert pipeline_enum == agent_enum

    def test_schemas_match_valid_categories_constant(self) -> None:
        """JSON schema enums must stay in sync with VALID_CATEGORIES in CLI."""
        pipeline_schema = json.loads(
            (_REPO_ROOT / "config" / "schemas" / "pipeline-agent-v1.json").read_text()
        )
        schema_cats = set(
            pipeline_schema["properties"]["categories"]["items"]["enum"]
        )
        assert schema_cats == VALID_CATEGORIES


# ---------------------------------------------------------------------------
# AGENT_MODE env var divergence detection
# ---------------------------------------------------------------------------


class TestAgentModeEnvVar:
    """Detect if AGENT_MODE env vars diverge from canonical category names.

    This is documented as an 'info' finding by the review agent. These tests
    serve as a canary: if AGENT_MODE needs to match categories, update the
    YAML files and these expectations.
    """

    @pytest.fixture()
    def agent_env_vars(self) -> dict[str, str]:
        """Load AGENT_MODE from all pipeline agent .md files."""
        from aquarco_supervisor.pipeline.agent_registry import _parse_md_agent_file

        agents_dir = _REPO_ROOT / "config" / "agents" / "definitions" / "pipeline"
        result: dict[str, str] = {}
        for f in sorted(agents_dir.glob("*.md")):
            frontmatter, _ = _parse_md_agent_file(f)
            env = frontmatter.get("environment", {})
            if "AGENT_MODE" in env:
                result[f.stem] = env["AGENT_MODE"]
        return result

    def test_agent_mode_values_documented(self, agent_env_vars: dict[str, str]) -> None:
        """Confirm we know which agents set AGENT_MODE and their values.

        If this test fails, a new agent started setting AGENT_MODE —
        verify it uses a canonical category name.
        """
        assert "implementation-agent" in agent_env_vars
        assert "docs-agent" in agent_env_vars

    def test_implementation_agent_mode_value(self, agent_env_vars: dict[str, str]) -> None:
        """AGENT_MODE for implementation-agent is currently 'implementation'.

        This is a known divergence from the canonical category 'implement'.
        This test documents the current state; update when the env var is fixed.
        """
        assert agent_env_vars["implementation-agent"] == "implementation"

    def test_docs_agent_mode_value(self, agent_env_vars: dict[str, str]) -> None:
        """AGENT_MODE for docs-agent is currently 'docs'.

        This is a known divergence from the canonical category 'document'.
        This test documents the current state; update when the env var is fixed.
        """
        assert agent_env_vars["docs-agent"] == "docs"

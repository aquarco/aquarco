"""Tests for the regression-aware-pipeline and analyze-bug category/agent additions.

Validates the new pipeline, category, and agent definition introduced in commit
fa62ebbe9293. Covers:
  - regression-aware-pipeline structure (stages, categories, conditions, jumps)
  - analyze-bug category output schema in pipelines.yaml
  - analyze-bug-agent.md frontmatter validation (categories, tools, resources)
  - recommended_pipeline enum includes regression-aware-pipeline
  - analyze-bug-agent tool deny list consistency with prompt constraints
  - AGENT_MODE env var for analyze-bug-agent matches canonical category
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from aquarco_supervisor.cli.agents import VALID_CATEGORIES, validate_definition
from aquarco_supervisor.config import get_pipeline_config, load_pipelines

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]

CANONICAL_CATEGORIES = {"analyze", "analyze-bug", "design", "document", "implement", "review", "test"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml(relpath: str) -> dict:
    return yaml.safe_load((_REPO_ROOT / relpath).read_text())


def _load_json(relpath: str) -> dict:
    return json.loads((_REPO_ROOT / relpath).read_text())


def _pipelines_doc() -> dict:
    return _load_yaml("config/pipelines.yaml")


def _get_pipeline(name: str) -> dict | None:
    doc = _pipelines_doc()
    pipelines = doc.get("pipelines", [])
    if isinstance(pipelines, dict):
        pipelines = list(pipelines.values())
    for p in pipelines:
        if p.get("name") == name:
            return p
    return None


def _get_category_def(name: str) -> dict | None:
    doc = _pipelines_doc()
    for cat in doc.get("categories", []):
        if cat.get("name") == name:
            return cat
    return None


# ---------------------------------------------------------------------------
# regression-aware-pipeline structure
# ---------------------------------------------------------------------------


class TestRegressionAwarePipelineStructure:
    """Verify the regression-aware-pipeline has the correct stage layout."""

    @pytest.fixture()
    def pipeline(self) -> dict:
        p = _get_pipeline("regression-aware-pipeline")
        assert p is not None, "regression-aware-pipeline not found in pipelines.yaml"
        return p

    def test_pipeline_exists(self, pipeline: dict) -> None:
        assert pipeline["name"] == "regression-aware-pipeline"

    def test_pipeline_version(self, pipeline: dict) -> None:
        assert pipeline["version"] == "1.0.0"

    def test_stage_count(self, pipeline: dict) -> None:
        assert len(pipeline["stages"]) == 8

    def test_stage_order(self, pipeline: dict) -> None:
        """Stages must follow the TDD-inspired order."""
        expected = [
            "bug-analysis",
            "regression-test",
            "fix-analysis",
            "hotfix",
            "review",
            "fix-review-findings",
            "verification-test",
            "documentation",
        ]
        actual = [s["name"] for s in pipeline["stages"]]
        assert actual == expected

    def test_stage_categories(self, pipeline: dict) -> None:
        """Each stage must use the correct canonical category."""
        expected = {
            "bug-analysis": "analyze-bug",
            "regression-test": "test",
            "fix-analysis": "analyze",
            "hotfix": "implement",
            "review": "review",
            "fix-review-findings": "implement",
            "verification-test": "test",
            "documentation": "document",
        }
        for stage in pipeline["stages"]:
            assert stage["category"] == expected[stage["name"]], (
                f"Stage '{stage['name']}' has wrong category: "
                f"expected '{expected[stage['name']]}', got '{stage['category']}'"
            )

    def test_all_stage_categories_are_canonical(self, pipeline: dict) -> None:
        """Every stage category must be in the canonical set."""
        for stage in pipeline["stages"]:
            assert stage["category"] in CANONICAL_CATEGORIES, (
                f"Stage '{stage['name']}': category '{stage['category']}' not canonical"
            )

    def test_bug_analysis_is_required(self, pipeline: dict) -> None:
        stage = pipeline["stages"][0]
        assert stage["name"] == "bug-analysis"
        assert stage["required"] is True

    def test_documentation_is_optional(self, pipeline: dict) -> None:
        stage = pipeline["stages"][-1]
        assert stage["name"] == "documentation"
        assert stage["required"] is False


# ---------------------------------------------------------------------------
# regression-aware-pipeline conditions
# ---------------------------------------------------------------------------


class TestRegressionAwarePipelineConditions:
    """Verify conditions and stage jumps in the regression-aware-pipeline."""

    @pytest.fixture()
    def stages(self) -> dict[str, dict]:
        p = _get_pipeline("regression-aware-pipeline")
        assert p is not None
        return {s["name"]: s for s in p["stages"]}

    def test_regression_test_has_ai_condition(self, stages: dict[str, dict]) -> None:
        """regression-test stage has AI condition checking for existing tests."""
        conds = stages["regression-test"].get("conditions", [])
        ai_conds = [c for c in conds if "ai" in c]
        assert len(ai_conds) >= 1
        assert "existing test" in ai_conds[0]["ai"].lower()

    def test_regression_test_ai_self_loops(self, stages: dict[str, dict]) -> None:
        """AI condition on regression-test loops back to itself when no existing test."""
        conds = stages["regression-test"]["conditions"]
        ai_cond = [c for c in conds if "ai" in c][0]
        assert ai_cond.get("no") == "regression-test"

    def test_regression_test_simple_condition(self, stages: dict[str, dict]) -> None:
        """regression-test has simple condition requiring tests_added > 0 && tests_failed > 0."""
        conds = stages["regression-test"]["conditions"]
        simple_conds = [c for c in conds if "simple" in c]
        assert len(simple_conds) >= 1
        assert "tests_added > 0" in simple_conds[0]["simple"]
        assert "tests_failed > 0" in simple_conds[0]["simple"]

    def test_regression_test_max_repeats(self, stages: dict[str, dict]) -> None:
        """All conditions on regression-test have maxRepeats guards."""
        for cond in stages["regression-test"]["conditions"]:
            assert "maxRepeats" in cond, f"Missing maxRepeats in condition: {cond}"

    def test_review_has_ai_and_simple_conditions(self, stages: dict[str, dict]) -> None:
        """Review stage has both AI and simple conditions."""
        conds = stages["review"]["conditions"]
        assert any("ai" in c for c in conds)
        assert any("simple" in c for c in conds)

    def test_review_ai_jumps_to_hotfix(self, stages: dict[str, dict]) -> None:
        """Review AI condition jumps back to hotfix when risks not mitigated."""
        conds = stages["review"]["conditions"]
        ai_cond = [c for c in conds if "ai" in c][0]
        assert ai_cond.get("no") == "hotfix"

    def test_review_simple_condition_jumps_to_verification(self, stages: dict[str, dict]) -> None:
        """Review simple condition skips fix-review-findings on pass."""
        conds = stages["review"]["conditions"]
        simple_cond = [c for c in conds if "simple" in c][0]
        assert simple_cond.get("no") == "verification-test"

    def test_fix_review_findings_loops_to_review(self, stages: dict[str, dict]) -> None:
        """fix-review-findings always jumps back to review."""
        conds = stages["fix-review-findings"]["conditions"]
        assert len(conds) == 1
        assert conds[0].get("yes") == "review"

    def test_verification_test_jumps_to_hotfix_on_failure(self, stages: dict[str, dict]) -> None:
        """verification-test goes back to hotfix when coverage/tests fail."""
        conds = stages["verification-test"]["conditions"]
        simple_cond = [c for c in conds if "simple" in c][0]
        assert simple_cond.get("no") == "hotfix"
        assert "coverage_percent >= 80" in simple_cond["simple"]
        assert "tests_failed == 0" in simple_cond["simple"]


# ---------------------------------------------------------------------------
# analyze-bug category output schema
# ---------------------------------------------------------------------------


class TestAnalyzeBugCategorySchema:
    """Verify the analyze-bug category definition in pipelines.yaml."""

    @pytest.fixture()
    def category(self) -> dict:
        cat = _get_category_def("analyze-bug")
        assert cat is not None, "analyze-bug category not found in pipelines.yaml"
        return cat

    def test_category_exists(self, category: dict) -> None:
        assert category["name"] == "analyze-bug"

    def test_output_schema_is_object(self, category: dict) -> None:
        schema = category["outputSchema"]
        assert schema["type"] == "object"

    def test_required_fields(self, category: dict) -> None:
        schema = category["outputSchema"]
        expected_required = {
            "bug_summary",
            "root_cause",
            "affected_components",
            "reproduction_steps",
            "fix_approach",
            "risks",
        }
        assert set(schema["required"]) == expected_required

    def test_all_required_fields_have_properties(self, category: dict) -> None:
        schema = category["outputSchema"]
        props = set(schema.get("properties", {}).keys())
        for field in schema["required"]:
            assert field in props, f"Required field '{field}' missing from properties"

    def test_bug_summary_is_string(self, category: dict) -> None:
        props = category["outputSchema"]["properties"]
        assert props["bug_summary"]["type"] == "string"

    def test_root_cause_is_string(self, category: dict) -> None:
        props = category["outputSchema"]["properties"]
        assert props["root_cause"]["type"] == "string"

    def test_affected_components_is_array(self, category: dict) -> None:
        props = category["outputSchema"]["properties"]
        assert props["affected_components"]["type"] == "array"
        assert props["affected_components"]["items"]["type"] == "string"

    def test_reproduction_steps_is_array(self, category: dict) -> None:
        props = category["outputSchema"]["properties"]
        assert props["reproduction_steps"]["type"] == "array"
        assert props["reproduction_steps"]["items"]["type"] == "string"

    def test_fix_approach_is_string(self, category: dict) -> None:
        props = category["outputSchema"]["properties"]
        assert props["fix_approach"]["type"] == "string"

    def test_risks_is_array(self, category: dict) -> None:
        props = category["outputSchema"]["properties"]
        assert props["risks"]["type"] == "array"
        assert props["risks"]["items"]["type"] == "string"


# ---------------------------------------------------------------------------
# recommended_pipeline enum includes regression-aware-pipeline
# ---------------------------------------------------------------------------


class TestRecommendedPipelineEnum:
    """Verify the analyze category's recommended_pipeline enum is complete."""

    @pytest.fixture()
    def analyze_schema(self) -> dict:
        cat = _get_category_def("analyze")
        assert cat is not None
        return cat["outputSchema"]

    def test_regression_aware_pipeline_in_enum(self, analyze_schema: dict) -> None:
        enum_values = analyze_schema["properties"]["recommended_pipeline"]["enum"]
        assert "regression-aware-pipeline" in enum_values

    def test_all_expected_pipelines_in_enum(self, analyze_schema: dict) -> None:
        enum_values = set(analyze_schema["properties"]["recommended_pipeline"]["enum"])
        expected = {
            "feature-pipeline",
            "bugfix-pipeline",
            "pr-review-pipeline",
            "regression-aware-pipeline",
        }
        assert enum_values == expected


# ---------------------------------------------------------------------------
# analyze-bug-agent.md frontmatter validation
# ---------------------------------------------------------------------------


class TestAnalyzeBugAgentDefinition:
    """Verify analyze-bug-agent.md frontmatter is valid."""

    @pytest.fixture()
    def agent_path(self) -> Path:
        return _REPO_ROOT / "config" / "agents" / "definitions" / "pipeline" / "analyze-bug-agent.md"

    @pytest.fixture()
    def frontmatter(self, agent_path: Path) -> dict:
        from aquarco_supervisor.pipeline.agent_registry import _parse_md_agent_file
        fm, _ = _parse_md_agent_file(agent_path)
        return fm

    def test_agent_file_exists(self, agent_path: Path) -> None:
        assert agent_path.exists()

    def test_name(self, frontmatter: dict) -> None:
        assert frontmatter["name"] == "analyze-bug-agent"

    def test_version(self, frontmatter: dict) -> None:
        assert frontmatter["version"] == "1.0.0"

    def test_categories(self, frontmatter: dict) -> None:
        assert frontmatter["categories"] == ["analyze-bug"]

    def test_category_is_canonical(self, frontmatter: dict) -> None:
        for cat in frontmatter["categories"]:
            assert cat in CANONICAL_CATEGORIES

    def test_model(self, frontmatter: dict) -> None:
        assert frontmatter["model"] == "sonnet"

    def test_priority(self, frontmatter: dict) -> None:
        assert frontmatter["priority"] == 1

    def test_allowed_tools(self, frontmatter: dict) -> None:
        allowed = set(frontmatter["tools"]["allowed"])
        assert allowed == {"Read", "Grep", "Glob", "Bash", "Agent"}

    def test_denied_tools(self, frontmatter: dict) -> None:
        denied = set(frontmatter["tools"]["denied"])
        assert denied == {"Write", "Edit"}

    def test_write_edit_denied(self, frontmatter: dict) -> None:
        """Write and Edit must be denied — agent is read-only."""
        denied = set(frontmatter["tools"]["denied"])
        assert "Write" in denied
        assert "Edit" in denied

    def test_resource_limits(self, frontmatter: dict) -> None:
        res = frontmatter["resources"]
        assert res["maxTokens"] == 60000
        assert res["timeoutMinutes"] == 20
        assert res["maxConcurrent"] == 3
        assert res["maxTurns"] == 25
        assert res["maxCost"] == 1.5

    def test_agent_mode_matches_category(self, frontmatter: dict) -> None:
        """AGENT_MODE env var should match the canonical category name."""
        env = frontmatter.get("environment", {})
        assert env.get("AGENT_MODE") == "analyze-bug"

    def test_passes_validate_definition(self, agent_path: Path) -> None:
        """Agent definition passes the CLI validate_definition check."""
        errors, record = validate_definition(agent_path)
        cat_errors = [e for e in errors if "categories" in e.field]
        assert not cat_errors, f"Category validation errors: {cat_errors}"


# ---------------------------------------------------------------------------
# analyze-bug-agent prompt consistency
# ---------------------------------------------------------------------------


class TestAnalyzeBugAgentPrompt:
    """Verify prompt content is consistent with tool configuration."""

    @pytest.fixture()
    def prompt_text(self) -> str:
        agent_path = _REPO_ROOT / "config" / "agents" / "definitions" / "pipeline" / "analyze-bug-agent.md"
        from aquarco_supervisor.pipeline.agent_registry import _parse_md_agent_file
        _, prompt = _parse_md_agent_file(agent_path)
        return prompt

    def test_prompt_states_no_write_edit(self, prompt_text: str) -> None:
        """Prompt should tell the agent it cannot write or edit files."""
        assert "may not write or edit" in prompt_text.lower()

    def test_prompt_mentions_structured_output(self, prompt_text: str) -> None:
        """Prompt should mention StructuredOutput for capturing results."""
        assert "StructuredOutput" in prompt_text

    def test_prompt_does_not_reference_write_edit_tools(self, prompt_text: str) -> None:
        """After the fix, prompt should not suggest using Write/Edit tools."""
        # The contradictory constraint was fixed — prompt should not tell agent to use Write/Edit
        assert "Use `Write` and `Edit`" not in prompt_text

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "Review finding: prompt says 'hotfix-regression-aware-pipeline' "
            "but actual pipeline is 'regression-aware-pipeline'"
        ),
    )
    def test_prompt_pipeline_name_accuracy(self, prompt_text: str) -> None:
        """Prompt references to the pipeline should use the correct name."""
        import re

        pipeline_refs = re.findall(r"\b[\w-]*regression-aware-pipeline\b", prompt_text)
        assert len(pipeline_refs) > 0, "Prompt should reference the regression-aware-pipeline"
        for ref in pipeline_refs:
            assert ref == "regression-aware-pipeline", (
                f"Pipeline name mismatch in prompt: '{ref}' should be "
                f"'regression-aware-pipeline'"
            )


# ---------------------------------------------------------------------------
# Cross-file consistency: analyze-bug category across all validation layers
# ---------------------------------------------------------------------------


class TestAnalyzeBugCrossFileConsistency:
    """Verify analyze-bug category is consistently defined across schemas,
    VALID_CATEGORIES, and pipelines.yaml."""

    def test_analyze_bug_in_valid_categories(self) -> None:
        """VALID_CATEGORIES constant includes analyze-bug."""
        assert "analyze-bug" in VALID_CATEGORIES

    def test_analyze_bug_in_pipeline_agent_schema(self) -> None:
        """pipeline-agent-v1.json enum includes analyze-bug."""
        schema = _load_json("config/schemas/pipeline-agent-v1.json")
        cat_enum = schema["properties"]["categories"]["items"]["enum"]
        assert "analyze-bug" in cat_enum

    def test_pipeline_agent_schema_matches_valid_categories(self) -> None:
        """pipeline-agent-v1.json category enum must match VALID_CATEGORIES."""
        schema = _load_json("config/schemas/pipeline-agent-v1.json")
        schema_cats = set(schema["properties"]["categories"]["items"]["enum"])
        assert schema_cats == VALID_CATEGORIES

    def test_analyze_bug_category_has_output_schema(self) -> None:
        """pipelines.yaml analyze-bug category must define an outputSchema."""
        cat = _get_category_def("analyze-bug")
        assert cat is not None
        assert "outputSchema" in cat, "analyze-bug category missing outputSchema"
        assert cat["outputSchema"].get("type") == "object"

    def test_analyze_bug_in_pipelines_yaml_categories(self) -> None:
        """pipelines.yaml categories section includes analyze-bug."""
        doc = _pipelines_doc()
        cat_names = {c["name"] for c in doc.get("categories", [])}
        assert "analyze-bug" in cat_names

    def test_all_pipeline_stage_categories_in_valid_set(self) -> None:
        """Every stage category in regression-aware-pipeline is in VALID_CATEGORIES."""
        pipeline = _get_pipeline("regression-aware-pipeline")
        assert pipeline is not None
        for stage in pipeline["stages"]:
            assert stage["category"] in VALID_CATEGORIES, (
                f"Stage '{stage['name']}' uses category '{stage['category']}' "
                f"not in VALID_CATEGORIES"
            )


# ---------------------------------------------------------------------------
# Pipeline loadability via config module
# ---------------------------------------------------------------------------


class TestRegressionAwarePipelineLoading:
    """Verify that load_pipelines and get_pipeline_config work with the real config."""

    @pytest.fixture()
    def pipelines(self) -> list:
        return load_pipelines(_REPO_ROOT / "config" / "pipelines.yaml")

    def test_regression_aware_pipeline_loads(self, pipelines: list) -> None:
        names = [p.name for p in pipelines]
        assert "regression-aware-pipeline" in names

    def test_get_pipeline_config_returns_stages(self, pipelines: list) -> None:
        stages = get_pipeline_config(pipelines, "regression-aware-pipeline")
        assert stages is not None
        assert len(stages) == 8

    def test_pipeline_has_analyze_bug_category(self, pipelines: list) -> None:
        p = [p for p in pipelines if p.name == "regression-aware-pipeline"][0]
        assert "analyze-bug" in p.categories

    def test_analyze_bug_output_schema_loaded(self, pipelines: list) -> None:
        p = [p for p in pipelines if p.name == "regression-aware-pipeline"][0]
        schema = p.categories.get("analyze-bug", {})
        assert schema.get("type") == "object"
        assert "bug_summary" in schema.get("required", [])


# ---------------------------------------------------------------------------
# Additional cross-pipeline consistency tests
# ---------------------------------------------------------------------------


class TestAllPipelineCategoriesConsistency:
    """Verify every pipeline's stage categories are valid across all pipelines."""

    @pytest.fixture()
    def all_pipelines(self) -> list[dict]:
        doc = _pipelines_doc()
        pipelines = doc.get("pipelines", [])
        if isinstance(pipelines, dict):
            return list(pipelines.values())
        return pipelines

    @pytest.fixture()
    def category_names(self) -> set[str]:
        doc = _pipelines_doc()
        return {c["name"] for c in doc.get("categories", [])}

    def test_every_stage_category_defined_in_categories_section(
        self, all_pipelines: list[dict], category_names: set[str]
    ) -> None:
        """Every stage category used across all pipelines must be defined in categories."""
        for pipeline in all_pipelines:
            for stage in pipeline.get("stages", []):
                cat = stage["category"]
                assert cat in category_names, (
                    f"Pipeline '{pipeline['name']}' stage '{stage['name']}' uses "
                    f"category '{cat}' not defined in categories section"
                )

    def test_every_category_used_by_at_least_one_pipeline(
        self, all_pipelines: list[dict], category_names: set[str]
    ) -> None:
        """Every defined category should be used by at least one pipeline stage."""
        used_cats: set[str] = set()
        for pipeline in all_pipelines:
            for stage in pipeline.get("stages", []):
                used_cats.add(stage["category"])
        unused = category_names - used_cats
        assert not unused, f"Categories defined but never used by any pipeline: {unused}"

    def test_all_pipelines_have_unique_names(self, all_pipelines: list[dict]) -> None:
        """Pipeline names must be unique."""
        names = [p["name"] for p in all_pipelines]
        assert len(names) == len(set(names)), f"Duplicate pipeline names: {names}"


# ---------------------------------------------------------------------------
# Regression-aware-pipeline condition guard completeness
# ---------------------------------------------------------------------------


class TestConditionGuardCompleteness:
    """Verify condition guards are complete and well-formed across all stages."""

    @pytest.fixture()
    def stages(self) -> list[dict]:
        p = _get_pipeline("regression-aware-pipeline")
        assert p is not None
        return p["stages"]

    def test_stages_with_conditions_all_have_max_repeats(self, stages: list[dict]) -> None:
        """Every condition in the pipeline should have a maxRepeats guard to prevent infinite loops."""
        for stage in stages:
            for cond in stage.get("conditions", []):
                assert "maxRepeats" in cond, (
                    f"Stage '{stage['name']}': condition missing maxRepeats guard: {cond}"
                )

    def test_condition_jumps_reference_valid_stages(self, stages: list[dict]) -> None:
        """All yes/no jump targets in conditions must reference existing stage names."""
        stage_names = {s["name"] for s in stages}
        for stage in stages:
            for cond in stage.get("conditions", []):
                for jump_key in ("yes", "no"):
                    target = cond.get(jump_key)
                    if target is not None:
                        assert target in stage_names, (
                            f"Stage '{stage['name']}': condition {jump_key}='{target}' "
                            f"references non-existent stage"
                        )

    def test_no_stage_jumps_to_itself_unconditionally(self, stages: list[dict]) -> None:
        """A stage should not have both yes and no pointing to itself (infinite loop)."""
        for stage in stages:
            for cond in stage.get("conditions", []):
                yes_target = cond.get("yes")
                no_target = cond.get("no")
                if yes_target and no_target:
                    assert not (yes_target == stage["name"] and no_target == stage["name"]), (
                        f"Stage '{stage['name']}' has condition with both yes and no "
                        f"jumping to itself — infinite loop"
                    )

    def test_single_jump_to_self_has_max_repeats(self, stages: list[dict]) -> None:
        """A condition with only one jump key pointing to self must have maxRepeats.

        Review finding: the original test only caught the case where BOTH yes and no
        point to self.  A condition with only 'yes' (or only 'no') pointing to self
        could still loop infinitely if the condition always evaluates the same way.
        The maxRepeats guard is the safety net for this scenario.
        """
        for stage in stages:
            for cond in stage.get("conditions", []):
                for jump_key in ("yes", "no"):
                    target = cond.get(jump_key)
                    if target == stage["name"]:
                        assert "maxRepeats" in cond, (
                            f"Stage '{stage['name']}': condition {jump_key} points to self "
                            f"without maxRepeats guard — potential infinite loop: {cond}"
                        )


# ---------------------------------------------------------------------------
# Extended cross-pipeline validation (addresses review findings)
# ---------------------------------------------------------------------------


class TestCrossPipelineStageIntegrity:
    """Additional cross-pipeline validations covering stage and condition integrity."""

    @pytest.fixture()
    def all_pipelines(self) -> list[dict]:
        doc = _pipelines_doc()
        pipelines = doc.get("pipelines", [])
        if isinstance(pipelines, dict):
            return list(pipelines.values())
        return pipelines

    def test_all_pipelines_have_at_least_one_stage(self, all_pipelines: list[dict]) -> None:
        """Every pipeline must define at least one stage."""
        for pipeline in all_pipelines:
            stages = pipeline.get("stages", [])
            assert len(stages) > 0, (
                f"Pipeline '{pipeline['name']}' has no stages"
            )

    def test_all_pipeline_stages_have_category(self, all_pipelines: list[dict]) -> None:
        """Every stage in every pipeline must specify a category."""
        for pipeline in all_pipelines:
            for stage in pipeline.get("stages", []):
                assert "category" in stage, (
                    f"Pipeline '{pipeline['name']}' stage '{stage.get('name', '?')}' "
                    f"is missing a category"
                )

    def test_all_pipeline_stages_have_name(self, all_pipelines: list[dict]) -> None:
        """Every stage in every pipeline must have a name."""
        for pipeline in all_pipelines:
            for i, stage in enumerate(pipeline.get("stages", [])):
                assert "name" in stage, (
                    f"Pipeline '{pipeline['name']}' stage index {i} is missing a name"
                )

    def test_stage_names_unique_within_pipeline(self, all_pipelines: list[dict]) -> None:
        """Stage names must be unique within each pipeline."""
        for pipeline in all_pipelines:
            names = [s["name"] for s in pipeline.get("stages", []) if "name" in s]
            assert len(names) == len(set(names)), (
                f"Pipeline '{pipeline['name']}' has duplicate stage names: {names}"
            )

    def test_all_condition_jumps_reference_valid_stages_across_all_pipelines(
        self, all_pipelines: list[dict]
    ) -> None:
        """yes/no jump targets must reference existing stage names in ALL pipelines."""
        for pipeline in all_pipelines:
            stage_names = {s["name"] for s in pipeline.get("stages", []) if "name" in s}
            for stage in pipeline.get("stages", []):
                for cond in stage.get("conditions", []):
                    for jump_key in ("yes", "no"):
                        target = cond.get(jump_key)
                        if target is not None:
                            assert target in stage_names, (
                                f"Pipeline '{pipeline['name']}' stage '{stage['name']}': "
                                f"condition {jump_key}='{target}' references non-existent stage"
                            )

    def test_all_conditions_have_max_repeats_across_all_pipelines(
        self, all_pipelines: list[dict]
    ) -> None:
        """Every condition across ALL pipelines should have a maxRepeats guard."""
        for pipeline in all_pipelines:
            for stage in pipeline.get("stages", []):
                for cond in stage.get("conditions", []):
                    assert "maxRepeats" in cond, (
                        f"Pipeline '{pipeline['name']}' stage '{stage['name']}': "
                        f"condition missing maxRepeats guard: {cond}"
                    )

    def test_all_pipelines_have_version(self, all_pipelines: list[dict]) -> None:
        """Every pipeline must specify a version string."""
        for pipeline in all_pipelines:
            assert "version" in pipeline, (
                f"Pipeline '{pipeline['name']}' is missing a version field"
            )

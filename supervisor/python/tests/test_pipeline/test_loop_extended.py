"""Extended tests for pipeline conditional loop functionality.

Covers gaps not addressed in test_loop.py:
- Config loading with loop parsing (load_pipelines)
- StageConfig model with loop field
- _resolve_field / _compare_complexity edge cases
- _parse_loop and _resolve_body_indices in visualize.py
- JSON schema validation of the LoopConfig definition
- Integration: load_pipelines -> get_pipeline_config with loops
- Pipeline visualization edge cases
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from aquarco_supervisor.config import get_pipeline_config, load_pipelines
from aquarco_supervisor.models import LoopConfig, PipelineConfig, StageConfig
from aquarco_supervisor.pipeline.executor import (
    _compare_complexity,
    _resolve_field,
    check_conditions,
    evaluate_loop_condition,
    resolve_loop_stages,
)
from aquarco_supervisor.pipeline.visualize import (
    _parse_loop,
    _resolve_body_indices,
    format_pipeline_stages,
    format_pipeline_stages_markdown,
)


# ---------------------------------------------------------------------------
# StageConfig model with loop field
# ---------------------------------------------------------------------------


class TestStageConfigWithLoop:
    """Test StageConfig Pydantic model when loop is present."""

    def test_stage_without_loop(self) -> None:
        stage = StageConfig(category="analyze")
        assert stage.loop is None
        assert stage.required is True
        assert stage.conditions == []

    def test_stage_with_loop(self) -> None:
        loop = LoopConfig(condition="status == done")
        stage = StageConfig(category="review", loop=loop)
        assert stage.loop is not None
        assert stage.loop.condition == "status == done"

    def test_stage_with_loop_and_conditions(self) -> None:
        loop = LoopConfig(condition="recommendation == approve", max_repeats=5)
        stage = StageConfig(
            category="review",
            conditions=["recommendation == request_changes"],
            loop=loop,
        )
        assert stage.conditions == ["recommendation == request_changes"]
        assert stage.loop.max_repeats == 5

    def test_stage_model_dump_includes_loop(self) -> None:
        loop = LoopConfig(
            condition="x == y",
            loop_stages=["implementation", "review"],
        )
        stage = StageConfig(category="review", loop=loop)
        dumped = stage.model_dump()
        assert "loop" in dumped
        assert dumped["loop"]["condition"] == "x == y"
        assert dumped["loop"]["loop_stages"] == ["implementation", "review"]

    def test_stage_optional_with_loop(self) -> None:
        loop = LoopConfig(condition="done == true")
        stage = StageConfig(category="docs", required=False, loop=loop)
        assert stage.required is False
        assert stage.loop is not None


# ---------------------------------------------------------------------------
# load_pipelines with loop parsing
# ---------------------------------------------------------------------------


class TestLoadPipelinesWithLoops:
    """Test that load_pipelines correctly parses loop configurations."""

    def test_pipeline_with_loop_stage(self, tmp_path: Path) -> None:
        pipelines_yaml = {
            "pipelines": [
                {
                    "name": "test-pipeline",
                    "version": "1.0.0",
                    "trigger": {"labels": ["test"]},
                    "stages": [
                        {"category": "implementation", "required": True},
                        {
                            "category": "review",
                            "required": True,
                            "loop": {
                                "condition": "recommendation == approve",
                                "max_repeats": 3,
                                "eval_mode": "simple",
                                "loopStages": ["implementation", "review"],
                            },
                        },
                    ],
                }
            ]
        }
        pipelines_file = tmp_path / "pipelines.yaml"
        pipelines_file.write_text(yaml.dump(pipelines_yaml))

        pipelines = load_pipelines(pipelines_file)
        assert len(pipelines) == 1

        review_stage = pipelines[0].stages[1]
        assert review_stage.loop is not None
        assert review_stage.loop.condition == "recommendation == approve"
        assert review_stage.loop.max_repeats == 3
        assert review_stage.loop.eval_mode == "simple"
        assert review_stage.loop.loop_stages == ["implementation", "review"]

    def test_pipeline_without_loop(self, tmp_path: Path) -> None:
        pipelines_yaml = {
            "pipelines": [
                {
                    "name": "simple-pipeline",
                    "version": "1.0.0",
                    "trigger": {"labels": ["bug"]},
                    "stages": [
                        {"category": "analyze"},
                        {"category": "implementation"},
                    ],
                }
            ]
        }
        pipelines_file = tmp_path / "pipelines.yaml"
        pipelines_file.write_text(yaml.dump(pipelines_yaml))

        pipelines = load_pipelines(pipelines_file)
        for stage in pipelines[0].stages:
            assert stage.loop is None

    def test_pipeline_with_ai_loop(self, tmp_path: Path) -> None:
        pipelines_yaml = {
            "pipelines": [
                {
                    "name": "quality-pipeline",
                    "version": "1.0.0",
                    "trigger": {"labels": ["quality"]},
                    "stages": [
                        {"category": "implementation"},
                        {
                            "category": "review",
                            "loop": {
                                "condition": "All risks mitigated",
                                "max_repeats": 5,
                                "eval_mode": "ai",
                                "loopStages": ["implementation", "test", "review"],
                            },
                        },
                    ],
                }
            ]
        }
        pipelines_file = tmp_path / "pipelines.yaml"
        pipelines_file.write_text(yaml.dump(pipelines_yaml))

        pipelines = load_pipelines(pipelines_file)
        review_stage = pipelines[0].stages[1]
        assert review_stage.loop is not None
        assert review_stage.loop.eval_mode == "ai"
        assert review_stage.loop.loop_stages == ["implementation", "test", "review"]

    def test_pipeline_with_null_loop(self, tmp_path: Path) -> None:
        """Explicitly null loop should be treated as no loop."""
        pipelines_yaml = {
            "pipelines": [
                {
                    "name": "null-loop-pipeline",
                    "version": "1.0.0",
                    "trigger": {"labels": ["test"]},
                    "stages": [
                        {"category": "analyze", "loop": None},
                    ],
                }
            ]
        }
        pipelines_file = tmp_path / "pipelines.yaml"
        pipelines_file.write_text(yaml.dump(pipelines_yaml))

        pipelines = load_pipelines(pipelines_file)
        assert pipelines[0].stages[0].loop is None

    def test_load_actual_pipelines_file(self) -> None:
        """Integration test: load the actual config/pipelines.yaml file."""
        actual_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "config"
            / "pipelines.yaml"
        )
        if not actual_path.exists():
            pytest.skip("config/pipelines.yaml not found")

        pipelines = load_pipelines(actual_path)
        assert len(pipelines) >= 1

        # Find the feature-pipeline — it should have a loop on review
        feature = next(
            (p for p in pipelines if p.name == "feature-pipeline"), None
        )
        assert feature is not None
        review_stages = [s for s in feature.stages if s.category == "review"]
        assert any(s.loop is not None for s in review_stages)


# ---------------------------------------------------------------------------
# get_pipeline_config integration with loops
# ---------------------------------------------------------------------------


class TestGetPipelineConfigWithLoops:
    """Test that get_pipeline_config returns loop data correctly."""

    def test_returns_loop_in_stage_dict(self, tmp_path: Path) -> None:
        pipelines_yaml = {
            "pipelines": [
                {
                    "name": "loop-pipeline",
                    "version": "1.0.0",
                    "trigger": {"labels": ["test"]},
                    "stages": [
                        {"category": "implementation"},
                        {
                            "category": "review",
                            "loop": {
                                "condition": "approved == true",
                                "max_repeats": 2,
                                "eval_mode": "simple",
                            },
                        },
                    ],
                }
            ]
        }
        pipelines_file = tmp_path / "pipelines.yaml"
        pipelines_file.write_text(yaml.dump(pipelines_yaml))

        pipelines = load_pipelines(pipelines_file)
        stage_dicts = get_pipeline_config(pipelines, "loop-pipeline")
        assert stage_dicts is not None
        assert len(stage_dicts) == 2

        review_dict = stage_dicts[1]
        assert review_dict["loop"] is not None
        assert review_dict["loop"]["condition"] == "approved == true"
        assert review_dict["loop"]["max_repeats"] == 2

    def test_nonexistent_pipeline_returns_none(self) -> None:
        pipelines: list[PipelineConfig] = []
        assert get_pipeline_config(pipelines, "nonexistent") is None


# ---------------------------------------------------------------------------
# _resolve_field edge cases
# ---------------------------------------------------------------------------


class TestResolveField:
    """Test dotted field path resolution."""

    def test_simple_field(self) -> None:
        assert _resolve_field({"name": "test"}, "name") == "test"

    def test_nested_field(self) -> None:
        data = {"a": {"b": {"c": 42}}}
        assert _resolve_field(data, "a.b.c") == 42

    def test_missing_top_level(self) -> None:
        assert _resolve_field({}, "missing") is None

    def test_missing_nested(self) -> None:
        assert _resolve_field({"a": {"b": 1}}, "a.c") is None

    def test_non_dict_intermediate(self) -> None:
        """If intermediate value is not a dict, return None."""
        data = {"a": "string_value"}
        assert _resolve_field(data, "a.b") is None

    def test_numeric_value(self) -> None:
        data = {"count": 0}
        assert _resolve_field(data, "count") == 0

    def test_boolean_value(self) -> None:
        data = {"active": True}
        assert _resolve_field(data, "active") is True

    def test_list_value(self) -> None:
        data = {"items": [1, 2, 3]}
        assert _resolve_field(data, "items") == [1, 2, 3]


# ---------------------------------------------------------------------------
# _compare_complexity edge cases
# ---------------------------------------------------------------------------


class TestCompareComplexity:
    """Test complexity comparison helper."""

    def test_equal_values(self) -> None:
        assert _compare_complexity("medium", ">=", "medium") is True
        assert _compare_complexity("medium", "<=", "medium") is True
        assert _compare_complexity("medium", ">", "medium") is False
        assert _compare_complexity("medium", "<", "medium") is False

    def test_ascending_order(self) -> None:
        assert _compare_complexity("trivial", "<", "low") is True
        assert _compare_complexity("low", "<", "medium") is True
        assert _compare_complexity("medium", "<", "high") is True
        assert _compare_complexity("high", "<", "epic") is True

    def test_case_insensitive(self) -> None:
        assert _compare_complexity("HIGH", ">=", "medium") is True
        assert _compare_complexity("Medium", ">=", "MEDIUM") is True

    def test_invalid_complexity_returns_false(self) -> None:
        assert _compare_complexity("invalid", ">=", "medium") is False
        assert _compare_complexity("medium", ">=", "invalid") is False

    def test_unsupported_operator_returns_false(self) -> None:
        assert _compare_complexity("medium", "==", "medium") is False


# ---------------------------------------------------------------------------
# _parse_loop in visualize.py
# ---------------------------------------------------------------------------


class TestParseLoop:
    """Test _parse_loop helper in visualize module."""

    def test_parse_loop_config_instance(self) -> None:
        cfg = LoopConfig(condition="x == y")
        result = _parse_loop(cfg)
        assert result is cfg

    def test_parse_loop_from_dict(self) -> None:
        data = {"condition": "status == done", "max_repeats": 2}
        result = _parse_loop(data)
        assert result is not None
        assert result.condition == "status == done"
        assert result.max_repeats == 2

    def test_parse_loop_invalid_dict(self) -> None:
        """Invalid dict should return None."""
        result = _parse_loop({"invalid_field": "no_condition"})
        assert result is None

    def test_parse_loop_none(self) -> None:
        assert _parse_loop(None) is None

    def test_parse_loop_string(self) -> None:
        assert _parse_loop("not a loop") is None

    def test_parse_loop_integer(self) -> None:
        assert _parse_loop(42) is None


# ---------------------------------------------------------------------------
# _resolve_body_indices in visualize.py
# ---------------------------------------------------------------------------


class TestResolveBodyIndices:
    """Test _resolve_body_indices helper in visualize module."""

    STAGES = [
        {"category": "analyze"},
        {"category": "design"},
        {"category": "implementation"},
        {"category": "test"},
        {"category": "review"},
    ]

    def test_single_match(self) -> None:
        result = _resolve_body_indices(["review"], self.STAGES)
        assert result == [4]

    def test_multiple_matches(self) -> None:
        result = _resolve_body_indices(
            ["implementation", "review"], self.STAGES
        )
        assert result == [2, 4]

    def test_no_match(self) -> None:
        result = _resolve_body_indices(["nonexistent"], self.STAGES)
        assert result == []

    def test_empty_loop_stages(self) -> None:
        result = _resolve_body_indices([], self.STAGES)
        assert result == []

    def test_all_stages(self) -> None:
        result = _resolve_body_indices(
            ["analyze", "design", "implementation", "test", "review"],
            self.STAGES,
        )
        assert result == [0, 1, 2, 3, 4]

    def test_empty_stage_defs(self) -> None:
        result = _resolve_body_indices(["review"], [])
        assert result == []


# ---------------------------------------------------------------------------
# check_conditions additional edge cases for loop context
# ---------------------------------------------------------------------------


class TestCheckConditionsEdgeCases:
    """Additional check_conditions edge cases relevant to loop exit conditions."""

    def test_single_word_condition_skipped(self) -> None:
        """Conditions with fewer than 3 parts are skipped (treated as pass)."""
        assert check_conditions(["incomplete"], {"x": "y"}) is True

    def test_two_word_condition_skipped(self) -> None:
        assert check_conditions(["field =="], {"field": "value"}) is True

    def test_equals_sign_operator(self) -> None:
        """Single = operator should also work."""
        output = {"status": "pass"}
        assert check_conditions(["status = pass"], output) is True

    def test_multiple_conditions_all_must_pass(self) -> None:
        output = {"a": "1", "b": "2"}
        assert check_conditions(["a == 1", "b == 2"], output) is True
        assert check_conditions(["a == 1", "b == 3"], output) is False

    def test_str_coercion(self) -> None:
        """Non-string values are coerced to string for comparison."""
        output = {"count": 42}
        assert check_conditions(["count == 42"], output) is True

    def test_boolean_coercion(self) -> None:
        output = {"flag": True}
        assert check_conditions(["flag == True"], output) is True

    def test_none_value_field_treated_as_missing(self) -> None:
        """None values are treated as missing (condition not met)."""
        output = {"field": None}
        assert check_conditions(["field == None"], output) is False


# ---------------------------------------------------------------------------
# evaluate_loop_condition edge cases
# ---------------------------------------------------------------------------


class TestEvaluateLoopConditionEdgeCases:
    """Additional edge cases for evaluate_loop_condition."""

    @pytest.mark.asyncio
    async def test_simple_inequality_not_met(self) -> None:
        loop = LoopConfig(condition="findings_count != 0", eval_mode="simple")
        output = {"findings_count": "0"}
        result = await evaluate_loop_condition(loop, output, {}, "/tmp")
        assert result is False

    @pytest.mark.asyncio
    async def test_simple_nested_field_not_met(self) -> None:
        loop = LoopConfig(
            condition="review.status == passed", eval_mode="simple"
        )
        output = {"review": {"status": "failed"}}
        result = await evaluate_loop_condition(loop, output, {}, "/tmp")
        assert result is False

    @pytest.mark.asyncio
    async def test_simple_deeply_nested(self) -> None:
        loop = LoopConfig(
            condition="a.b.c == done", eval_mode="simple"
        )
        output = {"a": {"b": {"c": "done"}}}
        result = await evaluate_loop_condition(loop, output, {}, "/tmp")
        assert result is True

    @pytest.mark.asyncio
    async def test_ai_mode_returns_non_dict(self) -> None:
        """AI eval returning non-dict structured output falls back to raw."""
        from aquarco_supervisor.cli.claude import ClaudeOutput

        loop = LoopConfig(condition="test", eval_mode="ai")

        mock_output = ClaudeOutput(
            structured="not a dict",
            raw='{"result": true, "reasoning": "ok"}',
        )

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=mock_output,
        ):
            result = await evaluate_loop_condition(
                loop, {}, {}, "/tmp"
            )
        assert result is True

    @pytest.mark.asyncio
    async def test_ai_mode_raw_false(self) -> None:
        """AI eval returning false in raw output."""
        from aquarco_supervisor.cli.claude import ClaudeOutput

        loop = LoopConfig(condition="test", eval_mode="ai")

        mock_output = ClaudeOutput(
            structured=None,
            raw='{"result": false, "reasoning": "not done"}',
        )

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=mock_output,
        ):
            result = await evaluate_loop_condition(
                loop, {}, {}, "/tmp"
            )
        assert result is False


# ---------------------------------------------------------------------------
# resolve_loop_stages edge cases
# ---------------------------------------------------------------------------


class TestResolveLoopStagesEdgeCases:
    """Additional resolve_loop_stages edge cases."""

    STAGE_DEFS: list[dict[str, Any]] = [
        {"category": "analyze"},
        {"category": "implementation"},
        {"category": "review"},
    ]

    def test_duplicate_categories_in_stage_defs(self) -> None:
        """If multiple stages share a category, all matching indices returned."""
        defs = [
            {"category": "implementation"},
            {"category": "review"},
            {"category": "implementation"},  # duplicate
            {"category": "review"},  # duplicate
        ]
        loop = LoopConfig(
            condition="x == y",
            loop_stages=["implementation", "review"],
        )
        result = resolve_loop_stages(loop, 3, defs)
        assert result == [0, 1, 2, 3]

    def test_current_stage_idx_out_of_range(self) -> None:
        """If current_stage_idx > len(defs), still returned for empty loops."""
        loop = LoopConfig(condition="x == y", loop_stages=[])
        result = resolve_loop_stages(loop, 99, self.STAGE_DEFS)
        assert result == [99]


# ---------------------------------------------------------------------------
# format_pipeline_stages edge cases
# ---------------------------------------------------------------------------


class TestFormatPipelineStagesEdgeCases:
    """Additional visualization edge cases."""

    def test_single_stage_no_arrows(self) -> None:
        stages = [{"category": "analyze"}]
        result = format_pipeline_stages(stages, markdown=False)
        assert "v" not in result.split("done")[0].split("[0]")[0]
        assert "[0] analyze" in result
        assert "done" in result

    def test_loop_with_empty_loop_stages(self) -> None:
        """Loop with no loopStages defaults to current category."""
        stages = [
            {
                "category": "review",
                "loop": {
                    "condition": "approved == true",
                    "max_repeats": 2,
                },
            }
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "LOOP: review" in result
        assert "max 2x" in result

    def test_invalid_loop_data_ignored(self) -> None:
        """Invalid loop data should not crash visualization."""
        stages = [
            {
                "category": "review",
                "loop": {"invalid": "data"},
            }
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "[0] review" in result
        # Should not have LOOP since parse fails
        assert "LOOP" not in result

    def test_loop_with_loop_config_object(self) -> None:
        """Loop data can be a LoopConfig instance."""
        loop = LoopConfig(
            condition="done == true",
            max_repeats=4,
            eval_mode="simple",
            loop_stages=["test"],
        )
        stages = [
            {"category": "test", "loop": loop},
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "LOOP: test" in result
        assert "max 4x" in result

    def test_visualization_preserves_stage_order(self) -> None:
        """Stages should appear in order in the visualization."""
        stages = [
            {"category": "analyze"},
            {"category": "design"},
            {"category": "implementation"},
            {"category": "test"},
            {"category": "docs"},
            {"category": "review"},
        ]
        result = format_pipeline_stages(stages, markdown=False)
        lines = result.split("\n")
        stage_lines = [l for l in lines if "[" in l and "]" in l]
        assert len(stage_lines) == 6
        # Check order
        categories = [l.split("]")[1].strip().split(" ")[0] for l in stage_lines]
        assert categories == [
            "analyze", "design", "implementation", "test", "docs", "review"
        ]


# ---------------------------------------------------------------------------
# format_pipeline_stages_markdown edge cases
# ---------------------------------------------------------------------------


class TestFormatPipelineStagesMarkdownEdgeCases:
    """Additional markdown rendering tests."""

    def test_empty_pipeline(self) -> None:
        result = format_pipeline_stages_markdown("empty", [])
        assert "## Pipeline Stages: empty" in result
        assert "(empty pipeline)" in result

    def test_pipeline_with_loop_includes_legend(self) -> None:
        stages = [
            {
                "category": "review",
                "loop": {
                    "condition": "done",
                    "max_repeats": 2,
                },
            }
        ]
        result = format_pipeline_stages_markdown("test", stages)
        assert "Legend" in result
        assert "exit when:" in result


# ---------------------------------------------------------------------------
# LoopConfig model edge cases
# ---------------------------------------------------------------------------


class TestLoopConfigEdgeCases:
    """Additional LoopConfig model tests."""

    def test_condition_required(self) -> None:
        with pytest.raises(Exception):
            LoopConfig()  # type: ignore[call-arg]

    def test_model_dump_by_alias(self) -> None:
        cfg = LoopConfig(
            condition="x == y",
            loop_stages=["review"],
        )
        dumped = cfg.model_dump(by_alias=True)
        assert "loopStages" in dumped
        assert dumped["loopStages"] == ["review"]

    def test_model_dump_by_field_name(self) -> None:
        cfg = LoopConfig(
            condition="x == y",
            loop_stages=["review"],
        )
        dumped = cfg.model_dump()
        assert "loop_stages" in dumped
        assert dumped["loop_stages"] == ["review"]

    def test_eval_mode_simple(self) -> None:
        cfg = LoopConfig(condition="x == y", eval_mode="simple")
        assert cfg.eval_mode == "simple"

    def test_eval_mode_ai(self) -> None:
        cfg = LoopConfig(condition="x == y", eval_mode="ai")
        assert cfg.eval_mode == "ai"

    def test_max_repeats_boundary_1(self) -> None:
        cfg = LoopConfig(condition="x == y", max_repeats=1)
        assert cfg.max_repeats == 1

    def test_max_repeats_boundary_10(self) -> None:
        cfg = LoopConfig(condition="x == y", max_repeats=10)
        assert cfg.max_repeats == 10


# ---------------------------------------------------------------------------
# JSON Schema validation
# ---------------------------------------------------------------------------


class TestPipelineJsonSchema:
    """Test that the JSON schema correctly validates pipeline definitions."""

    def test_schema_has_loop_config_def(self) -> None:
        schema_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "config"
            / "schemas"
            / "pipeline-definition-v1.json"
        )
        if not schema_path.exists():
            pytest.skip("Schema file not found")

        schema = json.loads(schema_path.read_text())
        assert "LoopConfig" in schema.get("$defs", {})

        loop_def = schema["$defs"]["LoopConfig"]
        assert loop_def["type"] == "object"
        assert "condition" in loop_def["required"]
        assert "max_repeats" in loop_def["properties"]
        assert "eval_mode" in loop_def["properties"]
        assert "loopStages" in loop_def["properties"]

    def test_schema_stage_has_loop_property(self) -> None:
        schema_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "config"
            / "schemas"
            / "pipeline-definition-v1.json"
        )
        if not schema_path.exists():
            pytest.skip("Schema file not found")

        schema = json.loads(schema_path.read_text())
        stage_def = schema["$defs"]["Stage"]
        assert "loop" in stage_def["properties"]
        assert stage_def["properties"]["loop"]["$ref"] == "#/$defs/LoopConfig"

    def test_schema_loop_max_repeats_bounds(self) -> None:
        schema_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "config"
            / "schemas"
            / "pipeline-definition-v1.json"
        )
        if not schema_path.exists():
            pytest.skip("Schema file not found")

        schema = json.loads(schema_path.read_text())
        max_repeats = schema["$defs"]["LoopConfig"]["properties"]["max_repeats"]
        assert max_repeats["minimum"] == 1
        assert max_repeats["maximum"] == 10
        assert max_repeats["default"] == 3

    def test_schema_eval_mode_enum(self) -> None:
        schema_path = (
            Path(__file__).parent.parent.parent.parent.parent
            / "config"
            / "schemas"
            / "pipeline-definition-v1.json"
        )
        if not schema_path.exists():
            pytest.skip("Schema file not found")

        schema = json.loads(schema_path.read_text())
        eval_mode = schema["$defs"]["LoopConfig"]["properties"]["eval_mode"]
        assert eval_mode["enum"] == ["simple", "ai"]

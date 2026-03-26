"""Tests for pipeline conditional loop functionality.

Covers: LoopConfig model, resolve_loop_stages, evaluate_loop_condition,
format_pipeline_stages, and loop integration in _execute_running_phase.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.models import LoopConfig
from aquarco_supervisor.pipeline.executor import (
    check_conditions,
    evaluate_loop_condition,
    resolve_loop_stages,
)
from aquarco_supervisor.pipeline.visualize import (
    format_pipeline_stages,
    format_pipeline_stages_markdown,
)


# ---------------------------------------------------------------------------
# LoopConfig model validation
# ---------------------------------------------------------------------------


class TestLoopConfig:
    """Test LoopConfig Pydantic model validation."""

    def test_minimal_config(self) -> None:
        cfg = LoopConfig(condition="recommendation == approve")
        assert cfg.condition == "recommendation == approve"
        assert cfg.max_repeats == 3
        assert cfg.eval_mode == "simple"
        assert cfg.loop_stages == []

    def test_full_config(self) -> None:
        cfg = LoopConfig(
            condition="All risks mitigated",
            max_repeats=5,
            eval_mode="ai",
            loop_stages=["implementation", "review"],
        )
        assert cfg.max_repeats == 5
        assert cfg.eval_mode == "ai"
        assert cfg.loop_stages == ["implementation", "review"]

    def test_camel_case_alias(self) -> None:
        """loopStages alias should work (from YAML)."""
        cfg = LoopConfig(
            condition="status == done",
            loopStages=["test", "review"],
        )
        assert cfg.loop_stages == ["test", "review"]

    def test_max_repeats_bounds(self) -> None:
        """max_repeats must be 1-10."""
        cfg = LoopConfig(condition="x == y", max_repeats=1)
        assert cfg.max_repeats == 1

        cfg = LoopConfig(condition="x == y", max_repeats=10)
        assert cfg.max_repeats == 10

    def test_max_repeats_below_minimum_raises(self) -> None:
        with pytest.raises(Exception):
            LoopConfig(condition="x == y", max_repeats=0)

    def test_max_repeats_above_maximum_raises(self) -> None:
        with pytest.raises(Exception):
            LoopConfig(condition="x == y", max_repeats=11)

    def test_invalid_eval_mode_raises(self) -> None:
        with pytest.raises(Exception):
            LoopConfig(condition="x == y", eval_mode="invalid")


# ---------------------------------------------------------------------------
# resolve_loop_stages
# ---------------------------------------------------------------------------


class TestResolveLoopStages:
    """Test resolution of loop body stage indices."""

    STAGE_DEFS: list[dict[str, Any]] = [
        {"category": "analyze"},
        {"category": "design"},
        {"category": "implementation"},
        {"category": "test"},
        {"category": "review"},
    ]

    def test_empty_loop_stages_returns_current(self) -> None:
        loop = LoopConfig(condition="x == y", loop_stages=[])
        result = resolve_loop_stages(loop, 4, self.STAGE_DEFS)
        assert result == [4]

    def test_single_loop_stage(self) -> None:
        loop = LoopConfig(condition="x == y", loop_stages=["review"])
        result = resolve_loop_stages(loop, 4, self.STAGE_DEFS)
        assert result == [4]

    def test_multiple_loop_stages(self) -> None:
        loop = LoopConfig(
            condition="x == y",
            loop_stages=["implementation", "review"],
        )
        result = resolve_loop_stages(loop, 4, self.STAGE_DEFS)
        assert result == [2, 4]

    def test_loop_stages_preserves_order(self) -> None:
        """Body indices should follow stage_defs order, not loop_stages order."""
        loop = LoopConfig(
            condition="x == y",
            loop_stages=["review", "implementation"],
        )
        result = resolve_loop_stages(loop, 4, self.STAGE_DEFS)
        # implementation (idx 2) comes before review (idx 4)
        assert result == [2, 4]

    def test_unmatched_loop_stages_returns_current(self) -> None:
        """If no categories match, fall back to current stage."""
        loop = LoopConfig(
            condition="x == y",
            loop_stages=["nonexistent"],
        )
        result = resolve_loop_stages(loop, 4, self.STAGE_DEFS)
        assert result == [4]

    def test_all_stages_in_loop(self) -> None:
        loop = LoopConfig(
            condition="x == y",
            loop_stages=["analyze", "design", "implementation", "test", "review"],
        )
        result = resolve_loop_stages(loop, 4, self.STAGE_DEFS)
        assert result == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# evaluate_loop_condition (simple mode)
# ---------------------------------------------------------------------------


class TestEvaluateLoopConditionSimple:
    """Test simple field-comparison loop condition evaluation."""

    @pytest.mark.asyncio
    async def test_condition_met(self) -> None:
        loop = LoopConfig(
            condition="recommendation == approve",
            eval_mode="simple",
        )
        output = {"recommendation": "approve"}
        result = await evaluate_loop_condition(loop, output, {}, "/tmp")
        assert result is True

    @pytest.mark.asyncio
    async def test_condition_not_met(self) -> None:
        loop = LoopConfig(
            condition="recommendation == approve",
            eval_mode="simple",
        )
        output = {"recommendation": "request_changes"}
        result = await evaluate_loop_condition(loop, output, {}, "/tmp")
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_field_not_met(self) -> None:
        loop = LoopConfig(
            condition="recommendation == approve",
            eval_mode="simple",
        )
        result = await evaluate_loop_condition(loop, {}, {}, "/tmp")
        assert result is False

    @pytest.mark.asyncio
    async def test_nested_field(self) -> None:
        loop = LoopConfig(
            condition="review.status == passed",
            eval_mode="simple",
        )
        output = {"review": {"status": "passed"}}
        result = await evaluate_loop_condition(loop, output, {}, "/tmp")
        assert result is True

    @pytest.mark.asyncio
    async def test_inequality_condition(self) -> None:
        loop = LoopConfig(
            condition="findings_count != 0",
            eval_mode="simple",
        )
        output = {"findings_count": "3"}
        result = await evaluate_loop_condition(loop, output, {}, "/tmp")
        assert result is True


# ---------------------------------------------------------------------------
# evaluate_loop_condition (AI mode)
# ---------------------------------------------------------------------------


class TestEvaluateLoopConditionAI:
    """Test AI-evaluated loop conditions."""

    @pytest.mark.asyncio
    async def test_ai_condition_true(self) -> None:
        """AI eval returns True when Claude says result is true."""
        from aquarco_supervisor.cli.claude import ClaudeOutput

        loop = LoopConfig(
            condition="All risks are mitigated",
            eval_mode="ai",
        )

        mock_output = ClaudeOutput(
            structured={"result": True, "reasoning": "All risks addressed"},
            raw='{"result": true, "reasoning": "All risks addressed"}',
        )

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=mock_output,
        ):
            result = await evaluate_loop_condition(
                loop, {"some": "output"}, {0: {"risks": []}}, "/tmp",
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_ai_condition_false(self) -> None:
        from aquarco_supervisor.cli.claude import ClaudeOutput

        loop = LoopConfig(
            condition="All risks are mitigated",
            eval_mode="ai",
        )

        mock_output = ClaudeOutput(
            structured={"result": False, "reasoning": "Risk X still open"},
            raw='{"result": false, "reasoning": "Risk X still open"}',
        )

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=mock_output,
        ):
            result = await evaluate_loop_condition(
                loop, {}, {0: {"risks": ["X"]}}, "/tmp",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_ai_condition_failure_returns_false(self) -> None:
        """On AI evaluation failure, return False (continue looping)."""
        loop = LoopConfig(
            condition="All risks are mitigated",
            eval_mode="ai",
        )

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Claude unavailable"),
        ):
            result = await evaluate_loop_condition(
                loop, {}, {}, "/tmp",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_eval_mode_returns_true(self) -> None:
        """Unknown eval_mode stops the loop (safety)."""
        # We need to bypass validation to test this edge case
        loop = LoopConfig(condition="x == y", eval_mode="simple")
        # Monkey-patch to unknown mode
        object.__setattr__(loop, "eval_mode", "unknown")

        result = await evaluate_loop_condition(loop, {}, {}, "/tmp")
        assert result is True


# ---------------------------------------------------------------------------
# check_conditions (used by loop simple mode)
# ---------------------------------------------------------------------------


class TestCheckConditionsForLoops:
    """Additional check_conditions tests relevant to loop exit conditions."""

    def test_value_with_spaces(self) -> None:
        """Condition value can contain spaces."""
        output = {"message": "all clear"}
        assert check_conditions(["message == all clear"], output) is True

    def test_numeric_string_equality(self) -> None:
        output = {"count": 0}
        assert check_conditions(["count == 0"], output) is True

    def test_boolean_string(self) -> None:
        output = {"passed": "True"}
        assert check_conditions(["passed == True"], output) is True


# ---------------------------------------------------------------------------
# format_pipeline_stages (visualization)
# ---------------------------------------------------------------------------


class TestFormatPipelineStages:
    """Test pipeline visualization output."""

    def test_empty_pipeline(self) -> None:
        result = format_pipeline_stages([], markdown=False)
        assert result == "(empty pipeline)"

    def test_linear_pipeline(self) -> None:
        stages = [
            {"category": "analyze"},
            {"category": "implementation"},
            {"category": "test"},
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "[0] analyze" in result
        assert "[1] implementation" in result
        assert "[2] test" in result
        assert "done" in result

    def test_conditional_stage(self) -> None:
        stages = [
            {"category": "analyze"},
            {
                "category": "design",
                "conditions": ["estimated_complexity >= medium"],
            },
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "if: estimated_complexity >= medium" in result
        assert "[1] design" in result

    def test_optional_stage(self) -> None:
        stages = [
            {"category": "docs", "required": False},
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "(optional)" in result

    def test_loop_annotation(self) -> None:
        stages = [
            {"category": "analyze"},
            {"category": "implementation"},
            {
                "category": "review",
                "loop": {
                    "condition": "recommendation == approve",
                    "max_repeats": 3,
                    "eval_mode": "simple",
                    "loopStages": ["implementation", "review"],
                },
            },
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "LOOP" in result
        assert "implementation -> review" in result
        assert "max 3x" in result
        assert "simple" in result
        assert "recommendation == approve" in result

    def test_ai_loop_annotation(self) -> None:
        stages = [
            {"category": "review",
             "loop": {
                 "condition": "All risks mitigated",
                 "max_repeats": 5,
                 "eval_mode": "ai",
             }},
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "AI" in result
        assert "max 5x" in result

    def test_markdown_wrapping(self) -> None:
        stages = [{"category": "analyze"}]
        result = format_pipeline_stages(stages, markdown=True)
        assert result.startswith("```\n")
        assert result.endswith("\n```")

    def test_multiple_conditions_joined(self) -> None:
        stages = [
            {"category": "analyze"},
            {
                "category": "design",
                "conditions": ["complexity >= medium", "has_ui == true"],
            },
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "complexity >= medium AND has_ui == true" in result

    def test_loop_body_indices(self) -> None:
        """Loop body shows referenced stage indices."""
        stages = [
            {"category": "analyze"},
            {"category": "implementation"},
            {"category": "test"},
            {
                "category": "review",
                "loop": {
                    "condition": "recommendation == approve",
                    "loopStages": ["implementation", "test", "review"],
                    "max_repeats": 3,
                },
            },
        ]
        result = format_pipeline_stages(stages, markdown=False)
        assert "[1] implementation" in result
        assert "[2] test" in result
        assert "body:" in result

    def test_full_feature_pipeline(self) -> None:
        """Integration test with the actual feature-pipeline shape."""
        stages = [
            {"category": "analyze", "required": True},
            {
                "category": "design",
                "required": True,
                "conditions": ["estimated_complexity >= medium"],
            },
            {"category": "implementation", "required": True},
            {"category": "test", "required": True},
            {"category": "docs", "required": False},
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
        ]
        result = format_pipeline_stages(stages, markdown=False)

        # Should have all 6 stages
        assert "[0] analyze" in result
        assert "[1] design" in result
        assert "[2] implementation" in result
        assert "[3] test" in result
        assert "[4] docs (optional)" in result
        assert "[5] review" in result

        # Should show the loop
        assert "LOOP" in result
        assert "exit when: recommendation == approve" in result


class TestFormatPipelineStagesMarkdown:
    """Test markdown section rendering."""

    def test_includes_heading(self) -> None:
        stages = [{"category": "analyze"}]
        result = format_pipeline_stages_markdown("feature-pipeline", stages)
        assert "## Pipeline Stages: feature-pipeline" in result

    def test_includes_legend(self) -> None:
        stages = [{"category": "analyze"}]
        result = format_pipeline_stages_markdown("test-pipeline", stages)
        assert "Legend" in result
        assert "LOOP" in result
        assert "entry condition" in result

"""Extended tests for pipeline condition evaluation — covers all acceptance criteria.

Tests in this module cover:
  - Simple expression parser edge cases (operator combos, negation, floats, booleans)
  - Cross-stage field references (dotted path resolution)
  - evaluate_conditions() with yes/no routing and maxRepeats
  - AI condition evaluation (mocked)
  - Boolean YAML key handling (True/False vs "yes"/"no")
  - Error resilience (invalid expressions, missing fields, etc.)
  - _build_eval_context, _is_truthy, _to_number, _compare helpers
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from aquarco_supervisor.pipeline.conditions import (
    ConditionResult,
    _build_eval_context,
    _compare,
    _is_truthy,
    _resolve_field,
    _to_number,
    _tokenize,
    evaluate_conditions,
    evaluate_simple_expression,
)


# ---------------------------------------------------------------------------
# Acceptance criterion: severity == major_issues || severity == blocking
# ---------------------------------------------------------------------------


class TestAcceptanceCriteriaExpressions:
    """Tests directly mapping to design acceptance criteria."""

    def test_severity_or_blocking_true_major(self) -> None:
        """severity == major_issues || severity == blocking => True when severity=major_issues."""
        ctx = {"severity": "major_issues"}
        assert evaluate_simple_expression(
            "severity == major_issues || severity == blocking", ctx
        ) is True

    def test_severity_or_blocking_false_minor(self) -> None:
        """severity == major_issues || severity == blocking => False when severity=minor_issues."""
        ctx = {"severity": "minor_issues"}
        assert evaluate_simple_expression(
            "severity == major_issues || severity == blocking", ctx
        ) is False

    def test_severity_or_blocking_true_blocking(self) -> None:
        ctx = {"severity": "blocking"}
        assert evaluate_simple_expression(
            "severity == major_issues || severity == blocking", ctx
        ) is True

    def test_tests_added_zero_short_circuits(self) -> None:
        """tests_added == 0 || (coverage_percent >= 80 && tests_failed == 0) => True when tests_added=0."""
        ctx = {"tests_added": 0, "coverage_percent": 50, "tests_failed": 3}
        assert evaluate_simple_expression(
            "tests_added == 0 || (coverage_percent >= 80 && tests_failed == 0)", ctx
        ) is True

    def test_coverage_and_no_failures(self) -> None:
        """coverage_percent>=80 && tests_failed==0 && tests_added=5 => True."""
        ctx = {"tests_added": 5, "coverage_percent": 90, "tests_failed": 0}
        assert evaluate_simple_expression(
            "tests_added == 0 || (coverage_percent >= 80 && tests_failed == 0)", ctx
        ) is True

    def test_true_literal_unconditional(self) -> None:
        """'true' evaluates to True unconditionally."""
        assert evaluate_simple_expression("true", {}) is True

    def test_false_literal_unconditional(self) -> None:
        assert evaluate_simple_expression("false", {}) is False

    def test_cross_stage_analysis_risks(self) -> None:
        """Cross-stage field reference analysis.risks resolves from stage_outputs."""
        stage_outputs = {"analysis": {"risks": ["risk1", "risk2"]}}
        ctx = _build_eval_context(stage_outputs, {})
        assert _resolve_field(ctx, "analysis.risks") == ["risk1", "risk2"]

    def test_cross_stage_nested_deep(self) -> None:
        """Deep dotted path a.b.c resolves correctly."""
        stage_outputs = {"a": {"b": {"c": "deep_value"}}}
        ctx = _build_eval_context(stage_outputs, {})
        assert _resolve_field(ctx, "a.b.c") == "deep_value"


# ---------------------------------------------------------------------------
# evaluate_conditions: jump routing
# ---------------------------------------------------------------------------


class TestEvaluateConditionsRouting:
    """Tests for evaluate_conditions yes/no routing and jump logic."""

    @pytest.mark.asyncio
    async def test_jump_to_implementation_on_false(self) -> None:
        """When condition is False and 'no' is 'implementation', jump there."""
        conditions = [
            {"simple": "severity == blocking", "no": "implementation", "maxRepeats": 5}
        ]
        result = await evaluate_conditions(conditions, {}, {"severity": "minor"}, {})
        assert result.jump_to == "implementation"
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_no_jump_when_no_conditions(self) -> None:
        """Empty conditions => no jump."""
        result = await evaluate_conditions([], {}, {}, {})
        assert result.jump_to is None
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_no_jump_when_all_conditions_pass_without_yes(self) -> None:
        """When all conditions are True but no 'yes' field, no jump."""
        conditions = [{"simple": "true"}]
        result = await evaluate_conditions(conditions, {}, {}, {})
        assert result.jump_to is None
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_max_repeats_exceeded_skips_condition(self) -> None:
        """When maxRepeats exceeded for target, skip and fall through."""
        conditions = [
            {"simple": "true", "yes": "implementation", "maxRepeats": 3}
        ]
        result = await evaluate_conditions(conditions, {}, {}, {"implementation": 3})
        assert result.jump_to is None
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_max_repeats_not_exceeded_allows_jump(self) -> None:
        conditions = [
            {"simple": "true", "yes": "implementation", "maxRepeats": 3}
        ]
        result = await evaluate_conditions(conditions, {}, {}, {"implementation": 2})
        assert result.jump_to == "implementation"
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_max_repeats_zero_means_unlimited(self) -> None:
        """maxRepeats=0 means no limit."""
        conditions = [
            {"simple": "true", "yes": "review", "maxRepeats": 0}
        ]
        result = await evaluate_conditions(conditions, {}, {}, {"review": 1000})
        assert result.jump_to == "review"
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_max_repeats_not_specified_means_unlimited(self) -> None:
        """No maxRepeats key means no limit."""
        conditions = [
            {"simple": "true", "yes": "review"}
        ]
        result = await evaluate_conditions(conditions, {}, {}, {"review": 999})
        assert result.jump_to == "review"
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_multiple_conditions_first_match_wins(self) -> None:
        """First condition with a matching jump target wins."""
        conditions = [
            {"simple": "x == 1", "yes": "first_target"},
            {"simple": "x == 1", "yes": "second_target"},
        ]
        result = await evaluate_conditions(conditions, {}, {"x": 1}, {})
        assert result.jump_to == "first_target"

    @pytest.mark.asyncio
    async def test_fallthrough_to_second_condition(self) -> None:
        """First condition has no matching branch, second matches."""
        conditions = [
            {"simple": "x == 99", "yes": "first_target"},  # False, no "no" -> skip
            {"simple": "true", "yes": "second_target"},
        ]
        result = await evaluate_conditions(conditions, {}, {"x": 1}, {})
        assert result.jump_to == "second_target"

    @pytest.mark.asyncio
    async def test_boolean_yaml_keys_true_false(self) -> None:
        """YAML may parse unquoted yes/no as True/False boolean keys."""
        conditions = [
            {"simple": "true", True: "target_stage"}  # True key instead of "yes"
        ]
        result = await evaluate_conditions(conditions, {}, {}, {})
        assert result.jump_to == "target_stage"

    @pytest.mark.asyncio
    async def test_boolean_yaml_key_false_branch(self) -> None:
        conditions = [
            {"simple": "false", False: "fallback_stage"}  # False key instead of "no"
        ]
        result = await evaluate_conditions(conditions, {}, {}, {})
        assert result.jump_to == "fallback_stage"

    @pytest.mark.asyncio
    async def test_non_dict_conditions_are_skipped(self) -> None:
        """Non-dict items in conditions list are skipped."""
        conditions: list[Any] = ["legacy_string", 42, None, {"simple": "true", "yes": "target"}]
        result = await evaluate_conditions(conditions, {}, {}, {})
        assert result.jump_to == "target"


# ---------------------------------------------------------------------------
# AI condition evaluation
# ---------------------------------------------------------------------------


class TestAIConditions:
    @pytest.mark.asyncio
    async def test_ai_evaluator_true_routes_to_yes(self) -> None:
        ai_evaluator = AsyncMock(return_value=True)
        conditions = [{"ai": "Is the code safe?", "yes": "deploy", "no": "fix"}]
        result = await evaluate_conditions(conditions, {}, {}, {}, ai_evaluator=ai_evaluator)
        assert result.jump_to == "deploy"
        ai_evaluator.assert_called_once()

    @pytest.mark.asyncio
    async def test_ai_evaluator_false_routes_to_no(self) -> None:
        ai_evaluator = AsyncMock(return_value=False)
        conditions = [{"ai": "Is the code safe?", "yes": "deploy", "no": "fix"}]
        result = await evaluate_conditions(conditions, {}, {}, {}, ai_evaluator=ai_evaluator)
        assert result.jump_to == "fix"

    @pytest.mark.asyncio
    async def test_ai_evaluator_none_skips(self) -> None:
        """No evaluator provided => AI condition is skipped."""
        conditions = [{"ai": "Check something", "yes": "next", "no": "prev"}]
        result = await evaluate_conditions(conditions, {}, {}, {})
        assert result.jump_to is None

    @pytest.mark.asyncio
    async def test_ai_evaluator_exception_skips(self) -> None:
        """AI evaluator that raises => condition is skipped."""

        async def failing_evaluator(prompt: str, context: dict) -> bool:
            raise RuntimeError("Claude CLI failed")

        conditions = [{"ai": "Check something", "yes": "next", "no": "prev"}]
        result = await evaluate_conditions(conditions, {}, {}, {}, ai_evaluator=failing_evaluator)
        assert result.jump_to is None
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_ai_evaluator_receives_correct_context(self) -> None:
        """AI evaluator receives merged context from stage_outputs + current_output."""
        captured_args: list[Any] = []

        async def capturing_evaluator(prompt: str, context: dict) -> bool:
            captured_args.append((prompt, context))
            return True

        stage_outputs = {"analysis": {"risk_level": "high"}}
        current_output = {"tests_passed": 42}
        conditions = [{"ai": "Are risks mitigated?", "yes": "deploy"}]
        await evaluate_conditions(
            conditions, stage_outputs, current_output, {},
            ai_evaluator=capturing_evaluator,
        )
        assert len(captured_args) == 1
        prompt, ctx = captured_args[0]
        assert prompt == "Are risks mitigated?"
        assert ctx["tests_passed"] == 42
        assert ctx["analysis"]["risk_level"] == "high"


# ---------------------------------------------------------------------------
# Simple expression parser — edge cases
# ---------------------------------------------------------------------------


class TestSimpleExpressionEdgeCases:
    def test_numeric_float_comparison(self) -> None:
        ctx = {"coverage": 85.5}
        assert evaluate_simple_expression("coverage >= 80.0", ctx) is True
        assert evaluate_simple_expression("coverage < 80.0", ctx) is False

    def test_negative_number(self) -> None:
        ctx = {"delta": -5}
        assert evaluate_simple_expression("delta < 0", ctx) is True
        assert evaluate_simple_expression("delta >= 0", ctx) is False

    def test_string_equality_not_found_in_context(self) -> None:
        """Unknown identifier treated as string literal."""
        ctx = {"severity": "major_issues"}
        assert evaluate_simple_expression("severity == major_issues", ctx) is True

    def test_nested_parentheses(self) -> None:
        ctx = {"a": 1, "b": 2, "c": 3}
        assert evaluate_simple_expression("(a == 1 && (b == 2 || c == 99))", ctx) is True
        assert evaluate_simple_expression("(a == 99 && (b == 2 || c == 3))", ctx) is False

    def test_empty_string_is_true(self) -> None:
        """Empty expression returns True (vacuously true)."""
        assert evaluate_simple_expression("", {}) is True
        assert evaluate_simple_expression("   ", {}) is True

    def test_invalid_expression_raises(self) -> None:
        with pytest.raises(ValueError):
            evaluate_simple_expression("== ==", {})

    def test_unbalanced_parens_raises(self) -> None:
        with pytest.raises(ValueError):
            evaluate_simple_expression("(a == 1", {"a": 1})

    def test_le_and_gt_operators(self) -> None:
        ctx = {"x": 5}
        assert evaluate_simple_expression("x <= 5", ctx) is True
        assert evaluate_simple_expression("x <= 4", ctx) is False
        assert evaluate_simple_expression("x > 4", ctx) is True
        assert evaluate_simple_expression("x > 5", ctx) is False

    def test_ne_operator(self) -> None:
        ctx = {"status": "ok"}
        assert evaluate_simple_expression("status != fail", ctx) is True
        assert evaluate_simple_expression("status != ok", ctx) is False

    def test_truthy_standalone_value(self) -> None:
        """A standalone identifier that resolves to a truthy value."""
        ctx = {"has_errors": True}
        # standalone ident without comparison operator -> truthy check
        assert evaluate_simple_expression("has_errors", ctx) is True

    def test_falsy_standalone_value(self) -> None:
        ctx = {"has_errors": False}
        assert evaluate_simple_expression("has_errors", ctx) is False

    def test_standalone_zero_is_falsy(self) -> None:
        ctx = {"count": 0}
        assert evaluate_simple_expression("count", ctx) is False

    def test_standalone_nonempty_list_is_truthy(self) -> None:
        ctx = {"items": [1, 2, 3]}
        assert evaluate_simple_expression("items", ctx) is True

    def test_standalone_empty_list_is_falsy(self) -> None:
        ctx = {"items": []}
        assert evaluate_simple_expression("items", ctx) is False

    def test_or_with_multiple_comparisons(self) -> None:
        ctx = {"status": "pass", "score": 100}
        expr = "status == pass || score >= 90"
        assert evaluate_simple_expression(expr, ctx) is True

    def test_and_all_must_be_true(self) -> None:
        ctx = {"a": 1, "b": 2}
        assert evaluate_simple_expression("a == 1 && b == 3", ctx) is False


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_build_eval_context_merges(self) -> None:
        stage_outputs = {"analysis": {"risk": "high"}}
        current = {"tests": 5}
        ctx = _build_eval_context(stage_outputs, current)
        assert ctx["analysis"]["risk"] == "high"
        assert ctx["tests"] == 5

    def test_build_eval_context_current_overrides(self) -> None:
        """Current output keys override stage output keys at top level."""
        stage_outputs = {"x": {"a": 1}}
        current = {"x": "overridden"}
        ctx = _build_eval_context(stage_outputs, current)
        assert ctx["x"] == "overridden"

    def test_resolve_field_simple(self) -> None:
        assert _resolve_field({"a": 1}, "a") == 1

    def test_resolve_field_nested(self) -> None:
        assert _resolve_field({"a": {"b": {"c": 3}}}, "a.b.c") == 3

    def test_resolve_field_missing(self) -> None:
        assert _resolve_field({"a": 1}, "b") is None

    def test_resolve_field_partial_path(self) -> None:
        assert _resolve_field({"a": 1}, "a.b") is None

    def test_is_truthy_none(self) -> None:
        assert _is_truthy(None) is False

    def test_is_truthy_bool(self) -> None:
        assert _is_truthy(True) is True
        assert _is_truthy(False) is False

    def test_is_truthy_numbers(self) -> None:
        assert _is_truthy(0) is False
        assert _is_truthy(1) is True
        assert _is_truthy(-1) is True
        assert _is_truthy(0.0) is False

    def test_is_truthy_strings(self) -> None:
        assert _is_truthy("") is False
        assert _is_truthy("false") is False
        assert _is_truthy("0") is False
        assert _is_truthy("no") is False
        assert _is_truthy("none") is False
        assert _is_truthy("hello") is True

    def test_is_truthy_collections(self) -> None:
        assert _is_truthy([]) is False
        assert _is_truthy([1]) is True
        assert _is_truthy({}) is False
        assert _is_truthy({"a": 1}) is True

    def test_to_number_int(self) -> None:
        assert _to_number(42) == 42.0

    def test_to_number_float(self) -> None:
        assert _to_number(3.14) == 3.14

    def test_to_number_string(self) -> None:
        assert _to_number("42") == 42.0
        assert _to_number("3.14") == 3.14

    def test_to_number_invalid(self) -> None:
        assert _to_number("not_a_number") is None
        assert _to_number(None) is None
        assert _to_number([]) is None

    def test_compare_numeric_eq(self) -> None:
        assert _compare(5, "EQ", 5) is True
        assert _compare(5, "EQ", 6) is False

    def test_compare_numeric_ne(self) -> None:
        assert _compare(5, "NE", 6) is True
        assert _compare(5, "NE", 5) is False

    def test_compare_numeric_ge_gt_le_lt(self) -> None:
        assert _compare(5, "GE", 5) is True
        assert _compare(5, "GE", 6) is False
        assert _compare(6, "GT", 5) is True
        assert _compare(5, "GT", 5) is False
        assert _compare(5, "LE", 5) is True
        assert _compare(6, "LE", 5) is False
        assert _compare(4, "LT", 5) is True
        assert _compare(5, "LT", 5) is False

    def test_compare_string_fallback(self) -> None:
        assert _compare("abc", "EQ", "abc") is True
        assert _compare("abc", "NE", "xyz") is True
        assert _compare("abc", "LT", "xyz") is True
        assert _compare("xyz", "GT", "abc") is True

    def test_compare_none_values(self) -> None:
        assert _compare(None, "EQ", None) is True
        assert _compare(None, "NE", "abc") is True

    def test_compare_unknown_operator(self) -> None:
        assert _compare(1, "UNKNOWN", 1) is False

    def test_tokenize_basic(self) -> None:
        tokens = _tokenize("a == 1")
        assert len(tokens) == 3
        assert tokens[0].type == "IDENT"
        assert tokens[1].type == "EQ"
        assert tokens[2].type == "NUMBER"

    def test_tokenize_all_operators(self) -> None:
        tokens = _tokenize("a == b != c >= d > e <= f < g && h || i")
        types = [t.type for t in tokens]
        assert "EQ" in types
        assert "NE" in types
        assert "GE" in types
        assert "GT" in types
        assert "LE" in types
        assert "LT" in types
        assert "AND" in types
        assert "OR" in types

    def test_tokenize_parentheses(self) -> None:
        tokens = _tokenize("(a || b)")
        types = [t.type for t in tokens]
        assert types == ["LPAREN", "IDENT", "OR", "IDENT", "RPAREN"]


# ---------------------------------------------------------------------------
# Condition with boolean 'simple' (YAML true/false)
# ---------------------------------------------------------------------------


class TestBooleanSimpleValue:
    @pytest.mark.asyncio
    async def test_simple_bool_true(self) -> None:
        """YAML `simple: true` (parsed as bool) should evaluate to True."""
        conditions = [{"simple": True, "yes": "next_stage"}]
        result = await evaluate_conditions(conditions, {}, {}, {})
        assert result.jump_to == "next_stage"
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_simple_bool_false(self) -> None:
        """YAML `simple: false` (parsed as bool) should evaluate to False."""
        conditions = [{"simple": False, "no": "fallback_stage"}]
        result = await evaluate_conditions(conditions, {}, {}, {})
        assert result.jump_to == "fallback_stage"
        assert result.matched is True


# ---------------------------------------------------------------------------
# Cross-stage references in evaluate_conditions
# ---------------------------------------------------------------------------


class TestCrossStageReferences:
    @pytest.mark.asyncio
    async def test_reference_previous_stage_output_in_condition(self) -> None:
        """analysis.estimated_complexity from stage_outputs used in condition."""
        conditions = [
            {"simple": "analysis.estimated_complexity == high", "yes": "design"}
        ]
        stage_outputs = {"analysis": {"estimated_complexity": "high"}}
        result = await evaluate_conditions(conditions, stage_outputs, {}, {})
        assert result.jump_to == "design"

    @pytest.mark.asyncio
    async def test_current_output_takes_precedence(self) -> None:
        """Current output fields override stage_outputs at top level."""
        conditions = [
            {"simple": "severity == critical", "yes": "fix"}
        ]
        stage_outputs = {"analysis": {"severity": "low"}}
        current_output = {"severity": "critical"}
        result = await evaluate_conditions(conditions, stage_outputs, current_output, {})
        assert result.jump_to == "fix"

    @pytest.mark.asyncio
    async def test_numeric_cross_stage_comparison(self) -> None:
        conditions = [
            {"simple": "test.coverage_percent >= 80", "yes": "deploy"}
        ]
        stage_outputs = {"test": {"coverage_percent": 92}}
        result = await evaluate_conditions(conditions, stage_outputs, {}, {})
        assert result.jump_to == "deploy"

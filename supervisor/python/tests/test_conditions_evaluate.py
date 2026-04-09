"""Tests for evaluate_conditions() async function — maxRepeats, jump routing, AI fallback.

Complements test_conditions.py and test_conditions_extended.py by testing the
full evaluate_conditions() async path with condition routing, maxRepeats guards,
and AI evaluator integration.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.pipeline.conditions import (
    ConditionResult,
    evaluate_conditions,
    evaluate_simple_expression,
    _build_eval_context,
    _resolve_field,
    _is_truthy,
    _to_number,
    _compare,
)


# -----------------------------------------------------------------------
# evaluate_conditions — jump routing
# -----------------------------------------------------------------------


class TestEvaluateConditionsRouting:
    """Test condition-driven stage jumps via evaluate_conditions()."""

    @pytest.mark.asyncio
    async def test_empty_conditions_no_jump(self):
        result = await evaluate_conditions([], {}, {}, {})
        assert result.jump_to is None
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_simple_true_with_yes_jump(self):
        conditions = [{"simple": "status == pass", "yes": "deploy"}]
        result = await evaluate_conditions(
            conditions, {}, {"status": "pass"}, {},
        )
        assert result.jump_to == "deploy"
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_simple_false_with_no_jump(self):
        conditions = [{"simple": "status == pass", "no": "fix"}]
        result = await evaluate_conditions(
            conditions, {}, {"status": "fail"}, {},
        )
        assert result.jump_to == "fix"
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_simple_true_no_yes_key_skips(self):
        """When condition is true but no 'yes' key, skip to next condition."""
        conditions = [
            {"simple": "status == pass"},  # No jump target
            {"simple": "status == pass", "yes": "deploy"},
        ]
        result = await evaluate_conditions(
            conditions, {}, {"status": "pass"}, {},
        )
        assert result.jump_to == "deploy"

    @pytest.mark.asyncio
    async def test_no_matching_jump_returns_empty(self):
        conditions = [
            {"simple": "status == pass"},  # No jump targets at all
        ]
        result = await evaluate_conditions(
            conditions, {}, {"status": "pass"}, {},
        )
        assert result.jump_to is None
        assert result.matched is False

    @pytest.mark.asyncio
    async def test_boolean_yaml_keys_true_false(self):
        """YAML may parse yes/no as True/False booleans."""
        conditions = [{
            "simple": "tests_passed > 0",
            True: "deploy",   # YAML 'yes:' becomes True
            False: "fix",     # YAML 'no:' becomes False
        }]
        result = await evaluate_conditions(
            conditions, {}, {"tests_passed": 10}, {},
        )
        assert result.jump_to == "deploy"

    @pytest.mark.asyncio
    async def test_boolean_yaml_keys_false_branch(self):
        conditions = [{
            "simple": "tests_passed > 0",
            True: "deploy",
            False: "fix",
        }]
        result = await evaluate_conditions(
            conditions, {}, {"tests_passed": 0}, {},
        )
        assert result.jump_to == "fix"


# -----------------------------------------------------------------------
# evaluate_conditions — maxRepeats guard
# -----------------------------------------------------------------------


class TestEvaluateConditionsMaxRepeats:
    """Test maxRepeats limiting on condition jumps."""

    @pytest.mark.asyncio
    async def test_max_repeats_not_exceeded(self):
        conditions = [{
            "simple": "true",
            "yes": "implement",
            "maxRepeats": 3,
        }]
        result = await evaluate_conditions(
            conditions, {}, {}, {"implement": 1},
        )
        assert result.jump_to == "implement"

    @pytest.mark.asyncio
    async def test_max_repeats_exceeded_skips(self):
        conditions = [{
            "simple": "true",
            "yes": "implement",
            "maxRepeats": 3,
        }]
        result = await evaluate_conditions(
            conditions, {}, {}, {"implement": 3},
        )
        assert result.jump_to is None

    @pytest.mark.asyncio
    async def test_max_repeats_exceeded_falls_through_to_next(self):
        """When maxRepeats exceeded on first condition, try next condition."""
        conditions = [
            {"simple": "true", "yes": "fix", "maxRepeats": 2},
            {"simple": "true", "yes": "deploy"},
        ]
        result = await evaluate_conditions(
            conditions, {}, {}, {"fix": 2},
        )
        assert result.jump_to == "deploy"

    @pytest.mark.asyncio
    async def test_max_repeats_zero_means_no_limit(self):
        """maxRepeats=0 means no limit (legacy behavior)."""
        conditions = [{
            "simple": "true",
            "yes": "fix",
            "maxRepeats": 0,
        }]
        # maxRepeats=0 => the `if max_repeats > 0` check is False => no limit
        result = await evaluate_conditions(
            conditions, {}, {}, {"fix": 100},
        )
        assert result.jump_to == "fix"


# -----------------------------------------------------------------------
# evaluate_conditions — AI conditions
# -----------------------------------------------------------------------


class TestEvaluateConditionsAI:
    """Test AI condition evaluation via async evaluator callback."""

    @pytest.mark.asyncio
    async def test_ai_condition_with_evaluator(self):
        async def ai_eval(prompt, context):
            return (True, "All tests pass")

        conditions = [{"ai": "Are all tests passing?", "yes": "deploy"}]
        result = await evaluate_conditions(
            conditions, {}, {}, {}, ai_evaluator=ai_eval,
        )
        assert result.jump_to == "deploy"
        assert result.matched is True

    @pytest.mark.asyncio
    async def test_ai_condition_false_branch(self):
        async def ai_eval(prompt, context):
            return (False, "Tests failing")

        conditions = [{"ai": "Are all tests passing?", "no": "fix"}]
        result = await evaluate_conditions(
            conditions, {}, {}, {}, ai_evaluator=ai_eval,
        )
        assert result.jump_to == "fix"

    @pytest.mark.asyncio
    async def test_ai_condition_no_evaluator_skips(self):
        """When no AI evaluator is provided, AI conditions are skipped."""
        conditions = [
            {"ai": "Are all tests passing?", "yes": "deploy"},
            {"simple": "true", "yes": "fallback"},
        ]
        result = await evaluate_conditions(
            conditions, {}, {}, {},
        )
        # AI condition skipped, falls through to simple condition
        assert result.jump_to == "fallback"

    @pytest.mark.asyncio
    async def test_ai_condition_error_skips(self):
        """When AI evaluator raises, condition is skipped."""
        async def ai_eval(prompt, context):
            raise RuntimeError("API error")

        conditions = [
            {"ai": "Check something", "yes": "deploy"},
            {"simple": "true", "yes": "fallback"},
        ]
        result = await evaluate_conditions(
            conditions, {}, {}, {}, ai_evaluator=ai_eval,
        )
        assert result.jump_to == "fallback"


# -----------------------------------------------------------------------
# evaluate_conditions — cross-stage field references
# -----------------------------------------------------------------------


class TestCrossStageReferences:
    """Test resolving fields from other stages via dotted paths."""

    @pytest.mark.asyncio
    async def test_cross_stage_field_reference(self):
        stage_outputs = {
            "analysis": {"severity": "blocking", "risks": ["risk1"]},
        }
        conditions = [{
            "simple": "analysis.severity == blocking",
            "yes": "fix",
        }]
        result = await evaluate_conditions(
            conditions, stage_outputs, {}, {},
        )
        assert result.jump_to == "fix"

    @pytest.mark.asyncio
    async def test_current_output_overrides_stage_outputs(self):
        """Unqualified names resolve from current_output, which is merged last."""
        stage_outputs = {"analysis": {"severity": "minor"}}
        current = {"severity": "blocking"}
        conditions = [{
            "simple": "severity == blocking",
            "yes": "fix",
        }]
        result = await evaluate_conditions(
            conditions, stage_outputs, current, {},
        )
        assert result.jump_to == "fix"


# -----------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------


class TestBuildEvalContext:
    def test_merges_stage_outputs_and_current(self):
        stages = {"analysis": {"risks": 3}}
        current = {"status": "pass"}
        ctx = _build_eval_context(stages, current)
        assert ctx["analysis"]["risks"] == 3
        assert ctx["status"] == "pass"

    def test_current_overwrites_stage_key(self):
        stages = {"status": "old"}
        current = {"status": "new"}
        ctx = _build_eval_context(stages, current)
        assert ctx["status"] == "new"


class TestResolveField:
    def test_simple_key(self):
        assert _resolve_field({"a": 1}, "a") == 1

    def test_dotted_path(self):
        assert _resolve_field({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_missing_key(self):
        assert _resolve_field({"a": 1}, "b") is None

    def test_non_dict_intermediate(self):
        assert _resolve_field({"a": "string"}, "a.b") is None


class TestIsTruthy:
    def test_none_is_falsy(self):
        assert _is_truthy(None) is False

    def test_true_is_truthy(self):
        assert _is_truthy(True) is True

    def test_false_is_falsy(self):
        assert _is_truthy(False) is False

    def test_zero_is_falsy(self):
        assert _is_truthy(0) is False

    def test_nonzero_is_truthy(self):
        assert _is_truthy(42) is True

    def test_empty_string_is_falsy(self):
        assert _is_truthy("") is False

    def test_false_string_is_falsy(self):
        assert _is_truthy("false") is False

    def test_no_string_is_falsy(self):
        assert _is_truthy("no") is False

    def test_none_string_is_falsy(self):
        assert _is_truthy("none") is False

    def test_zero_string_is_falsy(self):
        assert _is_truthy("0") is False

    def test_nonempty_string_is_truthy(self):
        assert _is_truthy("hello") is True

    def test_empty_list_is_falsy(self):
        assert _is_truthy([]) is False

    def test_nonempty_list_is_truthy(self):
        assert _is_truthy([1]) is True

    def test_empty_dict_is_falsy(self):
        assert _is_truthy({}) is False

    def test_nonempty_dict_is_truthy(self):
        assert _is_truthy({"a": 1}) is True


class TestToNumber:
    def test_int(self):
        assert _to_number(5) == 5.0

    def test_float(self):
        assert _to_number(3.14) == 3.14

    def test_numeric_string(self):
        assert _to_number("42") == 42.0

    def test_non_numeric_string(self):
        assert _to_number("hello") is None

    def test_none(self):
        assert _to_number(None) is None

    def test_list(self):
        assert _to_number([1]) is None


class TestCompare:
    def test_numeric_eq(self):
        assert _compare(5, "EQ", 5) is True
        assert _compare(5, "EQ", 6) is False

    def test_numeric_ne(self):
        assert _compare(5, "NE", 6) is True
        assert _compare(5, "NE", 5) is False

    def test_numeric_ge(self):
        assert _compare(5, "GE", 5) is True
        assert _compare(5, "GE", 4) is True
        assert _compare(5, "GE", 6) is False

    def test_numeric_gt(self):
        assert _compare(5, "GT", 4) is True
        assert _compare(5, "GT", 5) is False

    def test_numeric_le(self):
        assert _compare(5, "LE", 5) is True
        assert _compare(5, "LE", 6) is True
        assert _compare(5, "LE", 4) is False

    def test_numeric_lt(self):
        assert _compare(5, "LT", 6) is True
        assert _compare(5, "LT", 5) is False

    def test_string_eq(self):
        assert _compare("abc", "EQ", "abc") is True
        assert _compare("abc", "EQ", "def") is False

    def test_string_ne(self):
        assert _compare("abc", "NE", "def") is True

    def test_none_comparison(self):
        assert _compare(None, "EQ", None) is True
        assert _compare(None, "NE", "x") is True

    def test_unknown_operator_returns_false(self):
        assert _compare(5, "UNKNOWN", 5) is False


# -----------------------------------------------------------------------
# Expression parser edge cases
# -----------------------------------------------------------------------


class TestExpressionParserEdgeCases:
    def test_empty_expression_is_true(self):
        assert evaluate_simple_expression("", {}) is True

    def test_whitespace_only_is_true(self):
        assert evaluate_simple_expression("   ", {}) is True

    def test_parenthesized_or(self):
        ctx = {"a": 1, "b": 0}
        assert evaluate_simple_expression("(a > 0) || (b > 0)", ctx) is True

    def test_nested_parens(self):
        ctx = {"x": 5}
        assert evaluate_simple_expression("((x > 3))", ctx) is True

    def test_and_short_circuit(self):
        ctx = {"a": 0, "b": 1}
        assert evaluate_simple_expression("a > 0 && b > 0", ctx) is False

    def test_negative_number(self):
        ctx = {"val": -5}
        assert evaluate_simple_expression("val < 0", ctx) is True

    def test_float_comparison(self):
        ctx = {"cost": 3.14}
        assert evaluate_simple_expression("cost > 3.0", ctx) is True
        assert evaluate_simple_expression("cost < 4.0", ctx) is True

    def test_boolean_literal_true(self):
        assert evaluate_simple_expression("true", {}) is True

    def test_boolean_literal_false(self):
        assert evaluate_simple_expression("false", {}) is False

    def test_invalid_expression_raises(self):
        with pytest.raises(ValueError):
            evaluate_simple_expression("@#$%", {})

    def test_unresolved_ident_treated_as_string(self):
        """Unresolved identifiers become string literals."""
        # "unknown" not in context, treated as string "unknown"
        assert evaluate_simple_expression("status == unknown", {"status": "unknown"}) is True

    def test_complex_and_or_precedence(self):
        """AND has higher precedence than OR."""
        ctx = {"a": 1, "b": 0, "c": 1}
        # Should evaluate as: a > 0 || (b > 0 && c > 0)
        # = True || (False && True) = True || False = True
        assert evaluate_simple_expression("a > 0 || b > 0 && c > 0", ctx) is True

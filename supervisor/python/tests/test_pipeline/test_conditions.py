"""Tests for pipeline condition checking (both legacy and structured)."""

from __future__ import annotations

import pytest

from aquarco_supervisor.pipeline.executor import check_conditions
from aquarco_supervisor.pipeline.conditions import (
    ConditionResult,
    evaluate_conditions,
    evaluate_simple_expression,
)


# ---------------------------------------------------------------------------
# Legacy string-based conditions (backward compat)
# ---------------------------------------------------------------------------


def test_legacy_equality_condition() -> None:
    output = {"status": "pass"}
    assert check_conditions(["status == pass"], output) is True
    assert check_conditions(["status == fail"], output) is False


def test_legacy_inequality_condition() -> None:
    output = {"status": "pass"}
    assert check_conditions(["status != fail"], output) is True
    assert check_conditions(["status != pass"], output) is False


def test_legacy_complexity_gte() -> None:
    output = {"analysis": {"estimated_complexity": "high"}}
    assert check_conditions(["analysis.estimated_complexity >= medium"], output) is True
    assert check_conditions(["analysis.estimated_complexity >= epic"], output) is False


def test_legacy_empty_conditions() -> None:
    assert check_conditions([], {}) is True


def test_legacy_missing_field() -> None:
    assert check_conditions(["missing.field == value"], {}) is False


def test_legacy_nested_field_resolution() -> None:
    output = {"a": {"b": {"c": "value"}}}
    assert check_conditions(["a.b.c == value"], output) is True


# ---------------------------------------------------------------------------
# Simple expression parser
# ---------------------------------------------------------------------------


def test_simple_true_literal() -> None:
    assert evaluate_simple_expression("true", {}) is True


def test_simple_false_literal() -> None:
    assert evaluate_simple_expression("false", {}) is False


def test_simple_equality() -> None:
    ctx = {"severity": "major_issues"}
    assert evaluate_simple_expression("severity == major_issues", ctx) is True
    assert evaluate_simple_expression("severity == minor_issues", ctx) is False


def test_simple_inequality() -> None:
    ctx = {"severity": "major_issues"}
    assert evaluate_simple_expression("severity != minor_issues", ctx) is True
    assert evaluate_simple_expression("severity != major_issues", ctx) is False


def test_simple_numeric_comparison() -> None:
    ctx = {"coverage_percent": 90, "tests_failed": 0, "tests_added": 5}
    assert evaluate_simple_expression("coverage_percent >= 80", ctx) is True
    assert evaluate_simple_expression("coverage_percent < 80", ctx) is False
    assert evaluate_simple_expression("tests_failed == 0", ctx) is True
    assert evaluate_simple_expression("tests_added > 0", ctx) is True


def test_simple_or_expression() -> None:
    ctx = {"severity": "major_issues"}
    assert evaluate_simple_expression(
        "severity == major_issues || severity == blocking", ctx
    ) is True
    ctx2 = {"severity": "blocking"}
    assert evaluate_simple_expression(
        "severity == major_issues || severity == blocking", ctx2
    ) is True
    ctx3 = {"severity": "minor_issues"}
    assert evaluate_simple_expression(
        "severity == major_issues || severity == blocking", ctx3
    ) is False


def test_simple_and_expression() -> None:
    ctx = {"coverage_percent": 90, "tests_failed": 0}
    assert evaluate_simple_expression(
        "coverage_percent >= 80 && tests_failed == 0", ctx
    ) is True
    ctx2 = {"coverage_percent": 70, "tests_failed": 0}
    assert evaluate_simple_expression(
        "coverage_percent >= 80 && tests_failed == 0", ctx2
    ) is False


def test_simple_parentheses() -> None:
    ctx = {"tests_added": 0, "coverage_percent": 90, "tests_failed": 0}
    expr = "tests_added == 0 || (coverage_percent >= 80 && tests_failed == 0)"
    assert evaluate_simple_expression(expr, ctx) is True

    ctx2 = {"tests_added": 5, "coverage_percent": 90, "tests_failed": 0}
    assert evaluate_simple_expression(expr, ctx2) is True

    ctx3 = {"tests_added": 5, "coverage_percent": 70, "tests_failed": 2}
    assert evaluate_simple_expression(expr, ctx3) is False


def test_simple_cross_stage_reference() -> None:
    """Dotted field references resolve from nested stage outputs."""
    ctx = {"analysis": {"risks": ["risk1", "risk2"]}}
    # analysis.risks is a list, truthy
    assert evaluate_simple_expression("analysis.risks", ctx) is True


def test_simple_empty_expression() -> None:
    assert evaluate_simple_expression("", {}) is True


# ---------------------------------------------------------------------------
# Structured condition evaluation (evaluate_conditions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_conditions_simple_true_with_yes() -> None:
    """'simple: true' with 'yes: review' should jump to review."""
    conditions = [{"simple": "true", "yes": "review", "maxRepeats": 5}]
    result = await evaluate_conditions(conditions, {}, {}, {})
    assert result.jump_to == "review"
    assert result.matched is True


@pytest.mark.asyncio
async def test_evaluate_conditions_simple_false_with_no() -> None:
    """When condition evaluates to False, use the 'no' target."""
    conditions = [
        {"simple": "severity == major_issues || severity == blocking", "no": "test", "maxRepeats": 5}
    ]
    current_output = {"severity": "minor_issues"}
    result = await evaluate_conditions(conditions, {}, current_output, {})
    assert result.jump_to == "test"
    assert result.matched is True


@pytest.mark.asyncio
async def test_evaluate_conditions_no_jump_when_true_but_no_yes_field() -> None:
    """When condition is True but has no 'yes' field, skip to next condition."""
    conditions = [
        {"simple": "severity == major_issues", "no": "implementation"},
    ]
    current_output = {"severity": "major_issues"}
    result = await evaluate_conditions(conditions, {}, current_output, {})
    # True but no yes field -> skip; no more conditions -> no jump
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_max_repeats_exceeded() -> None:
    """When maxRepeats is exceeded, condition should be skipped."""
    conditions = [
        {"simple": "true", "yes": "review", "maxRepeats": 3}
    ]
    repeat_counts = {"review": 3}  # Already visited 3 times
    result = await evaluate_conditions(conditions, {}, {}, repeat_counts)
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_max_repeats_not_exceeded() -> None:
    conditions = [
        {"simple": "true", "yes": "review", "maxRepeats": 3}
    ]
    repeat_counts = {"review": 2}
    result = await evaluate_conditions(conditions, {}, {}, repeat_counts)
    assert result.jump_to == "review"
    assert result.matched is True


@pytest.mark.asyncio
async def test_evaluate_conditions_cross_stage_reference() -> None:
    """Cross-stage references like analysis.risks should resolve."""
    conditions = [
        {"simple": "analysis.estimated_complexity == high", "yes": "design"}
    ]
    stage_outputs = {"analysis": {"estimated_complexity": "high"}}
    result = await evaluate_conditions(conditions, stage_outputs, {}, {})
    assert result.jump_to == "design"


@pytest.mark.asyncio
async def test_evaluate_conditions_empty() -> None:
    result = await evaluate_conditions([], {}, {}, {})
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_multiple_fallthrough() -> None:
    """Multiple conditions: first match wins."""
    conditions = [
        {"simple": "severity == blocking", "no": "fix"},
        {"simple": "severity == major_issues", "no": "test"},
    ]
    current_output = {"severity": "minor_issues"}
    result = await evaluate_conditions(conditions, {}, current_output, {})
    # First condition: severity != blocking -> no: "fix"
    assert result.jump_to == "fix"


@pytest.mark.asyncio
async def test_evaluate_conditions_ai_without_evaluator() -> None:
    """AI conditions without an evaluator should be skipped."""
    conditions = [
        {"ai": "Is the code safe?", "yes": "deploy", "no": "fix"}
    ]
    result = await evaluate_conditions(conditions, {}, {}, {})
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_ai_with_evaluator() -> None:
    """AI conditions with evaluator should use the result."""
    async def mock_ai_evaluator(prompt: str, context: dict) -> bool:
        return True

    conditions = [
        {"ai": "Is the code safe?", "yes": "deploy", "no": "fix"}
    ]
    result = await evaluate_conditions(conditions, {}, {}, {}, ai_evaluator=mock_ai_evaluator)
    assert result.jump_to == "deploy"
    assert result.matched is True


@pytest.mark.asyncio
async def test_evaluate_conditions_ai_evaluator_returns_false() -> None:
    async def mock_ai_evaluator(prompt: str, context: dict) -> bool:
        return False

    conditions = [
        {"ai": "Is the code safe?", "yes": "deploy", "no": "fix"}
    ]
    result = await evaluate_conditions(conditions, {}, {}, {}, ai_evaluator=mock_ai_evaluator)
    assert result.jump_to == "fix"
    assert result.matched is True

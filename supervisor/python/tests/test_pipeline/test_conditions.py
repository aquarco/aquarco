"""Tests for pipeline condition checking."""

from __future__ import annotations

from aifishtank_supervisor.pipeline.executor import check_conditions


def test_equality_condition() -> None:
    output = {"status": "pass"}
    assert check_conditions(["status == pass"], output) is True
    assert check_conditions(["status == fail"], output) is False


def test_inequality_condition() -> None:
    output = {"status": "pass"}
    assert check_conditions(["status != fail"], output) is True
    assert check_conditions(["status != pass"], output) is False


def test_complexity_gte() -> None:
    output = {"analysis": {"estimated_complexity": "high"}}
    assert check_conditions(["analysis.estimated_complexity >= medium"], output) is True
    assert check_conditions(["analysis.estimated_complexity >= epic"], output) is False


def test_complexity_gt() -> None:
    output = {"analysis": {"estimated_complexity": "medium"}}
    assert check_conditions(["analysis.estimated_complexity > low"], output) is True
    assert check_conditions(["analysis.estimated_complexity > medium"], output) is False


def test_complexity_lte() -> None:
    output = {"analysis": {"estimated_complexity": "low"}}
    assert check_conditions(["analysis.estimated_complexity <= medium"], output) is True
    assert check_conditions(["analysis.estimated_complexity <= trivial"], output) is False


def test_complexity_lt() -> None:
    output = {"analysis": {"estimated_complexity": "medium"}}
    assert check_conditions(["analysis.estimated_complexity < high"], output) is True
    assert check_conditions(["analysis.estimated_complexity < medium"], output) is False


def test_empty_conditions() -> None:
    assert check_conditions([], {}) is True


def test_missing_field() -> None:
    assert check_conditions(["missing.field == value"], {}) is False


def test_multiple_conditions() -> None:
    output = {"status": "pass", "analysis": {"estimated_complexity": "high"}}
    conditions = [
        "status == pass",
        "analysis.estimated_complexity >= medium",
    ]
    assert check_conditions(conditions, output) is True

    conditions_fail = [
        "status == pass",
        "analysis.estimated_complexity >= epic",
    ]
    assert check_conditions(conditions_fail, output) is False


def test_nested_field_resolution() -> None:
    output = {"a": {"b": {"c": "value"}}}
    assert check_conditions(["a.b.c == value"], output) is True

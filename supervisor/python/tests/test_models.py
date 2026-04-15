"""Tests for Pydantic models."""

from __future__ import annotations

from aquarco_supervisor.models import (
    Complexity,
    PipelineConfig,
    StageConfig,
    Task,
    TaskStatus,
)


def test_task_defaults() -> None:
    task = Task(id="test-1", title="Test", category="analyze")
    assert task.status == TaskStatus.PENDING
    assert task.priority == 50
    assert task.retry_count == 0
    assert task.initial_context == {}


def test_complexity_ordering() -> None:
    assert Complexity.TRIVIAL < Complexity.LOW
    assert Complexity.LOW < Complexity.MEDIUM
    assert Complexity.MEDIUM < Complexity.HIGH
    assert Complexity.HIGH < Complexity.EPIC

    assert Complexity.MEDIUM >= Complexity.MEDIUM
    assert Complexity.HIGH >= Complexity.MEDIUM
    assert not (Complexity.LOW >= Complexity.MEDIUM)

    assert Complexity.MEDIUM <= Complexity.MEDIUM
    assert Complexity.LOW <= Complexity.MEDIUM
    assert not (Complexity.HIGH <= Complexity.MEDIUM)


def test_complexity_order() -> None:
    assert Complexity.TRIVIAL._order == 0
    assert Complexity.EPIC._order == 4


def test_complexity_comparisons() -> None:
    assert Complexity.LOW <= Complexity.HIGH
    assert Complexity.HIGH > Complexity.LOW
    assert Complexity.LOW < Complexity.HIGH
    assert Complexity.HIGH >= Complexity.MEDIUM
    # Non-Complexity comparisons return NotImplemented
    assert Complexity.LOW.__ge__("string") is NotImplemented
    assert Complexity.LOW.__gt__("string") is NotImplemented
    assert Complexity.LOW.__le__("string") is NotImplemented
    assert Complexity.LOW.__lt__("string") is NotImplemented


def test_task_status_values() -> None:
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.EXECUTING.value == "executing"
    assert TaskStatus.COMPLETED.value == "completed"


def test_task_status_cancelled_exists() -> None:
    """CANCELLED status was added and has the correct value."""
    assert TaskStatus.CANCELLED.value == "cancelled"
    # Verify it's a valid member of the enum
    assert TaskStatus("cancelled") == TaskStatus.CANCELLED


def test_task_status_all_values() -> None:
    """All expected task statuses exist in the enum."""
    expected = {
        "pending", "queued", "planning", "executing", "completed",
        "failed", "timeout", "cancelled", "rate_limited", "closed",
    }
    actual = {s.value for s in TaskStatus}
    assert actual == expected


def test_stage_config_name_field() -> None:
    stage = StageConfig(name="analysis", category="analyze")
    assert stage.name == "analysis"
    assert stage.category == "analyze"
    assert stage.conditions == []
    assert stage.required is True


def test_stage_config_name_default() -> None:
    stage = StageConfig(category="review")
    assert stage.name == ""


def test_stage_config_structured_conditions() -> None:
    conditions = [
        {"simple": "severity == major_issues", "no": "implementation", "maxRepeats": 3},
        {"ai": "All risks mitigated?", "no": "fix", "maxRepeats": 5},
    ]
    stage = StageConfig(name="review", category="review", conditions=conditions)
    assert len(stage.conditions) == 2
    assert stage.conditions[0]["simple"] == "severity == major_issues"
    assert stage.conditions[1]["ai"] == "All risks mitigated?"


def test_pipeline_config_categories() -> None:
    categories = {
        "analyze": {"type": "object", "required": ["risks"]},
        "design": {"type": "object"},
    }
    pipeline = PipelineConfig(
        name="test-pipeline",

        stages=[StageConfig(name="s1", category="analyze")],
        categories=categories,
    )
    assert pipeline.categories["analyze"]["type"] == "object"
    assert "design" in pipeline.categories


def test_pipeline_config_categories_default() -> None:
    pipeline = PipelineConfig(
        name="test-pipeline",

        stages=[StageConfig(name="s1", category="analyze")],
    )
    assert pipeline.categories == {}

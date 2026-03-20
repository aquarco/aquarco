"""Tests for Pydantic models."""

from __future__ import annotations

from aquarco_supervisor.models import (
    Complexity,
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

"""Tests for context accumulation."""

from __future__ import annotations

from aquarco_supervisor.pipeline.context import build_accumulated_context


def test_recent_stages_full_output() -> None:
    task_context = {
        "task": {"id": "test-1"},
        "stages": [
            {
                "stage_number": 0, "category": "analyze", "agent": "a1",
                "status": "completed", "summary": "s0", "full_output": "big data",
            },
            {
                "stage_number": 1, "category": "design", "agent": "a2",
                "status": "completed", "summary": "s1", "full_output": "more data",
            },
            {
                "stage_number": 2, "category": "impl", "agent": "a3",
                "status": "completed", "summary": "s2", "full_output": "code",
            },
        ],
        "context_entries": [{"key": "test", "value": "val"}],
    }

    result = build_accumulated_context(task_context, current_stage=3)

    assert result["current_stage"] == 3
    assert "previous_output" not in result
    assert len(result["context_entries"]) == 1

    # Stage 0 is 3 stages back (> 2), so should be summarized
    assert "full_output" not in result["stage_history"][0]
    assert result["stage_history"][0]["category"] == "analyze"

    # Stages 1 and 2 are within 2, should be full
    assert "full_output" in result["stage_history"][1]
    assert "full_output" in result["stage_history"][2]


def test_empty_stages() -> None:
    result = build_accumulated_context(
        {"task": {"id": "t1"}, "stages": []},
        current_stage=0,
    )
    assert result["stage_history"] == []
    assert "previous_output" not in result


def test_input_field_stripped() -> None:
    """The 'input' field duplicates data from earlier stages and should be removed."""
    task_context = {
        "task": {"id": "t1"},
        "stages": [
            {
                "stage_number": 0, "category": "analyze", "agent": "a1",
                "status": "completed", "input": {"huge": "data" * 100},
                "structured_output": {"summary": "analyzed"},
            },
        ],
    }

    result = build_accumulated_context(task_context, current_stage=1)
    assert len(result["stage_history"]) == 1
    assert "input" not in result["stage_history"][0]
    assert result["stage_history"][0]["structured_output"] == {"summary": "analyzed"}


def test_raw_output_stripped() -> None:
    """raw_output is bulky and should be stripped."""
    task_context = {
        "task": {"id": "t1"},
        "stages": [
            {
                "stage_number": 0, "category": "analyze", "agent": "a1",
                "status": "completed", "raw_output": "x" * 5000,
                "structured_output": {"ok": True},
            },
        ],
    }

    result = build_accumulated_context(task_context, current_stage=1)
    assert "raw_output" not in result["stage_history"][0]


def test_failed_stages_excluded() -> None:
    """Failed stages provide no useful output and should be excluded."""
    task_context = {
        "task": {"id": "t1"},
        "stages": [
            {
                "stage_number": 0, "category": "analyze", "agent": "a1",
                "status": "completed", "structured_output": {"ok": True},
            },
            {
                "stage_number": 1, "category": "design", "agent": "a2",
                "status": "failed", "error_message": "boom",
            },
            {
                "stage_number": 1, "category": "design", "agent": "a2",
                "status": "completed", "run": 2,
                "structured_output": {"design": "done"},
            },
        ],
    }

    result = build_accumulated_context(task_context, current_stage=2)
    # Only completed stages should appear
    assert len(result["stage_history"]) == 2
    statuses = [s["status"] for s in result["stage_history"]]
    assert "failed" not in statuses


def test_future_stages_excluded() -> None:
    """Stages at or beyond current_stage should not be in context."""
    task_context = {
        "task": {"id": "t1"},
        "stages": [
            {
                "stage_number": 0, "category": "analyze", "agent": "a1",
                "status": "completed", "structured_output": {"ok": True},
            },
            {
                "stage_number": 1, "category": "design", "agent": "a2",
                "status": "pending",
            },
            {
                "stage_number": 2, "category": "impl", "agent": "a3",
                "status": "pending",
            },
        ],
    }

    result = build_accumulated_context(task_context, current_stage=1)
    assert len(result["stage_history"]) == 1
    assert result["stage_history"][0]["category"] == "analyze"


def test_pending_stages_excluded() -> None:
    """Pending stages should be excluded even if their stage_number < current."""
    task_context = {
        "task": {"id": "t1"},
        "stages": [
            {
                "stage_number": 0, "category": "analyze", "agent": "a1",
                "status": "completed", "structured_output": {"ok": True},
            },
            {
                "stage_number": 1, "category": "design", "agent": "a2",
                "status": "pending",
            },
        ],
    }

    result = build_accumulated_context(task_context, current_stage=2)
    assert len(result["stage_history"]) == 1
    assert result["stage_history"][0]["status"] == "completed"

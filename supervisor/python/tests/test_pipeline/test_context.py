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

    result = build_accumulated_context(task_context, current_stage=3, previous_output={"x": 1})

    assert result["current_stage"] == 3
    assert result["previous_output"] == {"x": 1}
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
        previous_output=None,
    )
    assert result["stage_history"] == []
    assert result["previous_output"] == {}

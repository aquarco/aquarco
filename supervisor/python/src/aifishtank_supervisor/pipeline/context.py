"""Context accumulation for pipeline stages."""

from __future__ import annotations

from typing import Any

from ..logging import get_logger

log = get_logger("context")

# Fields to keep when summarizing older stages
SUMMARY_FIELDS = ("stage_number", "category", "agent", "status", "summary")


def build_accumulated_context(
    task_context: dict[str, Any],
    current_stage: int,
    previous_output: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the accumulated context for a pipeline stage.

    Recent stages (within 2 of current) get full output.
    Older stages get only summary fields.
    """
    stage_history = []
    stages = task_context.get("stages", [])

    for stage in stages:
        stage_num = stage.get("stage_number", 0)
        if current_stage - stage_num <= 2:
            # Recent stage: include full output
            stage_history.append(stage)
        else:
            # Older stage: include only summary
            summary = {k: stage.get(k) for k in SUMMARY_FIELDS if k in stage}
            stage_history.append(summary)

    return {
        "task": task_context.get("task", {}),
        "current_stage": current_stage,
        "previous_output": previous_output or {},
        "stage_history": stage_history,
        "context_entries": task_context.get("context_entries", []),
    }

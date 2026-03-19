"""Context accumulation for pipeline stages."""

from __future__ import annotations

from typing import Any

from ..logging import get_logger

log = get_logger("context")

# Fields to keep when summarizing older stages
SUMMARY_FIELDS = ("stage_number", "category", "agent", "status", "summary", "stage_key", "iteration")


def build_accumulated_context(
    task_context: dict[str, Any],
    current_stage: int,
    previous_output: dict[str, Any] | None,
    *,
    validation_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the accumulated context for a pipeline stage.

    Recent stages (within 2 of current) get full output.
    Older stages get only summary fields.
    Multi-agent stages at the same stage_number are grouped together.
    """
    stage_history = []
    stages = task_context.get("stages", [])

    # Group stages by stage_number to handle multi-agent categories
    stages_by_num: dict[int, list[dict[str, Any]]] = {}
    for stage in stages:
        stage_num = stage.get("stage_number", 0)
        stages_by_num.setdefault(stage_num, []).append(stage)

    for stage_num in sorted(stages_by_num):
        group = stages_by_num[stage_num]
        if current_stage - stage_num <= 2:
            # Recent stages: include full output
            for stage in group:
                stage_history.append(stage)
        else:
            # Older stages: include only summary
            for stage in group:
                summary = {k: stage.get(k) for k in SUMMARY_FIELDS if k in stage}
                stage_history.append(summary)

    result: dict[str, Any] = {
        "task": task_context.get("task", {}),
        "current_stage": current_stage,
        "previous_output": previous_output or {},
        "stage_history": stage_history,
        "context_entries": task_context.get("context_entries", []),
    }

    # Include validation items to address (for iteration re-runs)
    if validation_items:
        result["validation_items_to_address"] = validation_items

    # Include all open validation items from task context
    all_vi = task_context.get("validation_items", [])
    if all_vi:
        open_vi = [vi for vi in all_vi if vi.get("status") == "open"]
        if open_vi:
            result["open_validation_items"] = open_vi

    return result

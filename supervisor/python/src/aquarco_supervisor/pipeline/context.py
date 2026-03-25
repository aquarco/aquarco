"""Context accumulation for pipeline stages."""

from __future__ import annotations

from typing import Any

from ..logging import get_logger

log = get_logger("context")

# Fields to keep when summarizing older stages
SUMMARY_FIELDS = ("stage_number", "category", "agent", "status", "summary", "stage_key", "iteration")

# Fields to strip from every stage — they duplicate data already passed to earlier
# stages and bloat the context window without adding value.
_STRIP_FIELDS = frozenset(("input", "raw_output"))

# Statuses that provide no useful output for downstream stages
_USELESS_STATUSES = frozenset(("failed", "pending", "rate_limited"))


def _clean_stage(stage: dict[str, Any]) -> dict[str, Any]:
    """Remove bulky/redundant fields from a stage entry."""
    return {k: v for k, v in stage.items() if k not in _STRIP_FIELDS}


def build_accumulated_context(
    task_context: dict[str, Any],
    current_stage: int,
    *,
    validation_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the accumulated context for a pipeline stage.

    Recent completed stages (within 2 of current) get full output.
    Older stages get only summary fields.
    Failed, pending, and future stages are excluded entirely.
    The ``input`` field is always stripped — it duplicates data the
    previous stage already received.

    Agents can find the previous stage's output in the last entry of
    ``stage_history`` — there is no separate ``previous_output`` key.
    """
    stage_history = []
    stages = task_context.get("stages", [])

    # Group stages by stage_number to handle multi-agent categories
    stages_by_num: dict[int, list[dict[str, Any]]] = {}
    for stage in stages:
        stage_num = stage.get("stage_number", 0)
        stages_by_num.setdefault(stage_num, []).append(stage)

    for stage_num in sorted(stages_by_num):
        # Skip future stages — they have no output yet
        if stage_num >= current_stage:
            continue

        group = stages_by_num[stage_num]
        if current_stage - stage_num <= 2:
            # Recent stages: include full output (minus redundant fields)
            for stage in group:
                if stage.get("status") in _USELESS_STATUSES:
                    continue
                stage_history.append(_clean_stage(stage))
        else:
            # Older stages: include only summary
            for stage in group:
                if stage.get("status") in _USELESS_STATUSES:
                    continue
                summary = {k: stage.get(k) for k in SUMMARY_FIELDS if k in stage}
                stage_history.append(summary)

    result: dict[str, Any] = {
        "task": task_context.get("task", {}),
        "current_stage": current_stage,
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

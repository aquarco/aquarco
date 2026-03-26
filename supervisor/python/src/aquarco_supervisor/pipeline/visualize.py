"""Pipeline visualization utilities.

Renders pipeline definitions as text/markdown showing all possible
branches, conditional paths, and loop structures.
"""

from __future__ import annotations

from typing import Any

from ..models import LoopConfig


def format_pipeline_stages(
    stages: list[dict[str, Any]],
    *,
    markdown: bool = True,
) -> str:
    """Render pipeline stages as a readable diagram showing all branches.

    Produces a compact text representation that visualizes:
    - Linear stage flow (arrows between stages)
    - Conditional entry (when ``conditions`` are present)
    - Loop bodies with back-edges (when ``loop`` is configured)

    Parameters
    ----------
    stages:
        List of stage definition dicts (from pipeline config).
        Each must have at least ``category``; may have ``conditions``,
        ``loop``, and ``required``.
    markdown:
        If True, wrap the output in a markdown code block.

    Returns
    -------
    str
        Multi-line string with the pipeline visualization.
    """
    if not stages:
        return "(empty pipeline)"

    lines: list[str] = []

    for i, stage in enumerate(stages):
        category = stage.get("category", "unknown")
        required = stage.get("required", True)
        conditions = stage.get("conditions", [])
        loop_data = stage.get("loop")

        # Stage header
        req_marker = "" if required else " (optional)"
        stage_label = f"[{i}] {category}{req_marker}"

        # Entry conditions
        if conditions:
            cond_str = " AND ".join(conditions)
            lines.append(f"  {'|' if i > 0 else ' '}")
            lines.append(f"  v  if: {cond_str}")
        elif i > 0:
            lines.append("  |")
            lines.append("  v")

        lines.append(f"  {stage_label}")

        # Loop annotation
        if loop_data:
            loop_cfg = _parse_loop(loop_data)
            if loop_cfg:
                loop_stages = loop_cfg.loop_stages
                if not loop_stages:
                    loop_stages = [category]

                body_label = " -> ".join(loop_stages)
                mode_label = (
                    "AI" if loop_cfg.eval_mode == "ai" else "simple"
                )

                lines.append(
                    f"  +--[ LOOP: {body_label} ]"
                    f"  (max {loop_cfg.max_repeats}x, {mode_label})"
                )
                lines.append(
                    f"  |   exit when: {loop_cfg.condition}"
                )

                # Show which stages are part of the loop body
                body_indices = _resolve_body_indices(
                    loop_stages, stages,
                )
                if body_indices and body_indices != [i]:
                    idx_labels = [
                        f"[{idx}] {stages[idx].get('category', '?')}"
                        for idx in body_indices
                    ]
                    lines.append(
                        f"  |   body: {' -> '.join(idx_labels)}"
                    )

                lines.append("  +--^")

    # Final marker
    lines.append("  |")
    lines.append("  * done")

    body = "\n".join(lines)
    if markdown:
        return f"```\n{body}\n```"
    return body


def format_pipeline_stages_markdown(
    pipeline_name: str,
    stages: list[dict[str, Any]],
) -> str:
    """Render a complete markdown section for a pipeline's stages.

    Includes a heading, the stage diagram, and a legend explaining
    the notation.
    """
    diagram = format_pipeline_stages(stages, markdown=True)

    legend = (
        "**Legend:** "
        "`[n]` = stage index, "
        "`if:` = entry condition, "
        "`LOOP` = conditional repeat, "
        "`(optional)` = non-required stage, "
        "`exit when:` = loop exit condition"
    )

    return (
        f"## Pipeline Stages: {pipeline_name}\n\n"
        f"{diagram}\n\n"
        f"{legend}\n"
    )


def _parse_loop(loop_data: Any) -> LoopConfig | None:
    """Parse loop data from various forms."""
    if isinstance(loop_data, LoopConfig):
        return loop_data
    if isinstance(loop_data, dict):
        try:
            return LoopConfig(**loop_data)
        except Exception:
            return None
    return None


def _resolve_body_indices(
    loop_stages: list[str],
    stage_defs: list[dict[str, Any]],
) -> list[int]:
    """Find stage indices whose category matches the loop_stages list."""
    indices = []
    for i, sdef in enumerate(stage_defs):
        if sdef.get("category") in loop_stages:
            indices.append(i)
    return indices

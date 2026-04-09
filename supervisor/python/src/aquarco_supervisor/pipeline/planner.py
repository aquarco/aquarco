"""Pipeline planning phase - builds or AI-generates stage plans.

Extracted from executor.py to isolate planning logic from execution.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..exceptions import PipelineError, RetryableError
from ..logging import get_logger
from ..models import TaskStatus
from ..stage_manager import StageManager
from ..task_queue import TaskQueue
from .agent_invoker import AgentInvoker
from .agent_registry import AgentRegistry

log = get_logger("planner")


class PipelinePlanner:
    """Handles the planning phase of pipeline execution."""

    def __init__(
        self,
        tq: TaskQueue,
        sm: StageManager,
        registry: AgentRegistry,
        invoker: AgentInvoker,
        next_execution_order: Callable[[str], int],
    ) -> None:
        self._tq = tq
        self._sm = sm
        self._registry = registry
        self._invoker = invoker
        self._next_execution_order = next_execution_order

    def build_default_plan(
        self, stages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Fast path: each category has exactly one agent, no planning needed."""
        planned: list[dict[str, Any]] = []
        for stage_def in stages:
            category = stage_def["category"]
            agents = self._registry.get_agents_for_category(category)
            planned.append({
                "category": category,
                "agents": agents[:1],
                "parallel": False,
                "validation": [],
            })
        return planned

    async def execute_planning_phase(
        self,
        task_id: str,
        pipeline_name: str,
        stages: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run the planner agent to assign agents to pipeline categories."""
        log.info("planning_phase_start", task_id=task_id, pipeline=pipeline_name)

        agent_defs = await self._registry.get_all_agent_definitions_json()
        categories = [s["category"] for s in stages]

        planner_context = {
            "task_id": task_id,
            "pipeline_name": pipeline_name,
            "pipeline_categories": categories,
            "pipeline_stages": stages,
            "task_context": context,
            "available_agents": agent_defs,
        }

        # Create a planning stage at stage_number = -1
        planning_stage_key = "-1:planning:planner-agent"
        planning_stage_id = await self._sm.create_system_stage(
            task_id, -1, "planning", "planner-agent",
            stage_key=planning_stage_key,
        )
        planning_eo = self._next_execution_order(task_id)
        await self._sm.record_stage_executing(
            task_id, -1, "planning", "planner-agent",
            stage_id=planning_stage_id,
            stage_key=planning_stage_key, iteration=1,
            execution_order=planning_eo,
        )

        try:
            output = await self._invoker.execute_agent(
                "planner-agent", task_id, planner_context, -1
            )
        except RetryableError as e:
            await self._sm.record_stage_failed(
                task_id, -1, str(e),
                stage_id=planning_stage_id,
                stage_key=planning_stage_key,
            )
            raise
        except Exception as e:
            await self._sm.record_stage_failed(
                task_id, -1, str(e),
                stage_id=planning_stage_id,
                stage_key=planning_stage_key,
            )
            raise PipelineError(f"Planning phase failed: {e}") from e

        await self._sm.store_stage_output(
            task_id, -1, "planning", "planner-agent", output,
            stage_id=planning_stage_id,
            stage_key=planning_stage_key, iteration=1,
        )

        planned_stages = output.get("planned_stages", [])
        if not planned_stages:
            raise PipelineError("Planner returned empty planned_stages")

        # Validate: every required category has agents assigned
        planned_categories = {p["category"] for p in planned_stages}
        for stage_def in stages:
            if stage_def.get("required", True):
                cat = stage_def["category"]
                if cat not in planned_categories:
                    raise PipelineError(
                        f"Planner did not assign agents for required category '{cat}'"
                    )

        log.info(
            "planning_phase_complete",
            task_id=task_id,
            stages_planned=len(planned_stages),
        )
        return planned_stages

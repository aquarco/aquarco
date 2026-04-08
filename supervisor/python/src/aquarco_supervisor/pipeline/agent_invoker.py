"""Agent invocation with automatic max-turns continuation.

Extracted from executor.py to isolate the Claude CLI interaction loop
from pipeline orchestration logic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from ..cli.claude import _scan_file_for_rate_limit_event
from ..config import get_pipeline_categories
from ..database import Database
from ..exceptions import RateLimitError
from ..logging import get_logger
from ..models import PipelineConfig
from .agent_registry import AgentRegistry

log = get_logger("agent-invoker")


class AgentInvoker:
    """Invokes Claude CLI for agents with automatic max-turns continuation."""

    def __init__(
        self,
        db: Database,
        registry: AgentRegistry,
        pipelines: list[PipelineConfig],
    ) -> None:
        self._db = db
        self._registry = registry
        self._pipelines = pipelines
        # Late-resolve executor module so test mocks on executor.execute_claude
        # and executor.Path take effect at call time.
        from . import executor as _exec
        self._exec = _exec

    def get_output_schema_for_stage(
        self,
        pipeline_name: str,
        category: str,
        agent_name: str,
    ) -> dict[str, Any] | None:
        """Resolve output schema: pipeline categories first, then agent spec fallback."""
        # Try pipeline-level categories
        categories = get_pipeline_categories(self._pipelines, pipeline_name)
        if categories and category in categories:
            schema = categories[category]
            if schema:
                return schema

        # Fallback: agent-level outputSchema
        return self._registry.get_agent_output_schema(agent_name)

    async def execute_agent(
        self,
        agent_name: str,
        task_id: str,
        context: dict[str, Any],
        stage_num: int,
        *,
        work_dir: str | None = None,
        on_live_output: Callable[[str], Awaitable[None]] | None = None,
        pipeline_name: str = "",
        category: str = "",
        resume_session_id: str | None = None,
        resolve_clone_dir: Callable[[str], Any] | None = None,
    ) -> dict[str, Any]:
        """Invoke the Claude CLI for an agent, with automatic continuation.

        If the agent hits max_turns, automatically resumes the session until
        the work is complete or the cumulative cost exceeds maxCost.

        When ``resume_session_id`` is provided (e.g. from a previous
        rate-limited run), the first CLI invocation uses ``--resume`` to
        continue that conversation instead of starting fresh.
        """
        prompt_file = self._registry.get_agent_prompt_file(agent_name)
        timeout_minutes = self._registry.get_agent_timeout(agent_name)
        max_turns = self._registry.get_agent_max_turns(agent_name)
        max_cost = self._registry.get_agent_max_cost(agent_name)
        model = self._registry.get_agent_model(agent_name)

        if work_dir:
            clone_dir = work_dir
        elif resolve_clone_dir:
            clone_dir = await resolve_clone_dir(task_id)
        else:
            raise ValueError("Either work_dir or resolve_clone_dir must be provided")

        agent_context = {
            "task_id": task_id,
            "agent": agent_name,
            "stage_number": stage_num,
            "accumulated_context": context,
        }

        cumulative_cost = 0.0
        cumulative_input = 0
        cumulative_cache_read = 0
        cumulative_cache_write = 0
        cumulative_output = 0
        iteration = 0
        last_successful_output: dict[str, Any] | None = None

        while True:
            claude_output = await self._exec.execute_claude(
                prompt_file=prompt_file,
                context=agent_context,
                work_dir=clone_dir,
                timeout_seconds=timeout_minutes * 60,
                allowed_tools=self._registry.get_allowed_tools(agent_name),
                denied_tools=self._registry.get_denied_tools(agent_name),
                task_id=task_id,
                stage_num=stage_num,
                extra_env=self._registry.get_agent_environment(agent_name),
                output_schema=self.get_output_schema_for_stage(
                    pipeline_name, category, agent_name,
                ) if pipeline_name and category else self._registry.get_agent_output_schema(agent_name),
                max_turns=max_turns,
                resume_session_id=resume_session_id,
                on_live_output=on_live_output,
                model=model,
            )

            output = claude_output.structured
            iteration_cost = output.get("_cost_usd", 0.0)
            if "_cost_usd" not in output:
                log.warning(
                    "cost_usd_missing_from_output",
                    task_id=task_id,
                    stage=stage_num,
                    agent=agent_name,
                    iteration=iteration,
                )
            cumulative_cost += iteration_cost
            cumulative_input += output.get("_input_tokens", 0)
            cumulative_cache_read += output.get("_cache_read_tokens", 0)
            cumulative_cache_write += output.get("_cache_write_tokens", 0)
            cumulative_output += output.get("_output_tokens", 0)
            output["_cumulative_cost_usd"] = cumulative_cost
            output["_cumulative_input_tokens"] = cumulative_input
            output["_cumulative_cache_read_tokens"] = cumulative_cache_read
            output["_cumulative_cache_write_tokens"] = cumulative_cache_write
            output["_cumulative_output_tokens"] = cumulative_output
            iteration += 1

            # Preserve last successful structured output (non-error)
            if not output.get("_no_structured_output"):
                last_successful_output = dict(output)

            # Check if agent hit max_turns and can be continued
            if output.get("_subtype") == "error_max_turns":
                session_id = output.get("_session_id")
                if not session_id:
                    log.warning(
                        "max_turns_no_session_id",
                        task_id=task_id,
                        stage=stage_num,
                        agent=agent_name,
                    )
                    break

                if cumulative_cost >= max_cost:
                    log.warning(
                        "max_turns_cost_exceeded",
                        task_id=task_id,
                        stage=stage_num,
                        agent=agent_name,
                        cumulative_cost=cumulative_cost,
                        max_cost=max_cost,
                        iterations=iteration,
                    )
                    break

                log.info(
                    "max_turns_continuing",
                    task_id=task_id,
                    stage=stage_num,
                    agent=agent_name,
                    session_id=session_id[:8] + "..." if session_id else None,
                    cumulative_cost=cumulative_cost,
                    max_cost=max_cost,
                    iteration=iteration,
                )
                resume_session_id = session_id
                continue

            # Normal completion
            break

        # If final iteration lacks structured data, fall back to last successful output
        if output.get("_no_structured_output") and last_successful_output:
            last_successful_output["_cumulative_cost_usd"] = cumulative_cost
            last_successful_output["_cumulative_input_tokens"] = cumulative_input
            last_successful_output["_cumulative_cache_read_tokens"] = cumulative_cache_read
            last_successful_output["_cumulative_cache_write_tokens"] = cumulative_cache_write
            last_successful_output["_cumulative_output_tokens"] = cumulative_output
            output = last_successful_output

        output["_agent_name"] = agent_name
        output["_iterations"] = iteration

        # Detect rate-limit delivered as is_error=True inside a "success" result.
        if output.get("_is_error"):
            resets_at: str | None = None
            rate_event_line: str | None = None
            for _line in (claude_output.raw or "").splitlines():
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _msg = json.loads(_line)
                    if isinstance(_msg, dict) and _msg.get("type") == "rate_limit_event":
                        rate_event_line = _line
                        break
                except json.JSONDecodeError:
                    continue
            # Slow path: stream-scan the full file if not found in tail.
            if rate_event_line is None and claude_output.raw_output_path:
                rate_event_line = _scan_file_for_rate_limit_event(
                    self._exec.Path(claude_output.raw_output_path)
                )
            if rate_event_line:
                try:
                    _msg = json.loads(rate_event_line)
                    resets_at = (_msg.get("rate_limit_info") or {}).get("resetsAt")
                except json.JSONDecodeError:
                    pass
                raise RateLimitError(
                    f"Claude API rate limited (rate_limit_event); resetsAt={resets_at} "
                    f"(task={task_id}, stage={stage_num})",
                    session_id=output.get("_session_id"),
                )

        # Carry raw NDJSON log so store_stage_output persists it to raw_output column
        output["_raw_output"] = claude_output.raw

        # Save output log (sanitize task_id to prevent path traversal)
        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)
        _Path = self._exec.Path
        output_log = _Path(f"/var/log/aquarco/agent-output-{safe_id}-stage{stage_num}.json")
        output_log.parent.mkdir(parents=True, exist_ok=True)
        output_log.write_text(json.dumps(output, indent=2))

        return output

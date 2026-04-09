"""Pipeline stage runner - executes planned stages with condition evaluation.

Extracted from executor.py to isolate the main execution loop and
condition-driven exit-gate logic from pipeline orchestration.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..database import Database
from ..exceptions import (
    NoAvailableAgentError,
    RetryableError,
    StageError,
    _cooldown_for_error,
)
from ..logging import get_logger
from ..stage_manager import StageManager
from ..task_queue import TaskQueue
from .agent_invoker import AgentInvoker
from .agent_registry import AgentRegistry
from .conditions import ConditionResult, evaluate_ai_condition, evaluate_conditions
from .context import build_accumulated_context

log = get_logger("stage-runner")


class StageRunner:
    """Executes pipeline stages with condition-driven exit gates."""

    def __init__(
        self,
        db: Database,
        tq: TaskQueue,
        sm: StageManager,
        registry: AgentRegistry,
        invoker: AgentInvoker,
        next_execution_order: Callable[[str], int],
    ) -> None:
        self._db = db
        self._tq = tq
        self._sm = sm
        self._registry = registry
        self._invoker = invoker
        self._next_execution_order = next_execution_order
        # Late-resolve executor module so test mocks on executor._run_git,
        # executor._git_checkout, executor._auto_commit etc. take effect.
        from . import executor as _exec
        self._exec = _exec

    # -----------------------------------------------------------------------
    # Main execution loop
    # -----------------------------------------------------------------------

    async def execute_running_phase(
        self,
        task_id: str,
        planned_stages: list[dict[str, Any]],
        stage_defs: list[dict[str, Any]],
        clone_dir: str,
        branch_name: str,
        *,
        start_stage: int = 0,
        pipeline_name: str = "",
        stage_ids: dict[str, int] | None = None,
    ) -> bool:
        """Execute planned stages with condition-driven exit gates and named jumps.

        Returns True if the pipeline failed and should not continue.
        """
        if stage_ids is None:
            stage_ids = {}

        # Build name-indexed lookups for named-stage jumps
        stage_order: list[str] = []
        stages_by_name: dict[str, int] = {}

        for idx, sdef in enumerate(stage_defs):
            name = sdef.get("name", "")
            if name:
                stage_order.append(name)
                stages_by_name[name] = idx
            else:
                stage_order.append(str(idx))

        # Track outputs per stage name for cross-stage condition references
        stage_outputs: dict[str, dict[str, Any]] = {}
        repeat_counts: dict[str, int] = {}
        stage_iterations: dict[str, int] = {}

        current_idx = start_stage
        previous_output: dict[str, Any] = {}
        prev_completed_stage_id: int | None = None

        while current_idx < len(planned_stages):
            stage_num = current_idx
            plan = planned_stages[stage_num]

            category = plan["category"]
            raw_agents = plan.get("agents", [])
            agents = [
                a.get("name") or a.get("agent_name") or a
                if isinstance(a, dict) else a
                for a in raw_agents
            ]
            parallel = plan.get("parallel", False)

            stage_def = (
                stage_defs[stage_num] if stage_num < len(stage_defs) else {}
            )
            required = stage_def.get("required", True)
            conditions = stage_def.get("conditions", [])
            stage_name = stage_def.get("name", str(stage_num))

            repeat_counts[stage_name] = repeat_counts.get(stage_name, 0) + 1

            if stage_name not in stage_iterations:
                stage_iterations[stage_name] = 1
            base_iteration = stage_iterations[stage_name]

            # On revisit (repeat > 1), create new iteration stage rows
            if repeat_counts[stage_name] > 1:
                base_iteration = stage_iterations[stage_name]
                for agent_name in agents:
                    sk, new_id = await self._sm.create_iteration_stage(
                        task_id, stage_num, category, agent_name,
                        base_iteration,
                    )
                    if new_id is not None:
                        stage_ids[sk] = new_id

            # Ensure we're on the right branch
            await self._exec._git_checkout(clone_dir, branch_name)

            current_agent_stage_ids: dict[str, int | None] = {}

            try:
                if parallel and len(agents) > 1:
                    parallel_eos: dict[str, int] = {}
                    for a in agents:
                        parallel_eos[a] = self._next_execution_order(task_id)
                    stage_output = await self.execute_parallel_agents(
                        task_id, stage_num, category, agents,
                        clone_dir, branch_name,
                        pipeline_name=pipeline_name,
                        stage_ids=stage_ids,
                        execution_orders=parallel_eos,
                    )
                else:
                    stage_output = {}
                    per_agent_output: dict[str, dict[str, Any]] = {}
                    for agent_name in agents:
                        sk = f"{stage_num}:{category}:{agent_name}"
                        task_context = await self._sm.get_task_context(task_id) or {}
                        accumulated = build_accumulated_context(
                            task_context, stage_num,
                        )
                        eo = self._next_execution_order(task_id)
                        out, sid = await self.execute_planned_stage(
                            task_id, stage_num, category, agent_name,
                            accumulated, iteration=base_iteration,
                            stage_id=stage_ids.get(sk),
                            work_dir=clone_dir,
                            pipeline_name=pipeline_name,
                            execution_order=eo,
                        )
                        per_agent_output[agent_name] = out
                        current_agent_stage_ids[agent_name] = sid
                        stage_output.update(out)

                previous_output = stage_output
                stage_outputs[stage_name] = stage_output

                # Checkpoint and auto-commit
                completed_stage_id: int | None = None
                for _aid in current_agent_stage_ids.values():
                    if _aid is not None:
                        completed_stage_id = _aid
                        break
                if completed_stage_id is None:
                    for agent_name in agents:
                        sk = f"{stage_num}:{category}:{agent_name}"
                        if stage_ids.get(sk) is not None:
                            completed_stage_id = stage_ids[sk]
                            break
                if completed_stage_id is not None:
                    await self._sm.update_checkpoint(task_id, completed_stage_id)
                    prev_completed_stage_id = completed_stage_id
                await self._exec._auto_commit(clone_dir, task_id, stage_num, category)

                # --- Exit gate: evaluate structured conditions ---
                if conditions:
                    cond_result = await self._evaluate_stage_conditions(
                        task_id, stage_num, category, clone_dir,
                        conditions, stage_outputs, stage_output,
                        repeat_counts, stage_ids,
                        cond_eval_iteration=repeat_counts[stage_name],
                    )
                    if cond_result.jump_to and cond_result.jump_to in stages_by_name:
                        target_idx = stages_by_name[cond_result.jump_to]
                        target_name = cond_result.jump_to
                        if repeat_counts.get(target_name, 0) > 0:
                            next_iter = stage_iterations.get(target_name, 1) + 1
                            stage_iterations[target_name] = next_iter
                        log.info(
                            "condition_jump",
                            task_id=task_id,
                            from_stage=stage_name,
                            to_stage=target_name,
                            target_idx=target_idx,
                            condition_message=cond_result.message[:200] if cond_result.message else "",
                        )
                        if cond_result.message:
                            stage_outputs[stage_name]["_condition_message"] = cond_result.message
                            for _agent, _sid in current_agent_stage_ids.items():
                                if _sid is not None:
                                    await self._db.execute(
                                        """
                                        UPDATE stages
                                        SET structured_output = jsonb_set(
                                            COALESCE(structured_output, '{}'::jsonb),
                                            '{_condition_message}',
                                            %(msg)s::jsonb
                                        )
                                        WHERE id = %(id)s
                                        """,
                                        {
                                            "id": _sid,
                                            "msg": json.dumps(cond_result.message),
                                        },
                                    )
                        current_idx = target_idx
                        continue

                # Default: advance to next stage
                current_idx += 1

            except RetryableError as e:
                if prev_completed_stage_id is not None:
                    await self._sm.update_checkpoint(task_id, prev_completed_stage_id)
                cooldown_minutes, max_retries = _cooldown_for_error(e)
                await self._tq.postpone_task(
                    task_id, str(e),
                    cooldown_minutes=cooldown_minutes,
                    max_retries=max_retries,
                )
                _sid = getattr(e, "session_id", None)
                for agent_name in agents:
                    agent_stage_id = current_agent_stage_ids.get(agent_name)
                    if agent_stage_id is not None:
                        await self._db.execute(
                            """
                            UPDATE stages SET status = 'rate_limited', completed_at = NOW(),
                                   error_message = %(error)s,
                                   session_id = COALESCE(%(sid)s, session_id)
                            WHERE id = %(id)s
                            """,
                            {"id": agent_stage_id, "error": str(e), "sid": _sid},
                        )
                    else:
                        sk = f"{stage_num}:{category}:{agent_name}"
                        await self._db.execute(
                            """
                            UPDATE stages SET status = 'rate_limited', completed_at = NOW(),
                                   error_message = %(error)s,
                                   session_id = COALESCE(%(sid)s, session_id)
                            WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                                  AND run = (
                                      SELECT MAX(run) FROM stages
                                      WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                                  )
                            """,
                            {"task_id": task_id, "stage_key": sk, "error": str(e), "sid": _sid},
                        )
                return True

            except (StageError, NoAvailableAgentError) as e:
                if required:
                    if prev_completed_stage_id is not None:
                        await self._sm.update_checkpoint(task_id, prev_completed_stage_id)
                    await self._tq.postpone_task(
                        task_id, str(e),
                        cooldown_minutes=1,
                        max_retries=3,
                    )
                    log.warning(
                        "required_stage_failed_will_retry",
                        task_id=task_id,
                        stage=stage_num,
                        error=str(e),
                    )
                    return True
                else:
                    log.warning(
                        "optional_stage_failed",
                        task_id=task_id,
                        stage=stage_num,
                        error=str(e),
                    )
                    for agent_name in agents:
                        sk = f"{stage_num}:{category}:{agent_name}"
                        agent_stage_id = current_agent_stage_ids.get(agent_name)
                        skip_eo = self._next_execution_order(task_id)
                        await self._sm.record_stage_skipped(
                            task_id, stage_num, category,
                            stage_id=agent_stage_id, stage_key=sk,
                            execution_order=skip_eo,
                        )
                    current_idx += 1

        return False

    # -----------------------------------------------------------------------
    # Condition evaluation (lifted from nested closure)
    # -----------------------------------------------------------------------

    async def _evaluate_stage_conditions(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        clone_dir: str,
        conditions: list[dict[str, Any]],
        stage_outputs: dict[str, dict[str, Any]],
        stage_output: dict[str, Any],
        repeat_counts: dict[str, int],
        stage_ids: dict[str, int],
        *,
        cond_eval_iteration: int = 1,
    ) -> ConditionResult:
        """Evaluate exit-gate conditions for a stage, including AI conditions."""
        _cond_agent = "condition-evaluator-agent"

        async def _ai_eval(prompt: str, ctx: dict[str, Any]) -> tuple[bool, str]:
            cond_stage_key = (
                f"{stage_num}:condition-eval:condition-evaluator"
            )
            cond_stage_id = await self._sm.create_system_stage(
                task_id, stage_num,
                "condition-eval", "condition-evaluator",
                stage_key=cond_stage_key,
                iteration=cond_eval_iteration,
            )
            cond_eo = self._next_execution_order(task_id)
            await self._sm.record_stage_executing(
                task_id, stage_num,
                "condition-eval", "condition-evaluator",
                stage_id=cond_stage_id,
                stage_key=cond_stage_key,
                iteration=cond_eval_iteration,
                execution_order=cond_eo,
            )

            async def _cond_live_cb(tail: str) -> None:
                try:
                    await self._sm.update_stage_live_output(
                        task_id, cond_stage_key,
                        cond_eval_iteration, 1, tail,
                        stage_id=cond_stage_id,
                    )
                except Exception:
                    pass

            try:
                output = await evaluate_ai_condition(
                    prompt, ctx,
                    work_dir=clone_dir,
                    task_id=task_id,
                    stage_num=stage_num,
                    timeout_seconds=self._registry.get_agent_timeout(_cond_agent) * 60,
                    max_turns=self._registry.get_agent_max_turns(_cond_agent),
                    extra_env=self._registry.get_agent_environment(_cond_agent),
                    prompt_file=self._registry.get_agent_prompt_file(_cond_agent),
                    on_live_output=_cond_live_cb,
                    model=self._registry.get_agent_model(_cond_agent),
                )
                answer = bool(output.get("answer"))
                message = str(output.get("message", ""))
                output["prompt"] = prompt
                await self._sm.store_stage_output(
                    task_id, stage_num,
                    "condition-eval", "condition-evaluator",
                    output,
                    stage_id=cond_stage_id,
                    stage_key=cond_stage_key,
                    iteration=cond_eval_iteration,
                )
                return (answer, message)
            except Exception as exc:
                await self._sm.record_stage_failed(
                    task_id, stage_num, str(exc),
                    stage_id=cond_stage_id,
                    stage_key=cond_stage_key,
                    iteration=cond_eval_iteration,
                )
                raise

        return await evaluate_conditions(
            conditions,
            stage_outputs,
            stage_output,
            repeat_counts,
            ai_evaluator=_ai_eval,
        )

    # -----------------------------------------------------------------------
    # Single planned stage execution
    # -----------------------------------------------------------------------

    async def execute_planned_stage(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agent_name: str,
        context: dict[str, Any],
        *,
        iteration: int = 1,
        stage_id: int | None = None,
        work_dir: str | None = None,
        pipeline_name: str = "",
        execution_order: int | None = None,
    ) -> tuple[dict[str, Any], int | None]:
        """Execute a single planned stage with a specific agent.

        Returns ``(output_dict, stage_id)``.
        """
        stage_key = f"{stage_num}:{category}:{agent_name}"

        run = 1
        resume_session_id: str | None = None
        latest = await self._sm.get_latest_stage_run(task_id, stage_key, iteration)
        if latest and latest["status"] in ("failed", "rate_limited"):
            run = latest["run"] + 1
            resume_session_id = latest.get("session_id")
            stage_id = await self._sm.create_rerun_stage(
                task_id, stage_num, category, agent_name,
                stage_key, iteration, run,
            )
            log.info(
                "stage_retry_run",
                task_id=task_id,
                stage_key=stage_key,
                iteration=iteration,
                run=run,
                previous_status=latest["status"],
                resume_session=bool(resume_session_id),
            )
        elif latest and latest["status"] == "pending":
            run = latest["run"]
            stage_id = latest.get("id") or stage_id

        await self._sm.record_stage_executing(
            task_id, stage_num, category, agent_name,
            stage_id=stage_id,
            stage_key=stage_key, iteration=iteration, run=run,
            input_context=context,
            execution_order=execution_order,
        )
        await self._registry.increment_agent_instances(agent_name)

        async def _live_output_cb(tail: str) -> None:
            try:
                await self._sm.update_stage_live_output(
                    task_id, stage_key, iteration, run, tail,
                    stage_id=stage_id,
                )
            except Exception:
                pass

        try:
            output = await self._invoker.execute_agent(
                agent_name, task_id, context, stage_num,
                work_dir=work_dir,
                on_live_output=_live_output_cb,
                pipeline_name=pipeline_name,
                category=category,
                resume_session_id=resume_session_id,
            )
            await self._sm.store_stage_output(
                task_id, stage_num, category, agent_name, output,
                stage_id=stage_id,
                stage_key=stage_key, iteration=iteration, run=run,
            )
            return output, stage_id
        except RetryableError:
            raise
        except StageError as e:
            sid = getattr(e, "session_id", None)
            await self._sm.record_stage_failed(
                task_id, stage_num, str(e),
                stage_id=stage_id,
                stage_key=stage_key, iteration=iteration, run=run,
                session_id=sid,
            )
            raise
        except asyncio.CancelledError:
            # Task was cancelled (e.g. by _check_timed_out_tasks). Mark the
            # stage as failed so it doesn't stay stuck in 'executing' state.
            await self._sm.record_stage_failed(
                task_id, stage_num, "Stage cancelled (task timed out)",
                stage_id=stage_id,
                stage_key=stage_key, iteration=iteration, run=run,
            )
            raise
        except Exception as e:
            await self._sm.record_stage_failed(
                task_id, stage_num, str(e),
                stage_id=stage_id,
                stage_key=stage_key, iteration=iteration, run=run,
            )
            raise StageError(
                f"Stage {stage_num} ({category}/{agent_name}) failed: {e}"
            ) from e
        finally:
            await self._registry.decrement_agent_instances(agent_name)

    # -----------------------------------------------------------------------
    # Parallel agent execution
    # -----------------------------------------------------------------------

    async def execute_parallel_agents(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agents: list[str],
        clone_dir: str,
        branch_name: str,
        *,
        pipeline_name: str = "",
        stage_ids: dict[str, int] | None = None,
        execution_orders: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Run multiple agents in parallel using git worktrees."""
        log.info(
            "parallel_execution_start",
            task_id=task_id,
            stage=stage_num,
            agents=agents,
        )

        worktree_dirs: list[str] = []
        sub_branches: list[str] = []
        safe_task_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)

        try:
            for agent_name in agents:
                safe_agent = re.sub(r"[^a-zA-Z0-9._-]", "-", agent_name)
                wt_base = Path("/var/lib/aquarco/worktrees")
                wt_base.mkdir(parents=True, exist_ok=True)
                wt_dir = str(
                    wt_base / f"{safe_task_id}-{safe_agent}-s{stage_num}"
                )
                sub_branch = f"{branch_name}--{safe_agent}-s{stage_num}"
                if Path(wt_dir).exists():
                    try:
                        await self._exec._run_git(clone_dir, "worktree", "remove", wt_dir, "--force")
                    except Exception:
                        shutil.rmtree(wt_dir, ignore_errors=True)
                try:
                    await self._exec._run_git(clone_dir, "branch", "-D", sub_branch)
                except Exception:
                    pass
                await self._exec._run_git(
                    clone_dir, "worktree", "add",
                    "-b", sub_branch, wt_dir, branch_name,
                )
                worktree_dirs.append(wt_dir)
                sub_branches.append(sub_branch)

            _stage_ids = stage_ids or {}
            _execution_orders = execution_orders or {}

            async def _run_in_worktree(
                agent_name: str, wt_dir: str
            ) -> tuple[dict[str, Any], int | None]:
                sk = f"{stage_num}:{category}:{agent_name}"
                task_context = await self._sm.get_task_context(task_id) or {}
                accumulated = build_accumulated_context(
                    task_context, stage_num,
                )
                return await self.execute_planned_stage(
                    task_id, stage_num, category, agent_name,
                    accumulated, iteration=1,
                    stage_id=_stage_ids.get(sk),
                    work_dir=wt_dir,
                    pipeline_name=pipeline_name,
                    execution_order=_execution_orders.get(agent_name),
                )

            results = await asyncio.gather(
                *[
                    _run_in_worktree(agent, wt_dir)
                    for agent, wt_dir in zip(agents, worktree_dirs)
                ],
                return_exceptions=True,
            )

            merged_output: dict[str, Any] = {}
            for i, (agent_name, result) in enumerate(zip(agents, results)):
                if isinstance(result, Exception):
                    log.error(
                        "parallel_agent_failed",
                        agent=agent_name,
                        error=str(result),
                    )
                    continue
                output, _sid = result
                merged_output[agent_name] = output
                wt_dir = worktree_dirs[i]
                try:
                    await self._exec._run_git(wt_dir, "add", "-A")
                    status = await self._exec._run_git(wt_dir, "status", "--porcelain")
                    if status.strip():
                        await self._exec._run_git(
                            wt_dir, "commit", "-m",
                            f"chore(aquarco): {category} by {agent_name} "
                            f"for {task_id}",
                        )
                except Exception:
                    log.warning(
                        "worktree_commit_failed",
                        agent=agent_name,
                        worktree=wt_dir,
                    )

            # Merge all worktree branches back to main branch
            await self._exec._git_checkout(clone_dir, branch_name)
            for wt_dir in worktree_dirs:
                try:
                    wt_branch = await self._exec._run_git(
                        wt_dir, "rev-parse", "--abbrev-ref", "HEAD",
                    )
                    wt_branch = wt_branch.strip()
                    if wt_branch and wt_branch != branch_name:
                        await self._exec._run_git(
                            clone_dir, "merge", wt_branch,
                            "--no-edit", check=False,
                        )
                except Exception:
                    log.warning("worktree_merge_failed", worktree=wt_dir)

            errors = [
                r for r in results if isinstance(r, Exception)
            ]
            if errors and len(errors) == len(agents):
                raise StageError(
                    f"All parallel agents failed for stage {stage_num} "
                    f"({category}): {errors[0]}"
                )

            return merged_output

        finally:
            for wt_dir, sub_branch in zip(worktree_dirs, sub_branches):
                try:
                    await self._exec._run_git(clone_dir, "worktree", "remove", wt_dir, "--force")
                except Exception:
                    shutil.rmtree(wt_dir, ignore_errors=True)
                try:
                    await self._exec._run_git(clone_dir, "branch", "-D", sub_branch)
                except Exception:
                    pass

    # -----------------------------------------------------------------------
    # Legacy: single-stage execution (no pipeline)
    # -----------------------------------------------------------------------

    async def execute_stage(
        self,
        category: str,
        task_id: str,
        context: dict[str, Any],
        stage_num: int,
    ) -> dict[str, Any]:
        """Execute a single pipeline stage (legacy path, dynamic agent selection)."""
        agent_name = await self._registry.select_agent(category)
        await self._sm.record_stage_executing(task_id, stage_num, category, agent_name)
        await self._registry.increment_agent_instances(agent_name)

        try:
            output = await self._invoker.execute_agent(
                agent_name, task_id, context, stage_num
            )
            return output
        except StageError:
            raise
        except asyncio.CancelledError:
            # Task was cancelled (e.g. by _check_timed_out_tasks). Mark the
            # stage as failed so it doesn't stay stuck in 'executing' state.
            await self._sm.record_stage_failed(task_id, stage_num, "Stage cancelled (task timed out)")
            raise
        except Exception as e:
            await self._sm.record_stage_failed(task_id, stage_num, str(e))
            raise StageError(f"Stage {stage_num} ({category}) failed: {e}") from e
        finally:
            await self._registry.decrement_agent_instances(agent_name)

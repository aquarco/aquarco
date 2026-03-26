"""Pipeline stage execution engine with planning phase and iteration loops."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..cli.claude import execute_claude
from ..config import get_pipeline_config
from ..config_overlay import (
    ResolvedConfig,
    ScopedAgentView,
    load_overlay,
    resolve_config,
)
from ..database import Database
from ..exceptions import NoAvailableAgentError, PipelineError, RateLimitError, StageError
from ..logging import get_logger
from ..models import Complexity, LoopConfig, PipelineConfig, TaskPhase
from ..task_queue import TaskQueue
from ..utils import run_cmd as _run_cmd
from ..utils import run_git as _run_git
from ..utils import url_to_slug
from .agent_registry import AgentRegistry
from .context import build_accumulated_context
from .visualize import format_pipeline_stages

log = get_logger("pipeline")

# Branch names from external sources (GitHub webhooks, DB) must match this pattern
# before being passed to git subprocesses to prevent flag injection.
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]*$")

# Maximum number of iteration re-runs per stage to prevent infinite loops
_MAX_ITERATIONS = 5

# Absolute ceiling for loop repeats (overrides per-stage max_repeats if higher)
_MAX_LOOP_REPEATS = 10


class PipelineExecutor:
    """Executes multi-stage pipelines by invoking Claude CLI agents."""

    def __init__(
        self,
        db: Database,
        task_queue: TaskQueue,
        registry: AgentRegistry,
        pipelines: list[PipelineConfig],
    ) -> None:
        self._db = db
        self._tq = task_queue
        self._registry = registry
        self._pipelines = pipelines

    # -----------------------------------------------------------------------
    # Config overlay resolution
    # -----------------------------------------------------------------------

    async def _resolve_layered_config(self, task_id: str) -> ScopedAgentView | None:
        """Resolve 3-layer config: default -> global config repo -> per-repo.

        Returns a ScopedAgentView if any overlay exists, None otherwise.
        """
        # Layer 1: defaults from registry
        default_agents = self._registry.get_default_agents()
        default_pipelines = [
            {"name": p.name, "version": p.version, "trigger": p.trigger.model_dump(), "stages": [s.model_dump() for s in p.stages]}
            for p in self._pipelines
        ]
        default_prompts_dir = self._registry.get_default_prompts_dir()

        # Layer 2: global config repo (is_config_repo=true, clone_status='ready')
        global_overlay = None
        global_overlay_base = None
        try:
            config_repo = await self._db.fetch_one(
                """
                SELECT clone_dir FROM repositories
                WHERE is_config_repo = TRUE AND clone_status = 'ready'
                LIMIT 1
                """,
            )
            if config_repo:
                config_dir = Path(config_repo["clone_dir"])
                global_overlay = load_overlay(config_dir)
                if global_overlay:
                    global_overlay_base = config_dir
        except Exception:
            log.warning("global_config_repo_lookup_failed", task_id=task_id)

        # Layer 3: per-repo overlay from task's repo
        repo_overlay = None
        repo_overlay_base = None
        try:
            clone_dir = await self._resolve_clone_dir(task_id)
            repo_dir = Path(clone_dir)
            repo_overlay = load_overlay(repo_dir)
            if repo_overlay:
                repo_overlay_base = repo_dir
        except PipelineError:
            pass

        if not global_overlay and not repo_overlay:
            return None

        resolved = resolve_config(
            default_agents, default_pipelines, default_prompts_dir,
            global_overlay, global_overlay_base,
            repo_overlay, repo_overlay_base,
        )
        return ScopedAgentView(resolved)

    # -----------------------------------------------------------------------
    # Main entry point
    # -----------------------------------------------------------------------

    async def execute_pipeline(
        self,
        pipeline_name: str,
        task_id: str,
        context: dict[str, Any],
    ) -> None:
        """Execute a full pipeline for a task using three phases:
        1. Trigger (already done by poller)
        2. Planning — AI agent assigns agents to categories
        3. Running — execute planned stages with iteration loops
        """
        # Check for resume checkpoint
        checkpoint = await self._tq.get_checkpoint(task_id)
        start_stage = 0
        if checkpoint:
            start_stage = checkpoint["last_completed_stage"] + 1
            log.info("resuming_pipeline", task_id=task_id, from_stage=start_stage)

        # Pipeline is required; task.pipeline has a default of 'feature-pipeline'.
        if not pipeline_name:
            task = await self._tq.get_task(task_id)
            if not task:
                raise PipelineError(f"Task {task_id} not found")
            pipeline_name = task.pipeline
            log.info(
                "pipeline_from_task",
                task_id=task_id,
                pipeline=pipeline_name,
            )

        stages = get_pipeline_config(self._pipelines, pipeline_name)
        if not stages:
            raise PipelineError(f"Pipeline '{pipeline_name}' not found in config")

        categories = [s["category"] for s in stages]

        # --- Phase 2: Planning (or fast-path) ---
        if start_stage == 0:
            if self._registry.should_skip_planning(categories):
                planned_stages = self._build_default_plan(stages)
                log.info("planning_skipped_fast_path", task_id=task_id)
            else:
                await self._tq.update_task_phase(task_id, TaskPhase.PLANNING)
                planned_stages = await self._execute_planning_phase(
                    task_id, pipeline_name, stages, context
                )

            await self._tq.store_planned_stages(task_id, planned_stages)
            await self._tq.create_planned_pending_stages(task_id, planned_stages)
        else:
            # Resuming: load planned_stages from DB
            task = await self._tq.get_task(task_id)
            if not task or not task.planned_stages:
                raise PipelineError(
                    f"Cannot resume task {task_id}: no planned_stages found"
                )
            planned_stages = task.planned_stages

        # --- Resolve layered config ---
        scoped_view = await self._resolve_layered_config(task_id)

        # --- Phase 3: Running with iteration loops ---
        await self._tq.update_task_phase(task_id, TaskPhase.RUNNING)
        clone_dir = await self._resolve_clone_dir(task_id)

        # Create a per-task worktree so parallel tasks on the same repo
        # don't clobber each other's working directory.  We use --detach
        # to avoid conflicts when the branch is already checked out in the
        # main clone or another worktree.
        #
        # Worktrees live under /var/lib/aquarco/worktrees (not /tmp) so they
        # survive service restarts and are not wiped by PrivateTmp or tmpfiles.
        safe_task_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)
        worktree_base = Path("/var/lib/aquarco/worktrees")
        worktree_base.mkdir(parents=True, exist_ok=True)
        work_dir = str(worktree_base / safe_task_id)

        if Path(work_dir).exists() and start_stage > 0:
            # Resuming: reuse worktree with previous commits
            log.info("task_worktree_reused", task_id=task_id, work_dir=work_dir)
        elif Path(work_dir).exists():
            # Fresh start but stale worktree — clean and recreate
            try:
                await _run_git(clone_dir, "worktree", "remove", work_dir, "--force")
            except Exception:
                shutil.rmtree(work_dir, ignore_errors=True)
            await _run_git(clone_dir, "worktree", "add", "--detach", work_dir)
        else:
            await _run_git(clone_dir, "worktree", "add", "--detach", work_dir)

        branch_name = await self._setup_branch(
            task_id, context, work_dir, resuming=start_stage > 0,
        )

        # Persist branch_name on the task for later reference
        await self._db.execute(
            "UPDATE tasks SET branch_name = %(branch)s WHERE id = %(id)s",
            {"id": task_id, "branch": branch_name},
        )

        log.info(
            "task_worktree_created",
            task_id=task_id,
            clone_dir=clone_dir,
            work_dir=work_dir,
            branch=branch_name,
        )

        try:
            failed = await self._execute_running_phase(
                task_id, planned_stages, stages, work_dir, branch_name,
                start_stage=start_stage,
                scoped_view=scoped_view,
            )
        finally:
            if scoped_view:
                scoped_view.cleanup()

        if failed:
            # Keep worktree on failure for debugging / resume.
            log.info(
                "task_worktree_kept",
                task_id=task_id,
                work_dir=work_dir,
                reason="pipeline_failed",
            )
            return

        # Pipeline completed successfully — commit any leftovers, then create PR.
        # Keep worktree alive for potential rerun; close_task_resources() cleans it.
        # Push directly from the worktree — the branch is checked out here, so
        # we cannot checkout it in clone_dir (git forbids the same branch in
        # two worktrees).  The worktree shares the object store and remotes.
        try:
            await _run_git(work_dir, "add", "-A")
            status = await _run_git(work_dir, "status", "--porcelain")
            if status.strip():
                await _run_git(
                    work_dir, "commit", "-m",
                    f"chore(aquarco): uncommitted changes for {task_id}",
                )
        except Exception:
            log.warning("task_worktree_final_commit_failed", task_id=task_id)

        await self._tq.update_task_phase(task_id, TaskPhase.COMPLETED)
        await self._create_pipeline_pr(task_id, branch_name, work_dir, {}, stage_defs=stages)
        await self._tq.complete_task(task_id)
        await self._tq.delete_checkpoint(task_id)
        log.info("pipeline_completed", task_id=task_id, pipeline=pipeline_name)

    # -----------------------------------------------------------------------
    # Phase 2: Planning
    # -----------------------------------------------------------------------

    def _build_default_plan(
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

    async def _execute_planning_phase(
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
        await self._tq.record_stage_executing(
            task_id, -1, "planning", "planner-agent",
            stage_key=planning_stage_key, iteration=1,
        )

        try:
            output = await self._execute_agent(
                "planner-agent", task_id, planner_context, {}, -1
            )
        except Exception as e:
            await self._tq.record_stage_failed(
                task_id, -1, str(e), stage_key=planning_stage_key,
            )
            raise PipelineError(f"Planning phase failed: {e}") from e

        await self._tq.store_stage_output(
            task_id, -1, "planning", "planner-agent", output,
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

    # -----------------------------------------------------------------------
    # Phase 3: Running with iteration loops
    # -----------------------------------------------------------------------

    async def _execute_running_phase(
        self,
        task_id: str,
        planned_stages: list[dict[str, Any]],
        stage_defs: list[dict[str, Any]],
        clone_dir: str,
        branch_name: str,
        *,
        start_stage: int = 0,
        scoped_view: ScopedAgentView | None = None,
    ) -> bool:
        """Execute planned stages with validation-driven iteration loops
        and conditional loop repetitions.

        Returns True if the pipeline failed and should not continue.
        """
        previous_output: dict[str, Any] = {}
        # Track all stage outputs by stage_num for AI condition evaluation
        all_stage_outputs: dict[int, dict[str, Any]] = {}
        # Track which stages are inside an active loop to avoid double-execution
        loop_skip_set: set[int] = set()

        stage_num = start_stage
        while stage_num < len(planned_stages):
            plan = planned_stages[stage_num]

            # Skip stages that were already executed as part of a loop body
            if stage_num in loop_skip_set:
                stage_num += 1
                continue

            category = plan["category"]
            agents = plan.get("agents", [])
            parallel = plan.get("parallel", False)

            # Find matching stage def for conditions/required/loop
            stage_def = (
                stage_defs[stage_num] if stage_num < len(stage_defs) else {}
            )
            required = stage_def.get("required", True)
            conditions = stage_def.get("conditions", [])
            loop_cfg = self._resolve_loop_config(stage_def)

            # Check entry conditions
            if conditions and not check_conditions(conditions, previous_output):
                log.info("stage_skipped_conditions", task_id=task_id, stage=stage_num)
                for agent_name in agents:
                    sk = f"{stage_num}:{category}:{agent_name}"
                    await self._tq.record_stage_skipped(
                        task_id, stage_num, category, stage_key=sk,
                    )
                stage_num += 1
                continue

            # ── Conditional loop handling ──
            if loop_cfg is not None:
                loop_failed = await self._execute_loop(
                    task_id, stage_num, planned_stages, stage_defs,
                    clone_dir, branch_name, loop_cfg,
                    previous_output, all_stage_outputs,
                    scoped_view=scoped_view,
                )
                if loop_failed:
                    return True

                # Mark loop body stages as handled so the outer loop skips them
                body_indices = resolve_loop_stages(loop_cfg, stage_num, stage_defs)
                for idx in body_indices:
                    if idx != stage_num:
                        loop_skip_set.add(idx)

                # Update previous_output from the last loop iteration
                for idx in body_indices:
                    if idx in all_stage_outputs:
                        previous_output = all_stage_outputs[idx]

                stage_num += 1
                continue

            # ── Normal (non-loop) stage execution ──
            await _git_checkout(clone_dir, branch_name)

            try:
                if parallel and len(agents) > 1:
                    stage_output = await self._execute_parallel_agents(
                        task_id, stage_num, category, agents,
                        clone_dir, branch_name,
                        scoped_view=scoped_view,
                    )
                else:
                    # Sequential execution (single or multiple agents)
                    stage_output = {}
                    for agent_name in agents:
                        task_context = await self._tq.get_task_context(task_id) or {}
                        accumulated = build_accumulated_context(
                            task_context, stage_num,
                        )
                        out = await self._execute_planned_stage(
                            task_id, stage_num, category, agent_name,
                            accumulated, iteration=1,
                            scoped_view=scoped_view,
                            work_dir=clone_dir,
                        )
                        stage_output.update(out)

                previous_output = stage_output
                all_stage_outputs[stage_num] = stage_output

                # Process validation items from agent output
                for agent_name in agents:
                    sk = f"{stage_num}:{category}:{agent_name}"
                    await self._process_validation_items(task_id, sk, stage_output)

                # Iteration loop: re-run if open validation items target this category
                current_iteration = 1
                while await self._should_iterate(task_id, category, current_iteration):
                    current_iteration += 1
                    log.info(
                        "iteration_rerun",
                        task_id=task_id,
                        stage=stage_num,
                        category=category,
                        iteration=current_iteration,
                    )

                    open_items = await self._tq.get_open_validation_items(
                        task_id, category,
                    )
                    vi_in = [
                        {"id": vi.id, "description": vi.description}
                        for vi in open_items
                    ]

                    for agent_name in agents:
                        sk = f"{stage_num}:{category}:{agent_name}"
                        await self._tq.create_iteration_stage(
                            task_id, stage_num, category, agent_name,
                            current_iteration,
                        )
                        task_context = await self._tq.get_task_context(task_id) or {}
                        accumulated = build_accumulated_context(
                            task_context, stage_num,
                            validation_items=vi_in,
                        )
                        out = await self._execute_planned_stage(
                            task_id, stage_num, category, agent_name,
                            accumulated,
                            iteration=current_iteration,
                            validation_items_in=vi_in,
                            scoped_view=scoped_view,
                            work_dir=clone_dir,
                        )
                        stage_output.update(out)
                        await self._process_validation_items(task_id, sk, out)

                    previous_output = stage_output
                    all_stage_outputs[stage_num] = stage_output

                # Checkpoint and auto-commit
                await self._tq.checkpoint_pipeline(task_id, stage_num)
                await _auto_commit(clone_dir, task_id, stage_num, category)

            except RateLimitError as e:
                last_completed = stage_num - 1 if stage_num > 0 else 0
                await self._tq.checkpoint_pipeline(task_id, last_completed)
                await self._tq.rate_limit_task(task_id, str(e))
                for agent_name in agents:
                    sk = f"{stage_num}:{category}:{agent_name}"
                    await self._db.execute(
                        """
                        UPDATE stages SET status = 'rate_limited', completed_at = NOW(),
                               error_message = %(error)s
                        WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                              AND run = (
                                  SELECT MAX(run) FROM stages
                                  WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                              )
                        """,
                        {"task_id": task_id, "stage_key": sk, "error": str(e)},
                    )
                return True

            except (StageError, NoAvailableAgentError) as e:
                if required:
                    await self._tq.update_task_phase(task_id, TaskPhase.FAILED)
                    await self._tq.fail_task(task_id, str(e))
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
                        await self._tq.record_stage_skipped(
                            task_id, stage_num, category, stage_key=sk,
                        )

            stage_num += 1

        return False

    # -----------------------------------------------------------------------
    # Conditional loop execution
    # -----------------------------------------------------------------------

    def _resolve_loop_config(
        self, stage_def: dict[str, Any],
    ) -> LoopConfig | None:
        """Extract LoopConfig from a stage definition dict."""
        loop_data = stage_def.get("loop")
        if loop_data is None:
            return None
        if isinstance(loop_data, LoopConfig):
            return loop_data
        if isinstance(loop_data, dict):
            return LoopConfig(**loop_data)
        return None

    async def _execute_loop(
        self,
        task_id: str,
        trigger_stage_num: int,
        planned_stages: list[dict[str, Any]],
        stage_defs: list[dict[str, Any]],
        clone_dir: str,
        branch_name: str,
        loop_cfg: LoopConfig,
        previous_output: dict[str, Any],
        all_stage_outputs: dict[int, dict[str, Any]],
        *,
        scoped_view: ScopedAgentView | None = None,
    ) -> bool:
        """Execute a conditional loop.

        Repeats a set of stages until the exit condition is met or
        max_repeats is exhausted.

        Returns True if the pipeline failed and should not continue.
        """
        body_indices = resolve_loop_stages(loop_cfg, trigger_stage_num, stage_defs)
        max_repeats = min(loop_cfg.max_repeats, _MAX_LOOP_REPEATS)

        log.info(
            "loop_start",
            task_id=task_id,
            trigger_stage=trigger_stage_num,
            body_stages=body_indices,
            max_repeats=max_repeats,
            eval_mode=loop_cfg.eval_mode,
            condition=loop_cfg.condition,
        )

        for loop_iter in range(1, max_repeats + 1):
            log.info(
                "loop_iteration",
                task_id=task_id,
                trigger_stage=trigger_stage_num,
                iteration=loop_iter,
                max_repeats=max_repeats,
            )

            # Execute each stage in the loop body
            for body_stage_num in body_indices:
                plan = planned_stages[body_stage_num]
                category = plan["category"]
                agents = plan.get("agents", [])
                stage_def = (
                    stage_defs[body_stage_num]
                    if body_stage_num < len(stage_defs)
                    else {}
                )
                required = stage_def.get("required", True)

                await _git_checkout(clone_dir, branch_name)

                try:
                    stage_output = {}
                    for agent_name in agents:
                        task_context = (
                            await self._tq.get_task_context(task_id) or {}
                        )
                        accumulated = build_accumulated_context(
                            task_context, body_stage_num,
                        )
                        # Add loop metadata to context
                        accumulated["loop_iteration"] = loop_iter
                        accumulated["loop_max_repeats"] = max_repeats
                        accumulated["loop_condition"] = loop_cfg.condition

                        out = await self._execute_planned_stage(
                            task_id,
                            body_stage_num,
                            category,
                            agent_name,
                            accumulated,
                            iteration=loop_iter,
                            scoped_view=scoped_view,
                            work_dir=clone_dir,
                        )
                        stage_output.update(out)

                    previous_output = stage_output
                    all_stage_outputs[body_stage_num] = stage_output

                    # Process validation items
                    for agent_name in agents:
                        sk = f"{body_stage_num}:{category}:{agent_name}"
                        await self._process_validation_items(
                            task_id, sk, stage_output,
                        )

                    # Checkpoint and auto-commit per stage in loop body
                    await self._tq.checkpoint_pipeline(task_id, body_stage_num)
                    await _auto_commit(
                        clone_dir, task_id, body_stage_num, category,
                    )

                except RateLimitError as e:
                    last_completed = body_stage_num - 1 if body_stage_num > 0 else 0
                    await self._tq.checkpoint_pipeline(task_id, last_completed)
                    await self._tq.rate_limit_task(task_id, str(e))
                    return True

                except (StageError, NoAvailableAgentError) as e:
                    if required:
                        await self._tq.update_task_phase(
                            task_id, TaskPhase.FAILED,
                        )
                        await self._tq.fail_task(task_id, str(e))
                        return True
                    else:
                        log.warning(
                            "loop_optional_stage_failed",
                            task_id=task_id,
                            stage=body_stage_num,
                            iteration=loop_iter,
                            error=str(e),
                        )

            # After executing all body stages, evaluate the exit condition
            condition_met = await evaluate_loop_condition(
                loop_cfg, previous_output, all_stage_outputs, clone_dir,
            )

            log.info(
                "loop_condition_evaluated",
                task_id=task_id,
                trigger_stage=trigger_stage_num,
                iteration=loop_iter,
                condition_met=condition_met,
            )

            if condition_met:
                log.info(
                    "loop_exit_condition_met",
                    task_id=task_id,
                    trigger_stage=trigger_stage_num,
                    iterations_used=loop_iter,
                )
                break
        else:
            log.warning(
                "loop_max_repeats_reached",
                task_id=task_id,
                trigger_stage=trigger_stage_num,
                max_repeats=max_repeats,
            )

        return False

    async def _execute_planned_stage(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agent_name: str,
        context: dict[str, Any],
        *,
        iteration: int = 1,
        validation_items_in: list[dict[str, Any]] | None = None,
        scoped_view: ScopedAgentView | None = None,
        work_dir: str | None = None,
    ) -> dict[str, Any]:
        """Execute a single planned stage with a specific agent."""
        stage_key = f"{stage_num}:{category}:{agent_name}"

        # Determine run number: if a previous run failed/rate_limited, create a retry
        run = 1
        latest = await self._tq.get_latest_stage_run(task_id, stage_key, iteration)
        if latest and latest["status"] in ("failed", "rate_limited"):
            run = latest["run"] + 1
            await self._tq.create_rerun_stage(
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
            )
        elif latest and latest["status"] == "pending":
            run = latest["run"]

        await self._tq.record_stage_executing(
            task_id, stage_num, category, agent_name,
            stage_key=stage_key, iteration=iteration, run=run,
            input_context=context,
        )
        await self._registry.increment_agent_instances(agent_name)

        # Build live-output callback so CLI pushes debug log tail to DB
        async def _live_output_cb(tail: str) -> None:
            try:
                await self._tq.update_stage_live_output(
                    task_id, stage_key, iteration, run, tail,
                )
            except Exception:
                pass  # best-effort, don't break execution

        try:
            output = await self._execute_agent(
                agent_name, task_id, context, stage_num,
                work_dir=work_dir,
                scoped_view=scoped_view,
                on_live_output=_live_output_cb,
            )
            await self._tq.store_stage_output(
                task_id, stage_num, category, agent_name, output,
                stage_key=stage_key, iteration=iteration, run=run,
                validation_items_in=validation_items_in,
                validation_items_out=output.get("validation_items_new"),
            )
            return output
        except (StageError, RateLimitError):
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._tq.record_stage_failed(
                task_id, stage_num, str(e),
                stage_key=stage_key, iteration=iteration, run=run,
            )
            raise StageError(
                f"Stage {stage_num} ({category}/{agent_name}) failed: {e}"
            ) from e
        finally:
            await self._registry.decrement_agent_instances(agent_name)

    async def _execute_parallel_agents(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agents: list[str],
        clone_dir: str,
        branch_name: str,
        *,
        scoped_view: ScopedAgentView | None = None,
    ) -> dict[str, Any]:
        """Run multiple agents in parallel using git worktrees."""
        log.info(
            "parallel_execution_start",
            task_id=task_id,
            stage=stage_num,
            agents=agents,
        )

        worktree_dirs: list[str] = []
        safe_task_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)

        try:
            # Create a worktree per agent
            for agent_name in agents:
                safe_agent = re.sub(r"[^a-zA-Z0-9._-]", "-", agent_name)
                wt_base = Path("/var/lib/aquarco/worktrees")
                wt_base.mkdir(parents=True, exist_ok=True)
                wt_dir = str(
                    wt_base / f"{safe_task_id}-{safe_agent}-s{stage_num}"
                )
                if Path(wt_dir).exists():
                    try:
                        await _run_git(clone_dir, "worktree", "remove", wt_dir, "--force")
                    except Exception:
                        shutil.rmtree(wt_dir, ignore_errors=True)
                await _run_git(
                    clone_dir, "worktree", "add", wt_dir, branch_name,
                )
                worktree_dirs.append(wt_dir)

            # Run all agents in parallel
            async def _run_in_worktree(
                agent_name: str, wt_dir: str
            ) -> dict[str, Any]:
                task_context = await self._tq.get_task_context(task_id) or {}
                accumulated = build_accumulated_context(
                    task_context, stage_num,
                )
                return await self._execute_planned_stage(
                    task_id, stage_num, category, agent_name,
                    accumulated, iteration=1,
                    scoped_view=scoped_view,
                    work_dir=wt_dir,
                )

            results = await asyncio.gather(
                *[
                    _run_in_worktree(agent, wt_dir)
                    for agent, wt_dir in zip(agents, worktree_dirs)
                ],
                return_exceptions=True,
            )

            # Merge worktrees back
            merged_output: dict[str, Any] = {}
            for i, (agent_name, result) in enumerate(zip(agents, results)):
                if isinstance(result, Exception):
                    log.error(
                        "parallel_agent_failed",
                        agent=agent_name,
                        error=str(result),
                    )
                    continue
                merged_output[agent_name] = result
                # Merge changes from worktree into main branch
                wt_dir = worktree_dirs[i]
                try:
                    await _run_git(wt_dir, "add", "-A")
                    status = await _run_git(wt_dir, "status", "--porcelain")
                    if status.strip():
                        await _run_git(
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
            await _git_checkout(clone_dir, branch_name)
            for wt_dir in worktree_dirs:
                try:
                    wt_branch = await _run_git(
                        wt_dir, "rev-parse", "--abbrev-ref", "HEAD",
                    )
                    wt_branch = wt_branch.strip()
                    if wt_branch and wt_branch != branch_name:
                        await _run_git(
                            clone_dir, "merge", wt_branch,
                            "--no-edit", check=False,
                        )
                except Exception:
                    log.warning("worktree_merge_failed", worktree=wt_dir)

            # Check if any agent actually failed
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
            # Clean up worktrees
            for wt_dir in worktree_dirs:
                try:
                    await _run_git(clone_dir, "worktree", "remove", wt_dir, "--force")
                except Exception:
                    # Fallback: manual cleanup
                    shutil.rmtree(wt_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # Validation items
    # -----------------------------------------------------------------------

    async def _process_validation_items(
        self,
        task_id: str,
        stage_key: str,
        agent_output: dict[str, Any],
    ) -> None:
        """Extract and store validation items from agent output."""
        # Resolve items the agent claims to have fixed
        resolved_ids = agent_output.get("validation_items_resolved", [])
        for item_id in resolved_ids:
            if isinstance(item_id, int):
                await self._tq.resolve_validation_item(item_id, stage_key)

        # Add new validation items
        new_items = agent_output.get("validation_items_new", [])
        for item in new_items:
            if isinstance(item, dict) and "category" in item and "description" in item:
                await self._tq.add_validation_item(
                    task_id, stage_key, item["category"], item["description"],
                )

    async def _should_iterate(
        self,
        task_id: str,
        category: str,
        current_iteration: int,
    ) -> bool:
        """Check if a stage should re-run based on open validation items."""
        if current_iteration >= _MAX_ITERATIONS:
            log.warning(
                "max_iterations_reached",
                task_id=task_id,
                category=category,
                max=_MAX_ITERATIONS,
            )
            return False
        open_items = await self._tq.get_open_validation_items(task_id, category)
        return len(open_items) > 0

    # -----------------------------------------------------------------------
    # Legacy: single-stage execution (no pipeline)
    # -----------------------------------------------------------------------

    async def _execute_stage(
        self,
        category: str,
        task_id: str,
        context: dict[str, Any],
        stage_num: int,
    ) -> dict[str, Any]:
        """Execute a single pipeline stage (legacy path, dynamic agent selection)."""
        agent_name = await self._registry.select_agent(category)
        await self._tq.record_stage_executing(task_id, stage_num, category, agent_name)
        await self._registry.increment_agent_instances(agent_name)

        try:
            output = await self._execute_agent(
                agent_name, task_id, context, stage_num
            )
            return output
        except StageError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._tq.record_stage_failed(task_id, stage_num, str(e))
            raise StageError(f"Stage {stage_num} ({category}) failed: {e}") from e
        finally:
            await self._registry.decrement_agent_instances(agent_name)

    async def _execute_agent(
        self,
        agent_name: str,
        task_id: str,
        context: dict[str, Any],
        stage_num: int,
        *,
        work_dir: str | None = None,
        scoped_view: ScopedAgentView | None = None,
        on_live_output: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Invoke the Claude CLI for an agent, with automatic continuation.

        If the agent hits max_turns, automatically resumes the session until
        the work is complete or the cumulative cost exceeds maxCost.
        """
        # Use scoped_view for config lookups when available, fall back to registry
        cfg = scoped_view or self._registry
        prompt_file = cfg.get_agent_prompt_file(agent_name)
        timeout_minutes = cfg.get_agent_timeout(agent_name)
        max_turns = cfg.get_agent_max_turns(agent_name)
        max_cost = cfg.get_agent_max_cost(agent_name)
        clone_dir = work_dir or await self._resolve_clone_dir(task_id)

        agent_context = {
            "task_id": task_id,
            "agent": agent_name,
            "stage_number": stage_num,
            "accumulated_context": context,
        }

        cumulative_cost = 0.0
        resume_session_id: str | None = None
        iteration = 0
        max_resume_iterations = 10
        last_successful_output: dict[str, Any] | None = None

        while True:
            claude_output = await execute_claude(
                prompt_file=prompt_file,
                context=agent_context,
                work_dir=clone_dir,
                timeout_seconds=timeout_minutes * 60,
                allowed_tools=cfg.get_allowed_tools(agent_name),
                denied_tools=cfg.get_denied_tools(agent_name),
                task_id=task_id,
                stage_num=stage_num,
                extra_env=cfg.get_agent_environment(agent_name),
                output_schema=cfg.get_agent_output_schema(agent_name),
                max_turns=max_turns,
                resume_session_id=resume_session_id,
                on_live_output=on_live_output,
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
            output["_cumulative_cost_usd"] = cumulative_cost
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

                if iteration >= max_resume_iterations:
                    log.warning(
                        "max_resume_iterations_reached",
                        task_id=task_id,
                        stage=stage_num,
                        agent=agent_name,
                        iterations=iteration,
                        max_resume_iterations=max_resume_iterations,
                    )
                    break

                log.info(
                    "max_turns_continuing",
                    task_id=task_id,
                    stage=stage_num,
                    agent=agent_name,
                    session_id=session_id,
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
            output = last_successful_output

        output["_agent_name"] = agent_name
        output["_iterations"] = iteration

        # Save output log (sanitize task_id to prevent path traversal)
        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)
        output_log = Path(f"/var/log/aquarco/agent-output-{safe_id}-stage{stage_num}.json")
        output_log.parent.mkdir(parents=True, exist_ok=True)
        output_log.write_text(json.dumps(output, indent=2))

        return output

    # -----------------------------------------------------------------------
    # Repository / branch helpers
    # -----------------------------------------------------------------------

    async def _resolve_clone_dir(self, task_id: str) -> str:
        """Get the clone directory for a task's repository."""
        row = await self._db.fetch_one(
            """
            SELECT r.clone_dir FROM tasks t
            JOIN repositories r ON r.name = t.repository
            WHERE t.id = %(id)s
            """,
            {"id": task_id},
        )
        if not row:
            raise PipelineError(f"No clone_dir found for task {task_id}")
        clone_dir: str = row["clone_dir"]
        return clone_dir

    async def _setup_branch(
        self,
        task_id: str,
        context: dict[str, Any],
        work_dir: str,
        *,
        resuming: bool = False,
    ) -> str:
        """Set up the git branch for pipeline execution.

        ``work_dir`` may be the main clone **or** a detached worktree.
        When *resuming* a checkpointed pipeline, the branch already has
        commits from previous stages — we must **not** reset it to
        origin/base.
        """
        head_branch: str | None = context.get("head_branch")
        if head_branch:
            if not _SAFE_BRANCH_RE.match(head_branch):
                raise PipelineError(
                    f"Rejected unsafe head_branch value: '{head_branch}'"
                )
            await _run_git(work_dir, "checkout", "-B", head_branch, head_branch)
            return head_branch

        # Feature pipeline
        task = await self._tq.get_task(task_id)
        if not task:
            raise PipelineError(f"Task {task_id} not found")

        slug = re.sub(r"[^a-z0-9]+", "-", task.title.lower()).strip("-")[:50]
        branch_name = f"aquarco/{task_id}/{slug}"

        await _run_git(work_dir, "fetch", "origin")

        if resuming:
            # Branch already exists with work from earlier stages — just
            # check it out without resetting to origin/base.
            await _run_git(work_dir, "checkout", "-B", branch_name, branch_name)
        else:
            # Fresh start: create or reset the branch from origin/base.
            base_branch = await self._get_repo_branch(task_id)
            await _run_git(
                work_dir, "checkout", "-B", branch_name, f"origin/{base_branch}",
            )
        return branch_name

    async def _get_repo_branch(self, task_id: str) -> str:
        """Get the default branch for a task's repository."""
        row = await self._db.fetch_one(
            """
            SELECT r.branch FROM tasks t
            JOIN repositories r ON r.name = t.repository
            WHERE t.id = %(id)s
            """,
            {"id": task_id},
        )
        return (row["branch"] if row and row["branch"] else None) or "main"

    async def _create_pipeline_pr(
        self,
        task_id: str,
        branch_name: str,
        clone_dir: str,
        stage_output: dict[str, Any],
        *,
        stage_defs: list[dict[str, Any]] | None = None,
    ) -> None:
        """Create or update a PR after pipeline completion."""
        task = await self._tq.get_task(task_id)
        if not task:
            return

        context = task.initial_context or {}
        head_branch = context.get("head_branch")

        if head_branch:
            # PR review: comment on existing PR
            await _auto_commit(clone_dir, task_id, task.current_stage, "review")
            await _push_if_ahead(clone_dir, head_branch)
            source_ref = task.source_ref
            if source_ref:
                repo_slug = await self._get_repo_slug(task_id)
                if repo_slug:
                    summary = json.dumps(stage_output.get("summary", "Pipeline completed"))
                    await _run_cmd(
                        "gh", "issue", "comment", source_ref,
                        "--repo", repo_slug,
                        "--body", f"Pipeline completed.\n\n{summary}",
                    )
        else:
            # Feature pipeline: create new PR
            base_branch = await self._get_repo_branch(task_id)
            ahead = await _get_ahead_count(clone_dir, branch_name, base_branch)
            if ahead == 0:
                log.info("no_commits_to_push", task_id=task_id)
                return

            await _run_git(
                clone_dir, "fetch", "origin",
                f"+refs/heads/{branch_name}:refs/remotes/origin/{branch_name}",
                check=False,
            )
            await _run_git(clone_dir, "push", "origin", branch_name, "--force-with-lease")
            repo_slug = await self._get_repo_slug(task_id)
            if repo_slug:
                # Build PR body with pipeline stages visualization
                pr_body = f"Automated PR for task {task_id}\n"
                if stage_defs:
                    stages_diagram = format_pipeline_stages(
                        stage_defs, markdown=True,
                    )
                    pr_body += (
                        f"\n## Pipeline Stages\n\n{stages_diagram}\n"
                    )

                pr_output = await _run_cmd(
                    "gh", "pr", "create",
                    "--repo", repo_slug,
                    "--head", branch_name,
                    "--title", f"feat: {task.title}",
                    "--body", pr_body,
                )
                # Parse PR number from URL (e.g. https://github.com/.../pull/42)
                if pr_output:
                    pr_match = re.search(r"/pull/(\d+)", pr_output)
                    if pr_match:
                        pr_number = int(pr_match.group(1))
                        await self._tq.store_pr_info(
                            task_id, pr_number, branch_name,
                        )

    async def close_task_resources(self, task_id: str) -> None:
        """Remove worktrees for a closed task."""
        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)
        worktree_base = Path("/var/lib/aquarco/worktrees")
        work_dir = worktree_base / safe_id

        if work_dir.exists():
            clone_dir = await self._resolve_clone_dir(task_id)
            try:
                await _run_git(
                    clone_dir, "worktree", "remove", str(work_dir), "--force",
                )
            except Exception:
                shutil.rmtree(work_dir, ignore_errors=True)
            log.info("task_worktree_cleaned", task_id=task_id, work_dir=str(work_dir))

        # Clean parallel agent worktrees
        for wt in worktree_base.glob(f"{safe_id}-*"):
            shutil.rmtree(wt, ignore_errors=True)

    async def _get_repo_slug(self, task_id: str) -> str | None:
        """Get the owner/repo slug for a task's repository."""
        row = await self._db.fetch_one(
            """
            SELECT r.url FROM tasks t
            JOIN repositories r ON r.name = t.repository
            WHERE t.id = %(id)s
            """,
            {"id": task_id},
        )
        if not row:
            return None
        return url_to_slug(row["url"])


# -----------------------------------------------------------------------
# Free functions (conditions, git helpers)
# -----------------------------------------------------------------------


def check_conditions(
    conditions: list[str], previous_output: dict[str, Any]
) -> bool:
    """Evaluate stage conditions against previous output.

    Condition format: "field operator value"
    Supports: ==, !=, >=, >, <=, <
    """
    for condition in conditions:
        parts = condition.split()
        if len(parts) < 3:
            continue

        field = parts[0]
        operator = parts[1]
        expected = " ".join(parts[2:])

        # Resolve field value via dot notation
        actual = _resolve_field(previous_output, field)
        if actual is None:
            return False

        actual_str = str(actual)

        if operator in ("==", "="):
            if actual_str != expected:
                return False
        elif operator == "!=":
            if actual_str == expected:
                return False
        elif operator in (">=", ">", "<=", "<"):
            if not _compare_complexity(actual_str, operator, expected):
                return False

    return True


def _resolve_field(data: dict[str, Any], field_path: str) -> Any:
    """Resolve a dotted field path in a dict."""
    current: Any = data
    for key in field_path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _compare_complexity(actual: str, operator: str, expected: str) -> bool:
    """Compare complexity values using ordered scale."""
    try:
        a = Complexity(actual.lower())
        b = Complexity(expected.lower())
    except ValueError:
        return False

    if operator == ">=":
        return a >= b
    elif operator == ">":
        return a > b
    elif operator == "<=":
        return a <= b
    elif operator == "<":
        return a < b
    return False


async def evaluate_loop_condition(
    loop: LoopConfig,
    stage_outputs: dict[str, Any],
    all_stage_outputs: dict[int, dict[str, Any]],
    work_dir: str,
) -> bool:
    """Evaluate a loop exit condition.

    Returns True if the loop should STOP (condition is met).
    Returns False if the loop should CONTINUE (condition is not met).
    """
    if loop.eval_mode == "simple":
        # Use existing check_conditions with the exit condition
        return check_conditions([loop.condition], stage_outputs)

    if loop.eval_mode == "ai":
        return await _evaluate_ai_condition(
            loop.condition, stage_outputs, all_stage_outputs, work_dir,
        )

    # Unknown eval_mode — treat as met (stop looping)
    return True


async def _evaluate_ai_condition(
    condition: str,
    stage_outputs: dict[str, Any],
    all_stage_outputs: dict[int, dict[str, Any]],
    work_dir: str,
) -> bool:
    """Evaluate a natural-language condition using Claude CLI.

    The condition is a predicate like:
      "All risks mentioned in ANALYSIS.risks are avoided or mitigated"

    Claude returns a JSON object with {"result": true/false, "reasoning": "..."}.
    """
    import tempfile as _tmpmod

    # Build a compact summary of stage outputs for the prompt
    outputs_summary = json.dumps(
        {f"STAGE_{k}": v for k, v in all_stage_outputs.items()},
        indent=2,
        default=str,
    )
    # Truncate to avoid exceeding context limits
    if len(outputs_summary) > 30_000:
        outputs_summary = outputs_summary[:30_000] + "\n... (truncated)"

    prompt_text = (
        "You are a condition evaluator for a CI/CD pipeline.\n\n"
        "Given the following stage outputs from the pipeline:\n"
        f"```json\n{outputs_summary}\n```\n\n"
        f"Evaluate whether this condition is TRUE or FALSE:\n"
        f'"{condition}"\n\n'
        "Respond ONLY with a JSON object: "
        '{"result": true, "reasoning": "..."} or {"result": false, "reasoning": "..."}'
    )

    # Write a temporary prompt file for Claude CLI
    prompt_path = Path(_tmpmod.mktemp(suffix=".md", prefix="loop-cond-"))
    prompt_path.write_text(prompt_text)

    try:
        claude_output = await execute_claude(
            prompt_file=prompt_path,
            context={"condition": condition},
            work_dir=work_dir,
            timeout_seconds=120,
            allowed_tools=[],
            denied_tools=[],
            task_id="loop-condition-eval",
            stage_num=-1,
            output_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "boolean"},
                    "reasoning": {"type": "string"},
                },
                "required": ["result", "reasoning"],
            },
        )
        result = claude_output.structured
        if isinstance(result, dict) and "result" in result:
            return bool(result["result"])

        # Try to parse from raw output
        raw = claude_output.raw or ""
        if '"result": true' in raw.lower() or '"result":true' in raw.lower():
            return True
        if '"result": false' in raw.lower() or '"result":false' in raw.lower():
            return False

    except Exception as e:
        log.warning("ai_condition_eval_failed", error=str(e), condition=condition)
    finally:
        prompt_path.unlink(missing_ok=True)

    # On failure, assume condition is NOT met (continue looping until max)
    return False


def resolve_loop_stages(
    loop: LoopConfig,
    current_stage_idx: int,
    stage_defs: list[dict[str, Any]],
) -> list[int]:
    """Resolve which stage indices are included in a loop.

    If loop.loop_stages is empty, only the current stage is looped.
    Otherwise, find stage indices whose category matches the loop_stages list.
    """
    if not loop.loop_stages:
        return [current_stage_idx]

    indices = []
    for i, sdef in enumerate(stage_defs):
        if sdef["category"] in loop.loop_stages:
            indices.append(i)
    return indices if indices else [current_stage_idx]


# --- Git helpers ---


async def _git_checkout(clone_dir: str, branch: str) -> None:
    """Checkout a branch in the clone directory."""
    await _run_git(clone_dir, "checkout", branch)


async def _auto_commit(
    clone_dir: str, task_id: str, stage_num: int, category: str
) -> None:
    """Commit any uncommitted changes."""
    status = await _run_git(clone_dir, "status", "--porcelain")
    if not status.strip():
        return
    await _run_git(clone_dir, "add", "-A")
    await _run_git(
        clone_dir, "commit", "-m",
        f"chore(aquarco): {category} stage {stage_num} for {task_id}",
    )


async def _push_if_ahead(clone_dir: str, branch: str) -> None:
    """Push if local branch is ahead of remote."""
    ahead = await _get_ahead_count(clone_dir, branch)
    if ahead > 0:
        await _run_git(clone_dir, "push", "origin", branch)


async def _get_ahead_count(clone_dir: str, branch: str, base: str = "main") -> int:
    """Get number of commits ahead of the remote base branch."""
    result = await _run_git(
        clone_dir, "rev-list", "--count", f"origin/{base}..{branch}", check=False
    )
    try:
        return int(result) if result.strip() else 0
    except ValueError:
        return 0

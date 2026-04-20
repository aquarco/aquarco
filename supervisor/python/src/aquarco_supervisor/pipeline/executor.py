"""Pipeline orchestrator - delegates to planner, stage runner, and agent invoker.

This module is the top-level entry point for pipeline execution.  The heavy
lifting has been extracted into focused submodules:

- ``agent_invoker.py`` -- Claude CLI invocation with max-turns continuation
- ``planner.py`` -- planning phase (default plan or AI-generated)
- ``stage_runner.py`` -- stage execution loop with condition evaluation
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..cli.claude import _LOG_DIR as _CLAUDE_LOG_DIR
from ..cli.claude import execute_claude  # noqa: F401 — re-export for test backward compat
from ..config import get_pipeline_config
from ..database import Database
from ..exceptions import AuthenticationError, PipelineError, RetryableError, StageError
from ..logging import get_logger
from ..models import GitFlowConfig, PipelineConfig, TaskStatus
from ..stage_manager import StageManager
from ..task_queue import TaskQueue
from ..utils import run_cmd as _run_cmd
from ..utils import run_git as _run_git
from ..utils import url_to_slug
from .agent_invoker import AgentInvoker
from .agent_registry import AgentRegistry
from .conditions import (  # noqa: F401 — re-exported for backward compat
    _compare_complexity,
    check_conditions,
)
from .git_ops import _auto_commit, _get_ahead_count, _git_checkout, _push_if_ahead
from .git_workflow import (
    BranchInfo,
    find_active_release_branch,
    post_branch_created_comment,
    resolve_branch_info,
)
from .planner import PipelinePlanner
from .stage_runner import StageRunner

log = get_logger("pipeline")

# Branch names from external sources (GitHub webhooks, DB) must match this pattern
# before being passed to git subprocesses to prevent flag injection.
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]*$")


class PipelineExecutor:
    """Executes multi-stage pipelines by invoking Claude CLI agents."""

    def __init__(
        self,
        db: Database,
        task_queue: TaskQueue,
        registry: AgentRegistry,
        pipelines: list[PipelineConfig],
        *,
        stage_manager: StageManager | None = None,
    ) -> None:
        self._db = db
        self._tq = task_queue
        self._sm = stage_manager or StageManager(db)
        self._registry = registry
        self._pipelines = pipelines
        # Per-task execution_order counters.
        # Tracks the actual invocation sequence of stages within each task.
        self._execution_order: dict[str, int] = {}

        # Delegate to focused submodules
        self._invoker = AgentInvoker(db, registry, pipelines)
        self._planner = PipelinePlanner(
            task_queue, self._sm, registry,
            self._invoker, self._next_execution_order,
        )
        self._runner = StageRunner(
            db, task_queue, self._sm, registry,
            self._invoker, self._next_execution_order,
        )

    def _next_execution_order(self, task_id: str) -> int:
        """Increment and return the next execution_order value for a task."""
        current = self._execution_order.get(task_id, 0)
        next_val = current + 1
        self._execution_order[task_id] = next_val
        return next_val

    # -----------------------------------------------------------------------
    # Backward-compat bridge methods (used by legacy tests and callers)
    # -----------------------------------------------------------------------

    async def _execute_agent(
        self,
        agent_name: str,
        task_id: str,
        context: dict[str, Any],
        stage_num: int,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Invoke an agent via the runner's invoker. Mockable in tests."""
        return await self._runner._invoker.execute_agent(
            agent_name, task_id, context, stage_num, **kwargs
        )

    async def _execute_planned_stage(
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
        """Execute a single planned stage (backward-compat wrapper).

        Uses ``self._sm`` for stage management and ``self._execute_agent`` for
        agent invocation so that tests can mock either dependency directly.
        """
        stage_key = f"{stage_num}:{category}:{agent_name}"

        run = 1
        resume_session_id: str | None = None
        latest = await self._sm.get_latest_stage_run(task_id, stage_key, iteration)
        if latest and latest["status"] == "completed":
            log.warning(
                "completed_stage_guard",
                task_id=task_id,
                stage_key=stage_key,
                iteration=iteration,
                stage_id=latest.get("id"),
                msg="Refusing to re-execute a completed stage; returning existing output",
            )
            existing = await self._sm.get_stage_structured_output(latest["id"])
            return existing or {}, latest.get("id")
        elif latest and latest["status"] in ("failed", "rate_limited"):
            run = latest["run"] + 1
            resume_session_id = latest.get("session_id")
            stage_id = await self._sm.create_rerun_stage(
                task_id, stage_num, category, agent_name,
                stage_key, iteration, run,
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
        try:
            output = await self._execute_agent(
                agent_name, task_id, context, stage_num,
                work_dir=work_dir,
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
        except AuthenticationError:
            await self._sm.record_stage_failed(
                task_id, stage_num, "Claude authentication failed",
                stage_id=stage_id,
                stage_key=stage_key, iteration=iteration, run=run,
            )
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
        2. Planning -- AI agent assigns agents to categories
        3. Running -- execute planned stages with iteration loops
        """
        # Check for resume via last_completed_stage on the task
        task = await self._tq.get_task(task_id)
        start_stage = 0
        if task and task.last_completed_stage is not None:
            stage_number = await self._sm.get_stage_number_for_id(
                task.last_completed_stage
            )
            if stage_number is not None:
                start_stage = stage_number + 1
                log.info("resuming_pipeline", task_id=task_id, from_stage=start_stage)

        # Initialize per-task execution_order counter.
        # On resume, recover from existing DB rows to avoid collisions.
        if start_stage > 0:
            max_eo = await self._sm.get_max_execution_order(task_id)
            self._execution_order[task_id] = max_eo
            log.info(
                "execution_order_recovered",
                task_id=task_id,
                max_execution_order=max_eo,
            )
        else:
            self._execution_order[task_id] = 0

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
                planned_stages = self._planner.build_default_plan(stages)
                log.info("planning_skipped_fast_path", task_id=task_id)
            else:
                await self._tq.update_task_status(task_id, TaskStatus.PLANNING)
                planned_stages = await self._planner.execute_planning_phase(
                    task_id, pipeline_name, stages, context
                )

            await self._tq.store_planned_stages(task_id, planned_stages)
            stage_ids = await self._sm.create_planned_pending_stages(task_id, planned_stages)
        else:
            # Resuming: load planned_stages from DB
            task = await self._tq.get_task(task_id)
            if not task or not task.planned_stages:
                raise PipelineError(
                    f"Cannot resume task {task_id}: no planned_stages found"
                )
            planned_stages = task.planned_stages
            stage_ids: dict[str, int] = {}  # not available on resume

        # --- Phase 3: Running with iteration loops ---
        await self._tq.update_task_status(task_id, TaskStatus.EXECUTING)
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
            # Fresh start but stale worktree -- clean and recreate
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

        failed = await self._runner.execute_running_phase(
            task_id, planned_stages, stages, work_dir, branch_name,
            start_stage=start_stage,
            pipeline_name=pipeline_name,
            stage_ids=stage_ids,
        )

        if failed:
            # Keep worktree on failure for debugging / resume.
            log.info(
                "task_worktree_kept",
                task_id=task_id,
                work_dir=work_dir,
                reason="pipeline_failed",
            )
            return

        # Pipeline completed successfully -- commit any leftovers, then create PR.
        # Keep worktree alive for potential rerun; close_task_resources() cleans it.
        # Push directly from the worktree -- the branch is checked out here, so
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

        await self._create_pipeline_pr(task_id, branch_name, work_dir, {})
        await self._tq.complete_task(task_id)
        # Clean up per-task execution_order counter
        self._execution_order.pop(task_id, None)
        log.info("pipeline_completed", task_id=task_id, pipeline=pipeline_name)

    # -----------------------------------------------------------------------
    # Repository / branch helpers
    # -----------------------------------------------------------------------

    async def _resolve_clone_dir(self, task_id: str) -> str:
        """Get the clone directory for a task's repository."""
        row = await self._db.fetch_one(
            """
            SELECT r.clone_dir, r.clone_status FROM tasks t
            JOIN repositories r ON r.name = t.repository
            WHERE t.id = %(id)s
            """,
            {"id": task_id},
        )
        if not row:
            raise PipelineError(f"No clone_dir found for task {task_id}")
        clone_dir: str = row["clone_dir"]
        clone_status: str = row["clone_status"]
        if clone_status != "ready":
            raise PipelineError(
                f"Repository not ready for task {task_id}: clone_status={clone_status}"
            )
        if not Path(clone_dir).exists():
            raise PipelineError(
                f"Clone directory missing for task {task_id}: {clone_dir}"
            )
        return clone_dir

    async def _get_git_flow_config(self, task_id: str) -> GitFlowConfig | None:
        """Read git_flow_config JSONB from the DB for a task's repository.

        Returns None if the repository has no git_flow_config (Simple Branch mode).
        """
        row = await self._db.fetch_one(
            """
            SELECT r.git_flow_config FROM tasks t
            JOIN repositories r ON r.name = t.repository
            WHERE t.id = %(id)s
            """,
            {"id": task_id},
        )
        if not row:
            return None
        raw = row.get("git_flow_config") if isinstance(row, dict) else getattr(row, "git_flow_config", None)
        if not raw:
            return None
        if isinstance(raw, str):
            import json as _json
            raw = _json.loads(raw)
        cfg = GitFlowConfig(**raw)
        if not cfg.enabled:
            return None
        return cfg

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
        commits from previous stages -- we must **not** reset it to
        origin/base.

        Supports two modes:
        - **Simple Branch** (git_flow_config is None): uses ``aquarco/{task_id}/{slug}``
        - **Git Flow** (git_flow_config is set): uses label-driven branch naming
          (feature/*, bugfix/*, hotfix/*) with automatic base-branch resolution.
        """
        head_branch: str | None = context.get("head_branch")
        if head_branch:
            if not _SAFE_BRANCH_RE.match(head_branch):
                raise PipelineError(
                    f"Rejected unsafe head_branch value: '{head_branch}'"
                )
            await _run_git(work_dir, "checkout", "-B", head_branch, head_branch)
            return head_branch

        task = await self._tq.get_task(task_id)
        if not task:
            raise PipelineError(f"Task {task_id} not found")

        await _run_git(work_dir, "fetch", "origin")

        git_flow_cfg = await self._get_git_flow_config(task_id)

        if git_flow_cfg is not None:
            return await self._setup_git_flow_branch(
                task_id, task, git_flow_cfg, work_dir, resuming=resuming,
            )

        # Simple Branch mode (existing behaviour)
        slug = re.sub(r"[^a-z0-9]+", "-", task.title.lower()).strip("-")[:50]
        branch_name = f"aquarco/{task_id}/{slug}"

        if resuming:
            await _run_git(work_dir, "checkout", "-B", branch_name, branch_name)
        else:
            base_branch = await self._get_repo_branch(task_id)
            await _run_git(
                work_dir, "checkout", "-B", branch_name, f"origin/{base_branch}",
            )
        return branch_name

    async def _setup_git_flow_branch(
        self,
        task_id: str,
        task: Any,
        git_flow_cfg: GitFlowConfig,
        work_dir: str,
        *,
        resuming: bool = False,
    ) -> str:
        """Set up a branch using Git Flow rules."""
        # Extract labels from task context
        initial_ctx = task.initial_context or {}
        labels: list[str] = initial_ctx.get("labels", [])

        # Find active release branch for bugfix resolution
        active_release = await find_active_release_branch(
            work_dir,
            git_flow_cfg.branches.stable,
            git_flow_cfg.branches.release,
        )

        result = resolve_branch_info(
            git_flow_cfg, task_id, task.title, labels,
            active_release_branch=active_release,
        )

        if isinstance(result, str):
            # Error case — e.g. hotfix without target label
            # Post a comment on the GitHub issue explaining the problem
            repo_slug = await self._get_repo_slug(task_id)
            issue_number = initial_ctx.get("github_issue_number")
            if repo_slug and issue_number:
                try:
                    await _run_cmd(
                        "gh", "issue", "comment", str(issue_number),
                        "--repo", repo_slug,
                        "--body", f"⚠️ **Branch not created**\n\n{result}",
                    )
                except Exception as e:
                    log.warning(
                        "hotfix_comment_failed",
                        task_id=task_id,
                        error=str(e),
                    )
            raise PipelineError(
                f"Git Flow branch resolution failed for {task_id}: {result}"
            )

        branch_info: BranchInfo = result

        if resuming:
            await _run_git(
                work_dir, "checkout", "-B",
                branch_info.branch_name, branch_info.branch_name,
            )
        else:
            await _run_git(
                work_dir, "checkout", "-B",
                branch_info.branch_name,
                f"origin/{branch_info.base_branch}",
            )

        # Store the base branch in checkpoint_data for PR creation
        await self._db.execute(
            """
            UPDATE tasks
            SET checkpoint_data = COALESCE(checkpoint_data, '{}'::jsonb) || %(data)s::jsonb
            WHERE id = %(id)s
            """,
            {
                "id": task_id,
                "data": json.dumps({
                    "git_flow_base_branch": branch_info.base_branch,
                    "git_flow_branch_type": branch_info.branch_type,
                }),
            },
        )

        # Post branch-created comment on the GitHub issue
        repo_slug = await self._get_repo_slug(task_id)
        issue_number = initial_ctx.get("github_issue_number")
        if repo_slug and issue_number:
            try:
                await post_branch_created_comment(
                    repo_slug, issue_number, branch_info,
                )
            except Exception as e:
                log.warning(
                    "branch_created_comment_failed",
                    task_id=task_id,
                    error=str(e),
                )

        return branch_info.branch_name

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
    ) -> None:
        """Create or update a PR after pipeline completion."""
        task = await self._tq.get_task(task_id)
        if not task:
            return

        context = task.initial_context or {}
        head_branch = context.get("head_branch")

        if head_branch:
            # PR review: comment on existing PR
            stage_num = 0
            if task.last_completed_stage is not None:
                sn = await self._sm.get_stage_number_for_id(task.last_completed_stage)
                if sn is not None:
                    stage_num = sn
            await _auto_commit(clone_dir, task_id, stage_num, "review")
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
            # Use git flow base branch if available, otherwise fall back to repo.branch
            checkpoint = task.checkpoint_data or {}
            base_branch = checkpoint.get(
                "git_flow_base_branch",
                await self._get_repo_branch(task_id),
            )
            ahead = await _get_ahead_count(clone_dir, branch_name, base_branch)
            if ahead == 0:
                log.info("no_commits_to_push", task_id=task_id)
                return

            # --force is safe: these branches are exclusively owned by the
            # pipeline, so there is no risk of overwriting human work.
            # (--force-with-lease fails with "stale info" in worktrees
            # because the remote-tracking ref lives in the parent repo.)
            await _run_git(clone_dir, "push", "origin", branch_name, "--force")
            repo_slug = await self._get_repo_slug(task_id)
            if repo_slug:
                # Check if a PR already exists for this branch
                existing_pr = await _run_cmd(
                    "gh", "pr", "view", branch_name,
                    "--repo", repo_slug,
                    "--json", "number,url",
                    check=False,
                )
                if existing_pr:
                    pr_match = re.search(r'"number"\s*:\s*(\d+)', existing_pr)
                    if pr_match:
                        pr_number = int(pr_match.group(1))
                        log.info("pr_already_exists", task_id=task_id, pr_number=pr_number)
                        await self._tq.store_pr_info(
                            task_id, pr_number, branch_name,
                        )
                        return

                # Use the branch type for the PR title prefix
                branch_type = checkpoint.get("git_flow_branch_type", "feat")
                pr_prefix_map = {
                    "feature": "feat",
                    "bugfix": "fix",
                    "hotfix": "fix",
                }
                pr_prefix = pr_prefix_map.get(branch_type, "feat")

                # Include Closes #N if we have the issue number
                initial_ctx = task.initial_context or {}
                issue_num = initial_ctx.get("github_issue_number")
                pr_body = f"Automated PR for task {task_id}"
                if issue_num:
                    pr_body += f"\n\nCloses #{issue_num}"

                pr_output = await _run_cmd(
                    "gh", "pr", "create",
                    "--repo", repo_slug,
                    "--head", branch_name,
                    "--base", base_branch,
                    "--title", f"{pr_prefix}: {task.title}",
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
        """Remove worktrees and raw NDJSON output logs for a closed task."""
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

        # Delete raw NDJSON output files written by execute_claude for this task.
        deleted = 0
        for raw_log in _CLAUDE_LOG_DIR.glob(f"claude-raw-{safe_id}-stage*.ndjson"):
            try:
                raw_log.unlink()
                deleted += 1
            except OSError:
                pass
        if deleted:
            log.info("task_raw_logs_cleaned", task_id=task_id, count=deleted)

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



# Backward-compatible re-export — _resolve_field was moved to conditions.py
from .conditions import _resolve_field as _resolve_field  # noqa: F401, E402

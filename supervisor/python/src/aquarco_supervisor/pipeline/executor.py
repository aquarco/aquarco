"""Pipeline orchestrator - delegates to planner, stage runner, and agent invoker.

This module is the top-level entry point for pipeline execution.  The heavy
lifting has been extracted into focused submodules:

- ``agent_invoker.py`` -- Claude CLI invocation with max-turns continuation
- ``planner.py`` -- planning phase (default plan or AI-generated)
- ``stage_runner.py`` -- stage execution loop with condition evaluation
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..cli.claude import _LOG_DIR as _CLAUDE_LOG_DIR
from ..cli.claude import execute_claude  # noqa: F401 — re-export for test backward compat
from ..config import get_pipeline_config
from ..database import Database
from ..exceptions import PipelineError
from ..logging import get_logger
from ..models import Complexity, PipelineConfig, TaskStatus
from ..stage_manager import StageManager
from ..task_queue import TaskQueue
from ..utils import run_cmd as _run_cmd
from ..utils import run_git as _run_git
from ..utils import url_to_slug
from .agent_invoker import AgentInvoker
from .agent_registry import AgentRegistry
from .git_ops import _auto_commit, _get_ahead_count, _git_checkout, _push_if_ahead
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
            task_queue, stage_manager, registry,
            self._invoker, self._next_execution_order,
        )
        self._runner = StageRunner(
            db, task_queue, stage_manager, registry,
            self._invoker, self._next_execution_order,
        )

    def _next_execution_order(self, task_id: str) -> int:
        """Increment and return the next execution_order value for a task."""
        current = self._execution_order.get(task_id, 0)
        next_val = current + 1
        self._execution_order[task_id] = next_val
        return next_val

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
            # Branch already exists with work from earlier stages -- just
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
            base_branch = await self._get_repo_branch(task_id)
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

                pr_output = await _run_cmd(
                    "gh", "pr", "create",
                    "--repo", repo_slug,
                    "--head", branch_name,
                    "--title", f"feat: {task.title}",
                    "--body", f"Automated PR for task {task_id}",
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


# -----------------------------------------------------------------------
# Free functions (conditions)
# -----------------------------------------------------------------------


def check_conditions(
    conditions: list[str] | list[dict[str, Any]], previous_output: dict[str, Any]
) -> bool:
    """Evaluate stage conditions against previous output (sync bridge).

    Supports both legacy string format ("field operator value") and
    new structured format (list of condition dicts with simple/ai keys).

    Note: ai: conditions are skipped in this sync bridge. Use
    evaluate_conditions() directly for full async AI support.
    """
    if not conditions:
        return True

    # Detect format: if first item is a dict, use new structured evaluation
    if conditions and isinstance(conditions[0], dict):
        from .conditions import evaluate_simple_expression, _build_eval_context
        context = _build_eval_context({}, previous_output)
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if "simple" in cond:
                raw = cond["simple"]
                val = raw if isinstance(raw, bool) else evaluate_simple_expression(str(raw), context)
                jump = cond.get("yes" if val else "no") or cond.get(True if val else False)
                if jump is not None:
                    return False  # jump means "don't proceed linearly"
        return True

    # Legacy string-based format
    for condition in conditions:
        if not isinstance(condition, str):
            continue
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

"""Pipeline stage execution engine."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from ..cli.claude import execute_claude
from ..config import get_pipeline_config
from ..database import Database
from ..exceptions import NoAvailableAgentError, PipelineError, StageError
from ..logging import get_logger
from ..models import Complexity, SupervisorConfig
from ..task_queue import TaskQueue
from ..utils import run_cmd as _run_cmd
from ..utils import run_git as _run_git
from ..utils import url_to_slug
from .agent_registry import AgentRegistry
from .context import build_accumulated_context

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
        config: SupervisorConfig,
    ) -> None:
        self._db = db
        self._tq = task_queue
        self._registry = registry
        self._config = config

    async def execute_pipeline(
        self,
        pipeline_name: str,
        task_id: str,
        context: dict[str, Any],
    ) -> None:
        """Execute a full pipeline for a task."""
        # Check for resume checkpoint
        checkpoint = await self._tq.get_checkpoint(task_id)
        start_stage = 0
        if checkpoint:
            start_stage = checkpoint["last_completed_stage"] + 1
            log.info("resuming_pipeline", task_id=task_id, from_stage=start_stage)

        # Get pipeline stages from config
        if not pipeline_name:
            # Single-stage execution based on task category
            task = await self._tq.get_task(task_id)
            if not task:
                raise PipelineError(f"Task {task_id} not found")
            await self._execute_single_stage(task_id, task.category, context)
            return

        stages = get_pipeline_config(self._config, pipeline_name)
        if not stages:
            raise PipelineError(f"Pipeline '{pipeline_name}' not found in config")

        stage_count = len(stages)

        # Create all stage records on first run
        if start_stage == 0:
            await self._tq.create_pending_stages(task_id, stages)

        # Setup branch
        clone_dir = await self._resolve_clone_dir(task_id)
        branch_name = await self._setup_branch(task_id, context, clone_dir)

        previous_output: dict[str, Any] = {}

        for stage_num in range(start_stage, stage_count):
            stage_def = stages[stage_num]
            category = stage_def["category"]
            required = stage_def.get("required", True)
            conditions = stage_def.get("conditions", [])

            # Check conditions
            if conditions and not check_conditions(conditions, previous_output):
                log.info("stage_skipped_conditions", task_id=task_id, stage=stage_num)
                await self._tq.record_stage_skipped(task_id, stage_num, category)
                continue

            # Ensure we're on the right branch
            await _git_checkout(clone_dir, branch_name)

            # Build context
            task_context = await self._tq.get_task_context(task_id) or {}
            accumulated = build_accumulated_context(task_context, stage_num, previous_output)

            try:
                stage_output = await self._execute_stage(
                    category, task_id, accumulated, previous_output, stage_num
                )
                previous_output = stage_output

                # Store output and checkpoint
                agent_name = stage_output.get("_agent_name", "unknown")
                await self._tq.store_stage_output(
                    task_id, stage_num, category, agent_name, stage_output
                )
                await self._tq.checkpoint_pipeline(task_id, stage_num)

                # Auto-commit any changes
                await _auto_commit(clone_dir, task_id, stage_num, category)

            except (StageError, NoAvailableAgentError) as e:
                if required:
                    await self._tq.fail_task(task_id, str(e))
                    return
                else:
                    log.warning(
                        "optional_stage_failed",
                        task_id=task_id,
                        stage=stage_num,
                        error=str(e),
                    )
                    await self._tq.record_stage_skipped(task_id, stage_num, category)

        # Pipeline completed successfully
        await self._create_pipeline_pr(task_id, branch_name, clone_dir, previous_output)
        await self._tq.complete_task(task_id)
        await self._tq.delete_checkpoint(task_id)
        log.info("pipeline_completed", task_id=task_id, pipeline=pipeline_name)

    async def _execute_stage(
        self,
        category: str,
        task_id: str,
        context: dict[str, Any],
        previous_output: dict[str, Any],
        stage_num: int,
    ) -> dict[str, Any]:
        """Execute a single pipeline stage."""
        agent_name = await self._registry.select_agent(category)
        await self._tq.record_stage_executing(task_id, stage_num, category, agent_name)
        await self._registry.increment_agent_instances(agent_name)

        try:
            output = await self._execute_agent(
                agent_name, task_id, context, previous_output, stage_num
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
        previous_output: dict[str, Any],
        stage_num: int,
    ) -> dict[str, Any]:
        """Invoke the Claude CLI for an agent."""
        prompt_file = self._registry.get_agent_prompt_file(agent_name)
        timeout_minutes = self._registry.get_agent_timeout(agent_name)
        clone_dir = await self._resolve_clone_dir(task_id)

        agent_context = {
            "task_id": task_id,
            "agent": agent_name,
            "stage_number": stage_num,
            "accumulated_context": context,
            "previous_stage_output": previous_output,
        }

        output = await execute_claude(
            prompt_file=prompt_file,
            context=agent_context,
            work_dir=clone_dir,
            timeout_seconds=timeout_minutes * 60,
            allowed_tools=self._registry.get_allowed_tools(agent_name),
            denied_tools=self._registry.get_denied_tools(agent_name),
            task_id=task_id,
            stage_num=stage_num,
        )

        output["_agent_name"] = agent_name

        # Save output log (sanitize task_id to prevent path traversal)
        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)
        output_log = Path(f"/var/log/aifishtank/agent-output-{safe_id}-stage{stage_num}.json")
        output_log.parent.mkdir(parents=True, exist_ok=True)
        output_log.write_text(json.dumps(output, indent=2))

        return output

    async def _execute_single_stage(
        self,
        task_id: str,
        category: str,
        context: dict[str, Any],
    ) -> None:
        """Execute a task as a single stage (no pipeline)."""
        try:
            stage_output = await self._execute_stage(
                category, task_id, context, {}, 0
            )
            await self._maybe_create_single_stage_pr(
                task_id, category, stage_output
            )
            await self._tq.complete_task(task_id)
        except (StageError, NoAvailableAgentError) as e:
            await self._tq.fail_task(task_id, str(e))

    async def _maybe_create_single_stage_pr(
        self,
        task_id: str,
        category: str,
        stage_output: dict[str, Any],
    ) -> None:
        """Create a PR after single-stage execution if there are code changes."""
        try:
            clone_dir = await self._resolve_clone_dir(task_id)
        except PipelineError:
            return

        task = await self._tq.get_task(task_id)
        if not task:
            return

        # Check for uncommitted changes or agent-created branch
        status = await _run_git(clone_dir, "status", "--porcelain")
        branch = await _run_git(clone_dir, "rev-parse", "--abbrev-ref", "HEAD")

        has_changes = bool(status.strip())
        on_agent_branch = branch.startswith("aifishtank/")

        if not has_changes and not on_agent_branch:
            # No code changes — for review tasks, post output as comment
            if category == "review" and task.source_ref:
                repo_slug = await self._get_repo_slug(task_id)
                if repo_slug:
                    summary = stage_output.get("summary", "Review completed")
                    if isinstance(summary, dict):
                        summary = json.dumps(summary, indent=2)
                    await _run_cmd(
                        "gh", "issue", "comment", task.source_ref,
                        "--repo", repo_slug,
                        "--body", str(summary),
                    )
            return

        # Create branch if needed
        if has_changes and not on_agent_branch:
            branch_name = f"aifishtank/{task_id}"
            await _run_git(clone_dir, "checkout", "-b", branch_name)
            on_agent_branch = True

        # Commit, push, create PR
        if has_changes:
            await _auto_commit(clone_dir, task_id, 0, category)

        if on_agent_branch:
            current_branch = await _run_git(
                clone_dir, "rev-parse", "--abbrev-ref", "HEAD"
            )
            base = await self._get_repo_branch(task_id)
            ahead = await _get_ahead_count(clone_dir, current_branch, base)
            if ahead > 0:
                await _run_git(
                    clone_dir, "push", "origin", current_branch, "--force-with-lease"
                )
                repo_slug = await self._get_repo_slug(task_id)
                if repo_slug:
                    await _run_cmd(
                        "gh", "pr", "create",
                        "--repo", repo_slug,
                        "--head", current_branch,
                        "--title", f"feat: {task.title}",
                        "--body", f"Automated PR for task {task_id}",
                    )

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
        self, task_id: str, context: dict[str, Any], clone_dir: str
    ) -> str:
        """Set up the git branch for pipeline execution."""
        head_branch: str | None = context.get("head_branch")
        if head_branch:
            # Validate branch name from external context before passing to git.
            if not _SAFE_BRANCH_RE.match(head_branch):
                raise PipelineError(
                    f"Rejected unsafe head_branch value: '{head_branch}'"
                )
            # PR review: use existing branch
            await _git_checkout(clone_dir, head_branch)
            return head_branch

        # Feature pipeline: create new branch
        task = await self._tq.get_task(task_id)
        if not task:
            raise PipelineError(f"Task {task_id} not found")

        slug = re.sub(r"[^a-z0-9]+", "-", task.title.lower()).strip("-")[:50]
        branch_name = f"aifishtank/{task_id}/{slug}"

        # Use the repo's configured default branch
        base_branch = await self._get_repo_branch(task_id)
        await _run_git(clone_dir, "fetch", "origin")
        await _run_git(clone_dir, "checkout", "-b", branch_name, f"origin/{base_branch}")
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
        return row["branch"] if row else "main"

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

            await _run_git(clone_dir, "push", "origin", branch_name, "--force-with-lease")
            repo_slug = await self._get_repo_slug(task_id)
            if repo_slug:
                await _run_cmd(
                    "gh", "pr", "create",
                    "--repo", repo_slug,
                    "--head", branch_name,
                    "--title", f"feat: {task.title}",
                    "--body", f"Automated PR for task {task_id}",
                )

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


# --- Git helpers ---


async def _git_checkout(clone_dir: str, branch: str) -> None:
    """Checkout a branch in the clone directory."""
    await _run_git(clone_dir, "checkout", "--", branch)


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
        f"chore(aifishtank): {category} stage {stage_num} for {task_id}",
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

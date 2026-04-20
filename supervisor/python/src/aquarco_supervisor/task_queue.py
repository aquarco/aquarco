"""Task queue operations - task lifecycle, poll state, and spending queries.

Stage-specific operations (create, record, update) have been extracted to
``stage_manager.py``.  This module retains task lifecycle management, poll
state, spending aggregation, and backward-compatible re-exports.
"""

from __future__ import annotations

import json
from typing import Any

from .database import Database
from .logging import get_logger
from .models import Task, TaskStatus

# Backward-compatible re-exports so existing callers don't break.
from .stage_manager import StageManager  # noqa: F401
from .stage_manager import _resolve_stage_status  # noqa: F401

log = get_logger("task-queue")


class TaskQueue:
    """Manages task lifecycle and poll state in PostgreSQL.

    Stage-specific methods live on StageManager.  This class delegates
    attribute lookups for those methods so existing callers keep working.
    New code should use StageManager directly.
    """

    def __init__(self, db: Database, max_retries: int = 3) -> None:
        self._db = db
        self._max_retries = max_retries
        self._sm = StageManager(db)

    def __getattr__(self, name: str) -> Any:
        """Delegate stage methods to StageManager for backward compat."""
        try:
            return getattr(self._sm, name)
        except AttributeError:
            raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'") from None

    async def create_task(
        self,
        task_id: str,
        title: str,
        source: str,
        source_ref: str,
        repository: str,
        pipeline: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Create a new task. Returns True if created, False if already exists."""
        result = await self._db.fetch_one(
            """
            INSERT INTO tasks
                (id, title, source, source_ref, repository, pipeline, initial_context)
            VALUES (%(id)s, %(title)s, %(source)s, %(source_ref)s,
                    %(repository)s, %(pipeline)s, %(context)s::jsonb)
            ON CONFLICT (id) DO NOTHING
            RETURNING id
            """,
            {
                "id": task_id,
                "title": title,
                "source": source,
                "source_ref": source_ref,
                "repository": repository,
                "pipeline": pipeline,
                "context": json.dumps(context or {}),
            },
        )
        created = result is not None
        if created:
            log.info("task_created", task_id=task_id, pipeline=pipeline)
        return created

    async def get_next_task(self) -> Task | None:
        """Atomically claim the next pending task. Returns None if queue is empty."""
        row = await self._db.fetch_one(
            """
            UPDATE tasks
            SET status = 'queued', updated_at = NOW()
            WHERE id = (
                SELECT id FROM tasks
                WHERE status = 'pending'
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, title, pipeline, pipeline_version, repository,
                      source, source_ref,
                      status, priority, initial_context, planned_stages,
                      created_at, updated_at,
                      started_at, completed_at,
                      last_completed_stage, checkpoint_data,
                      retry_count, error_message,
                      parent_task_id, pr_number, branch_name
            """
        )
        if row is None:
            return None
        return Task.model_validate(row)

    async def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Update task status with appropriate timestamp handling."""
        params = {"id": task_id, "status": status.value}

        if status in (TaskStatus.EXECUTING, TaskStatus.PLANNING):
            query = """
                UPDATE tasks
                SET status = %(status)s, updated_at = NOW(),
                    started_at = COALESCE(started_at, NOW()), error_message = NULL
                WHERE id = %(id)s
            """
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
            query = """
                UPDATE tasks
                SET status = %(status)s, updated_at = NOW(), completed_at = NOW()
                WHERE id = %(id)s
            """
        else:
            query = """
                UPDATE tasks
                SET status = %(status)s, updated_at = NOW()
                WHERE id = %(id)s
            """

        await self._db.execute(query, params)

    async def task_exists(self, task_id: str) -> bool:
        """Check if a task with the given ID exists."""
        count = await self._db.fetch_val(
            "SELECT COUNT(*) FROM tasks WHERE id = %(id)s",
            {"id": task_id},
        )
        return bool(count and count > 0)

    async def get_task(self, task_id: str) -> Task | None:
        """Fetch a task by ID."""
        row = await self._db.fetch_one(
            """
            SELECT id, title, status, priority, source, source_ref,
                   pipeline, pipeline_version, repository,
                   initial_context, planned_stages,
                   created_at, updated_at,
                   started_at, completed_at,
                   last_completed_stage, checkpoint_data,
                   retry_count, error_message,
                   parent_task_id, pr_number, branch_name
            FROM tasks WHERE id = %(id)s
            """,
            {"id": task_id},
        )
        if row is None:
            return None
        return Task.model_validate(row)

    async def fail_task(self, task_id: str, error_message: str) -> None:
        """Increment retry count and either reset to pending or mark failed."""
        await self._db.execute(
            """
            UPDATE tasks
            SET retry_count = retry_count + 1,
                error_message = %(error)s,
                status = CASE
                    WHEN retry_count + 1 >= %(max_retries)s THEN 'failed'
                    ELSE 'pending'
                END,
                updated_at = NOW(),
                completed_at = CASE
                    WHEN retry_count + 1 >= %(max_retries)s THEN NOW()
                    ELSE NULL
                END
            WHERE id = %(id)s
            """,
            {"id": task_id, "error": error_message, "max_retries": self._max_retries},
        )
        log.warning("task_failed", task_id=task_id, error=error_message)

    async def postpone_task(
        self,
        task_id: str,
        error_message: str,
        *,
        cooldown_minutes: int = 60,
        max_retries: int = 24,
    ) -> None:
        """Mark task as rate-limited with a per-task cooldown, or fail if retries exhausted.

        Each postpone hit increments ``rate_limit_count`` and stores the
        *cooldown_minutes* so that ``get_postponed_tasks()`` can apply a
        per-row cooldown when deciding when to resume.

        After *max_retries* (default 24) the task is marked ``failed``.
        """
        await self._db.execute(
            """
            UPDATE tasks
            SET rate_limit_count = rate_limit_count + 1,
                error_message = %(error)s,
                postpone_cooldown_minutes = %(cooldown)s,
                status = CASE
                    WHEN rate_limit_count + 1 >= %(max)s THEN 'failed'
                    ELSE 'rate_limited'
                END,
                completed_at = CASE
                    WHEN rate_limit_count + 1 >= %(max)s THEN NOW()
                    ELSE NULL
                END,
                updated_at = NOW()
            WHERE id = %(id)s
            """,
            {
                "id": task_id,
                "error": error_message,
                "cooldown": cooldown_minutes,
                "max": max_retries,
            },
        )
        row = await self._db.fetch_one(
            "SELECT status, rate_limit_count FROM tasks WHERE id = %(id)s",
            {"id": task_id},
        )
        count = row["rate_limit_count"] if row else 0
        event = "task_postpone_exhausted" if row and row["status"] == "failed" else "task_postponed"
        log.warning(event, task_id=task_id, cooldown_minutes=cooldown_minutes, rate_limit_count=count)

    async def rate_limit_task(
        self, task_id: str, error_message: str, *, max_rate_limit_retries: int = 24,
    ) -> None:
        """Backward-compat alias for postpone_task with 60-min cooldown."""
        await self.postpone_task(task_id, error_message, cooldown_minutes=60, max_retries=max_rate_limit_retries)

    async def get_postponed_tasks(self) -> list[str]:
        """Return task IDs whose per-row cooldown has elapsed and are ready to resume."""
        rows = await self._db.fetch_all(
            """
            SELECT id FROM tasks
            WHERE status = 'rate_limited'
              AND updated_at < NOW() - make_interval(mins := postpone_cooldown_minutes)
            ORDER BY updated_at ASC
            """
        )
        return [r["id"] for r in rows]

    # Deprecated alias — prefer get_postponed_tasks().
    async def get_rate_limited_tasks(self, cooldown_minutes: int = 60) -> list[str]:
        return await self.get_postponed_tasks()

    async def resume_rate_limited_task(self, task_id: str) -> None:
        """Move a rate-limited task back to pending for retry."""
        await self._db.execute(
            """
            UPDATE tasks
            SET status = 'pending', started_at = NULL, updated_at = NOW()
            WHERE id = %(id)s AND status = 'rate_limited'
            """,
            {"id": task_id},
        )
        # Reset rate-limited stages back to pending
        await self._db.execute(
            """
            UPDATE stages
            SET status = 'pending', started_at = NULL, completed_at = NULL,
                error_message = NULL
            WHERE task_id = %(id)s AND status = 'rate_limited'
            """,
            {"id": task_id},
        )
        log.info("task_rate_limit_resumed", task_id=task_id)

    async def reset_task_to_pending(self, task_id: str, reason: str) -> None:
        """Reset an executing task back to pending (e.g. when auth is broken)."""
        await self._db.execute(
            """
            UPDATE tasks
            SET status = 'pending', error_message = %(reason)s, updated_at = NOW()
            WHERE id = %(id)s AND status = 'executing'
            """,
            {"id": task_id, "reason": reason},
        )
        log.info("task_reset_to_pending", task_id=task_id, reason=reason)

    async def complete_task(self, task_id: str) -> None:
        """Mark a task as completed."""
        await self._db.execute(
            """
            UPDATE tasks
            SET status = 'completed', completed_at = NOW(), updated_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": task_id},
        )
        log.info("task_completed", task_id=task_id)

    async def get_task_spending(self, task_id: str) -> dict[str, Any]:
        """Aggregate spending across all completed stages for a task."""
        row = await self._db.fetch_one(
            """
            SELECT
                COALESCE(SUM(tokens_input), 0)       AS input_tokens,
                COALESCE(SUM(cache_write_tokens), 0)  AS cache_write_tokens,
                COALESCE(SUM(cache_read_tokens), 0)   AS cache_read_tokens,
                COALESCE(SUM(tokens_output), 0)       AS output_tokens,
                COALESCE(SUM(cost_usd), 0)            AS total_cost_usd
            FROM stages
            WHERE task_id = %(id)s AND status = 'completed'
            """,
            {"id": task_id},
        )
        return dict(row) if row else {}

    async def get_live_stage_spending(
        self, task_id: str, stage_key: str,
    ) -> dict[str, Any] | None:
        """Parse live_output of an in-progress stage for current spending."""
        from .spending import parse_ndjson_spending

        row = await self._db.fetch_one(
            """
            SELECT raw_output FROM stages
            WHERE task_id = %(id)s AND stage_key = %(sk)s
              AND status = 'executing'
            """,
            {"id": task_id, "sk": stage_key},
        )
        if row and row["raw_output"]:
            summary = parse_ndjson_spending(row["raw_output"])
            return {
                "input_tokens": summary.total_input,
                "cache_write_tokens": summary.total_cache_write,
                "cache_read_tokens": summary.total_cache_read,
                "output_tokens": summary.total_output,
                "estimated_cost_usd": summary.estimated_cost_usd,
                "model": summary.model,
                "turns": len(summary.turns),
            }
        return None

    async def get_timed_out_tasks(self, timeout_minutes: int = 60) -> list[str]:
        """Get IDs of tasks that have been executing longer than the timeout."""
        rows = await self._db.fetch_all(
            """
            SELECT id FROM tasks
            WHERE status = 'executing'
              AND started_at < NOW() - make_interval(mins => %(mins)s)
            """,
            {"mins": timeout_minutes},
        )
        return [row["id"] for row in rows]

    # --- Poll State ---

    async def update_poll_state(
        self,
        poller_name: str,
        cursor: str,
        state_data: dict[str, Any] | None = None,
    ) -> None:
        """Upsert poller state with cursor and optional state data."""
        await self._db.execute(
            """
            INSERT INTO poll_state
                (poller_name, last_poll_at, last_successful_at, cursor, state_data)
            VALUES (%(name)s, NOW(), NOW(), %(cursor)s, %(data)s::jsonb)
            ON CONFLICT (poller_name) DO UPDATE
            SET last_poll_at = NOW(), last_successful_at = NOW(),
                cursor = %(cursor)s, state_data = %(data)s::jsonb
            """,
            {
                "name": poller_name,
                "cursor": cursor,
                "data": json.dumps(state_data or {}),
            },
        )

    async def get_poll_cursor(self, poller_name: str) -> str:
        """Get the last cursor for a poller. Returns empty string if not found."""
        result = await self._db.fetch_val(
            "SELECT COALESCE(cursor, '') FROM poll_state WHERE poller_name = %(name)s",
            {"name": poller_name},
        )
        return result or ""

    # --- Phase & Planning ---

    async def store_planned_stages(
        self, task_id: str, planned_stages: list[dict[str, Any]]
    ) -> None:
        """Store planner output (agent assignments per category) on the task."""
        await self._db.execute(
            """
            UPDATE tasks SET planned_stages = %(stages)s::jsonb, updated_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": task_id, "stages": json.dumps(planned_stages)},
        )

    async def retry_task(self, task_id: str) -> None:
        """Reset the latest failed/rate_limited stage and task to pending (RETRY semantics)."""
        # Reset latest failed/rate_limited stage
        await self._db.execute(
            """
            UPDATE stages
            SET status = 'pending', error_message = NULL,
                started_at = NULL, completed_at = NULL,
                structured_output = NULL, raw_output = NULL, live_output = NULL
            WHERE task_id = %(id)s AND id = (
                SELECT id FROM stages WHERE task_id = %(id)s
                AND status IN ('failed', 'rate_limited')
                ORDER BY stage_number DESC, run DESC LIMIT 1
            )
            """,
            {"id": task_id},
        )
        # Reset task to pending
        await self._db.execute(
            """
            UPDATE tasks
            SET status = 'pending', error_message = NULL, updated_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": task_id},
        )
        log.info("task_retried", task_id=task_id)

    async def rerun_task(self, task_id: str) -> str:
        """Create a new task referencing the original (RERUN semantics). Returns new task ID."""
        import uuid

        original = await self._db.fetch_one(
            """
            SELECT id, title, source, source_ref, repository,
                   pipeline, pipeline_version, initial_context
            FROM tasks WHERE id = %(id)s
            """,
            {"id": task_id},
        )
        if not original:
            raise ValueError(f"Task {task_id} not found")

        source_ref = original["source_ref"] or task_id
        # Use a short UUID suffix to avoid primary key collisions under concurrency.
        short_uid = uuid.uuid4().hex[:8]
        new_id = f"{source_ref}-rerun-{short_uid}"

        await self._db.execute(
            """
            INSERT INTO tasks
                (id, title, source, source_ref, repository, pipeline,
                 pipeline_version, initial_context, parent_task_id)
            VALUES (%(new_id)s, %(title)s, %(source)s, %(source_ref)s,
                    %(repository)s, %(pipeline)s, %(pipeline_version)s,
                    %(context)s::jsonb, %(parent_id)s)
            """,
            {
                "new_id": new_id,
                "title": original["title"],
                "source": original["source"],
                "source_ref": original["source_ref"],
                "repository": original["repository"],
                "pipeline": original["pipeline"],
                "pipeline_version": original["pipeline_version"],
                "context": json.dumps(original["initial_context"] or {}),
                "parent_id": task_id,
            },
        )
        log.info("task_rerun_created", task_id=task_id, new_task_id=new_id)
        return new_id

    async def close_task(self, task_id: str) -> None:
        """Mark a task as closed."""
        await self._db.execute(
            """
            UPDATE tasks SET status = 'closed', updated_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": task_id},
        )
        log.info("task_closed", task_id=task_id)

    async def store_pr_info(
        self, task_id: str, pr_number: int, branch_name: str
    ) -> None:
        """Save PR details after creation."""
        await self._db.execute(
            """
            UPDATE tasks
            SET pr_number = %(pr_number)s, branch_name = %(branch)s, updated_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": task_id, "pr_number": pr_number, "branch": branch_name},
        )

    async def get_tasks_pending_close(self) -> list[dict[str, Any]]:
        """Find completed tasks with PR numbers (candidates for auto-close)."""
        rows = await self._db.fetch_all(
            """
            SELECT id, pr_number, repository
            FROM tasks
            WHERE status = 'completed' AND pr_number IS NOT NULL
            ORDER BY completed_at ASC
            """
        )
        return [dict(r) for r in rows]


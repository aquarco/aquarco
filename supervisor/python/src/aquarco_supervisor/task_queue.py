"""Task queue operations - all PostgreSQL task and poll state queries."""

from __future__ import annotations

import json
import warnings
from typing import Any


def _resolve_stage_status(
    output: dict[str, Any],
    raw_output: str | None,
) -> tuple[str, str | None]:
    """Determine the correct stage status and error message from agent output.

    Rules (in priority order):
    - subtype="success", is_error=False  → completed
    - subtype="error_max_turns"          → max_turns
    - subtype="success", is_error=True   → scan raw_output for rate_limit_event
                                           → rate_limited (with resetsAt) or failed
    - anything else                      → failed
    """
    subtype = output.get("_subtype")
    is_error = output.get("_is_error", False)

    if subtype == "success" and not is_error:
        return "completed", None

    if subtype == "error_max_turns":
        return "max_turns", "Agent reached max_turns limit"

    if subtype == "success" and is_error:
        if raw_output:
            for line in raw_output.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if isinstance(msg, dict) and msg.get("type") == "rate_limit_event":
                        info = msg.get("rate_limit_info", {})
                        resets_at = info.get("resetsAt")
                        return "rate_limited", f"Rate limited by Claude API; resetsAt={resets_at}"
                except json.JSONDecodeError:
                    continue
        return "failed", "Agent returned is_error=true with no rate limit event"

    return "failed", f"Unexpected output subtype={subtype!r}"

from .database import Database
from .logging import get_logger
from .models import Task, TaskStatus
from .spending import parse_ndjson_spending

log = get_logger("task-queue")


class TaskQueue:
    """Manages task lifecycle and poll state in PostgreSQL."""

    def __init__(self, db: Database, max_retries: int = 3) -> None:
        self._db = db
        self._max_retries = max_retries

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
        elif status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT):
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
        if row and row["status"] == "failed":
            log.warning(
                "task_postpone_exhausted",
                task_id=task_id,
                rate_limit_count=row["rate_limit_count"],
            )
        else:
            log.warning(
                "task_postponed",
                task_id=task_id,
                cooldown_minutes=cooldown_minutes,
                rate_limit_count=row["rate_limit_count"] if row else 0,
            )

    async def rate_limit_task(
        self, task_id: str, error_message: str, *, max_rate_limit_retries: int = 24,
    ) -> None:
        """Mark task as rate-limited, or fail it if retries exhausted.

        Delegates to :meth:`postpone_task` with a 60-minute cooldown.
        Kept for backward compatibility.
        """
        await self.postpone_task(
            task_id,
            error_message,
            cooldown_minutes=60,
            max_retries=max_rate_limit_retries,
        )

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

    async def get_rate_limited_tasks(self, cooldown_minutes: int = 60) -> list[str]:
        """Return task IDs that have been rate-limited for longer than cooldown.

        .. deprecated::
            Use :meth:`get_postponed_tasks` which applies the per-row cooldown.
            This alias ignores *cooldown_minutes* and delegates to get_postponed_tasks().
        """
        warnings.warn(
            "get_rate_limited_tasks() is deprecated and ignores cooldown_minutes; "
            "use get_postponed_tasks() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
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

    async def store_stage_output(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agent: str,
        output: dict[str, Any],
        *,
        stage_id: int | None = None,
        stage_key: str | None = None,
        iteration: int = 1,
        run: int = 1,
    ) -> None:
        """Upsert a stage record with a status derived from the agent output.

        Status rules:
        - subtype="success", is_error=False  → completed
        - subtype="error_max_turns"          → max_turns
        - subtype="success", is_error=True   → rate_limited (rate_limit_event found) or failed
        - anything else                      → failed
        """
        # raw_output is accumulated line-by-line during execution via
        # update_stage_live_output.  The value from _execute_agent is authoritative
        # (full NDJSON from claude_output.raw) and overwrites the streamed version.
        raw_output = output.pop("_raw_output", None)

        # Determine status before serialising so _subtype/_is_error are still present.
        status, error_msg = _resolve_stage_status(output, raw_output)

        # Extract spending metadata before serializing structured_output.
        # Prefer cumulative values (sum across resume iterations) over
        # per-iteration values which only cover the last CLI invocation.
        def _pop_cumulative(key: str, cumulative_key: str) -> Any:
            cumulative = output.pop(cumulative_key, None)
            per_iter = output.pop(key, None)
            return cumulative if cumulative is not None else per_iter

        cost_usd = _pop_cumulative("_cost_usd", "_cumulative_cost_usd")
        tokens_input = _pop_cumulative("_input_tokens", "_cumulative_input_tokens")
        tokens_output = _pop_cumulative("_output_tokens", "_cumulative_output_tokens")
        cache_read = _pop_cumulative("_cache_read_tokens", "_cumulative_cache_read_tokens")
        cache_write = _pop_cumulative("_cache_write_tokens", "_cumulative_cache_write_tokens")
        structured_json = json.dumps(output)
        # Extract model from raw_output NDJSON for the model column
        model = None
        if raw_output:
            try:
                spending_summary = parse_ndjson_spending(raw_output)
                model = spending_summary.model
            except Exception:
                log.warning("Failed to extract model from raw_output for stage", exc_info=True)

        spending_params = {
            "cost_usd": cost_usd,
            "tokens_in": tokens_input,
            "tokens_out": tokens_output,
            "cache_read": cache_read,
            "cache_write": cache_write,
            "model": model,
        }
        if stage_id is not None:
            await self._db.execute(
                """
                UPDATE stages
                SET agent = %(agent)s, status = %(status)s,
                    structured_output = %(output)s::jsonb,
                    raw_output = %(raw)s,
                    live_output = NULL,
                    error_message = %(error_msg)s,
                    cost_usd = %(cost_usd)s,
                    tokens_input = %(tokens_in)s,
                    tokens_output = %(tokens_out)s,
                    cache_read_tokens = %(cache_read)s,
                    cache_write_tokens = %(cache_write)s,
                    model = %(model)s,
                    started_at = COALESCE(stages.started_at, NOW()),
                    completed_at = NOW()
                WHERE id = %(id)s
                """,
                {
                    "id": stage_id,
                    "agent": agent,
                    "status": status,
                    "error_msg": error_msg,
                    "output": structured_json,
                    "raw": raw_output,
                    **spending_params,
                },
            )
        elif stage_key:
            await self._db.execute(
                """
                UPDATE stages
                SET agent = %(agent)s, status = %(status)s,
                    structured_output = %(output)s::jsonb,
                    raw_output = %(raw)s,
                    live_output = NULL,
                    error_message = %(error_msg)s,
                    cost_usd = %(cost_usd)s,
                    tokens_input = %(tokens_in)s,
                    tokens_output = %(tokens_out)s,
                    cache_read_tokens = %(cache_read)s,
                    cache_write_tokens = %(cache_write)s,
                    model = %(model)s,
                    started_at = COALESCE(stages.started_at, NOW()),
                    completed_at = NOW()
                WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                      AND iteration = %(iteration)s AND run = %(run)s
                """,
                {
                    "task_id": task_id,
                    "stage_key": stage_key,
                    "iteration": iteration,
                    "run": run,
                    "agent": agent,
                    "status": status,
                    "error_msg": error_msg,
                    "output": structured_json,
                    "raw": raw_output,
                    **spending_params,
                },
            )
        else:
            # Legacy path: use (task_id, stage_number)
            await self._db.execute(
                """
                INSERT INTO stages (task_id, stage_number, category, agent, status,
                                   structured_output, raw_output, error_message,
                                   cost_usd, tokens_input, tokens_output,
                                   cache_read_tokens, cache_write_tokens,
                                   model, started_at, completed_at)
                VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s, %(status)s,
                        %(output)s::jsonb, %(raw)s, %(error_msg)s,
                        %(cost_usd)s, %(tokens_in)s, %(tokens_out)s,
                        %(cache_read)s, %(cache_write)s,
                        %(model)s, NOW(), NOW())
                ON CONFLICT (task_id, stage_number) DO UPDATE
                SET agent = %(agent)s, status = %(status)s,
                    structured_output = %(output)s::jsonb,
                    raw_output = %(raw)s,
                    error_message = %(error_msg)s,
                    cost_usd = %(cost_usd)s,
                    tokens_input = %(tokens_in)s,
                    tokens_output = %(tokens_out)s,
                    cache_read_tokens = %(cache_read)s,
                    cache_write_tokens = %(cache_write)s,
                    model = %(model)s,
                    started_at = COALESCE(stages.started_at, NOW()),
                    completed_at = NOW()
                """,
                {
                    "task_id": task_id,
                    "stage": stage_num,
                    "category": category,
                    "agent": agent,
                    "status": status,
                    "error_msg": error_msg,
                    "output": structured_json,
                    "raw": raw_output,
                    **spending_params,
                },
            )

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

    async def get_task_context(self, task_id: str) -> dict[str, Any] | None:
        """Call the database-side get_task_context function."""
        result = await self._db.fetch_val(
            "SELECT get_task_context(%(id)s)",
            {"id": task_id},
        )
        if result is None:
            return None
        if isinstance(result, str):
            parsed: dict[str, Any] = json.loads(result)
            return parsed
        return dict(result)

    async def update_checkpoint(
        self, task_id: str, stage_id: int, data: dict[str, Any] | None = None
    ) -> None:
        """Update the task's last_completed_stage and optional checkpoint_data."""
        if data is not None:
            await self._db.execute(
                """
                UPDATE tasks
                SET last_completed_stage = %(stage_id)s,
                    checkpoint_data = %(data)s::jsonb,
                    updated_at = NOW()
                WHERE id = %(id)s
                """,
                {"id": task_id, "stage_id": stage_id, "data": json.dumps(data)},
            )
        else:
            await self._db.execute(
                """
                UPDATE tasks
                SET last_completed_stage = %(stage_id)s, updated_at = NOW()
                WHERE id = %(id)s
                """,
                {"id": task_id, "stage_id": stage_id},
            )

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

    # --- Stage Management ---

    async def create_system_stage(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agent: str,
        *,
        stage_key: str,
        iteration: int = 1,
        run: int = 1,
    ) -> int | None:
        """Insert a stage row for a system agent (planner, condition-evaluator).

        Unlike planned pipeline stages (created by create_planned_pending_stages),
        system agent stages are not pre-created.  This method INSERTs the row so
        that subsequent record_stage_executing / store_stage_output UPDATEs find it.

        Returns the ``stages.id`` of the new row, or ``None`` when the row
        already exists (ON CONFLICT DO NOTHING).
        """
        return await self._db.fetch_val(
            """
            INSERT INTO stages
                (task_id, stage_number, category, agent, status,
                 stage_key, iteration, run)
            VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s,
                    'pending', %(stage_key)s, %(iteration)s, %(run)s)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            {
                "task_id": task_id,
                "stage": stage_num,
                "category": category,
                "agent": agent,
                "stage_key": stage_key,
                "iteration": iteration,
                "run": run,
            },
        )

    async def record_stage_executing(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agent: str,
        *,
        stage_id: int | None = None,
        stage_key: str | None = None,
        iteration: int = 1,
        run: int = 1,
        input_context: dict[str, Any] | None = None,
        execution_order: int | None = None,
    ) -> None:
        """Record that a stage is now executing."""
        if stage_id is not None:
            await self._db.execute(
                """
                UPDATE stages
                SET agent = %(agent)s, status = 'executing', started_at = NOW(),
                    input = %(input)s::jsonb, live_output = NULL, raw_output = NULL,
                    cost_usd = 0, tokens_input = 0, tokens_output = 0,
                    cache_read_tokens = 0, cache_write_tokens = 0,
                    execution_order = %(eo)s
                WHERE id = %(id)s
                """,
                {
                    "id": stage_id,
                    "agent": agent,
                    "input": json.dumps(input_context) if input_context else None,
                    "eo": execution_order,
                },
            )
        elif stage_key:
            # New path: update existing row created by create_planned_pending_stages
            await self._db.execute(
                """
                UPDATE stages
                SET agent = %(agent)s, status = 'executing', started_at = NOW(),
                    input = %(input)s::jsonb, live_output = NULL, raw_output = NULL,
                    cost_usd = 0, tokens_input = 0, tokens_output = 0,
                    cache_read_tokens = 0, cache_write_tokens = 0,
                    execution_order = %(eo)s
                WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                      AND iteration = %(iteration)s AND run = %(run)s
                """,
                {
                    "task_id": task_id,
                    "stage_key": stage_key,
                    "iteration": iteration,
                    "run": run,
                    "agent": agent,
                    "input": json.dumps(input_context) if input_context else None,
                    "eo": execution_order,
                },
            )
        else:
            # Legacy path
            await self._db.execute(
                """
                INSERT INTO stages (task_id, stage_number, category, agent, status, started_at,
                                   cost_usd, tokens_input, tokens_output,
                                   cache_read_tokens, cache_write_tokens,
                                   execution_order)
                VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s, 'executing', NOW(),
                        0, 0, 0, 0, 0, %(eo)s)
                ON CONFLICT (task_id, stage_number) DO UPDATE
                SET agent = %(agent)s, status = 'executing', started_at = NOW(),
                    live_output = NULL, raw_output = NULL,
                    cost_usd = 0, tokens_input = 0, tokens_output = 0,
                    cache_read_tokens = 0, cache_write_tokens = 0,
                    execution_order = %(eo)s
                """,
                {
                    "task_id": task_id,
                    "stage": stage_num,
                    "category": category,
                    "agent": agent,
                    "eo": execution_order,
                },
            )

    async def record_stage_failed(
        self,
        task_id: str,
        stage_num: int,
        error_message: str,
        *,
        stage_id: int | None = None,
        stage_key: str | None = None,
        iteration: int = 1,
        run: int = 1,
        session_id: str | None = None,
    ) -> None:
        """Record that a stage has failed."""
        if stage_id is not None:
            await self._db.execute(
                """
                UPDATE stages
                SET status = 'failed', completed_at = NOW(),
                    error_message = %(error)s, session_id = %(session_id)s
                WHERE id = %(id)s
                """,
                {
                    "id": stage_id,
                    "error": error_message,
                    "session_id": session_id,
                },
            )
        elif stage_key:
            await self._db.execute(
                """
                UPDATE stages
                SET status = 'failed', completed_at = NOW(),
                    error_message = %(error)s, session_id = %(session_id)s
                WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                      AND iteration = %(iteration)s AND run = %(run)s
                """,
                {
                    "task_id": task_id,
                    "stage_key": stage_key,
                    "iteration": iteration,
                    "run": run,
                    "error": error_message,
                    "session_id": session_id,
                },
            )
        else:
            await self._db.execute(
                """
                UPDATE stages
                SET status = 'failed', completed_at = NOW(),
                    error_message = %(error)s, session_id = %(session_id)s
                WHERE task_id = %(task_id)s AND stage_number = %(stage)s
                """,
                {
                    "task_id": task_id,
                    "stage": stage_num,
                    "error": error_message,
                    "session_id": session_id,
                },
            )

    async def record_stage_skipped(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        *,
        stage_id: int | None = None,
        stage_key: str | None = None,
        execution_order: int | None = None,
    ) -> None:
        """Record that a stage was skipped.

        Only updates stages that are not already in a terminal state
        (completed, failed) to avoid overwriting results from agents
        that already ran successfully or recorded an error.
        """
        if stage_id is not None:
            await self._db.execute(
                """
                UPDATE stages
                SET status = 'skipped', completed_at = NOW(),
                    execution_order = %(eo)s
                WHERE id = %(id)s
                      AND status NOT IN ('completed', 'failed')
                """,
                {"id": stage_id, "eo": execution_order},
            )
        elif stage_key:
            await self._db.execute(
                """
                UPDATE stages
                SET status = 'skipped', completed_at = NOW(),
                    execution_order = %(eo)s
                WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                      AND status NOT IN ('completed', 'failed')
                """,
                {"task_id": task_id, "stage_key": stage_key, "eo": execution_order},
            )
        else:
            await self._db.execute(
                """
                INSERT INTO stages (task_id, stage_number, category, status,
                                   started_at, completed_at, execution_order)
                VALUES (%(task_id)s, %(stage)s, %(category)s, 'skipped',
                        NOW(), NOW(), %(eo)s)
                ON CONFLICT (task_id, stage_number) DO UPDATE
                SET status = 'skipped', completed_at = NOW(),
                    execution_order = %(eo)s
                WHERE stages.status NOT IN ('completed', 'failed')
                """,
                {"task_id": task_id, "stage": stage_num, "category": category,
                 "eo": execution_order},
            )

    async def create_pending_stages(
        self, task_id: str, stages: list[dict[str, Any]]
    ) -> None:
        """Create all stages for a pipeline as pending."""
        for i, stage in enumerate(stages):
            await self._db.execute(
                """
                INSERT INTO stages (task_id, stage_number, category, status)
                VALUES (%(task_id)s, %(stage)s, %(category)s, 'pending')
                ON CONFLICT (task_id, stage_number) DO NOTHING
                """,
                {"task_id": task_id, "stage": i, "category": stage["category"]},
            )

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

    async def create_planned_pending_stages(
        self, task_id: str, planned_stages: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Create stage rows from planner output.

        Each planned stage entry has: category, agents[], parallel.
        Creates one row per agent per category at iteration 1.

        Returns a mapping of ``stage_key -> stages.id`` for every
        successfully inserted row.  Rows that already exist (ON CONFLICT)
        are omitted from the mapping.
        """
        stage_ids: dict[str, int] = {}
        for stage_num, plan in enumerate(planned_stages):
            category = plan["category"]
            raw_agents = plan.get("agents", [])
            agents = [
                a.get("name") or a.get("agent_name") or a
                if isinstance(a, dict)
                else a
                for a in raw_agents
            ]
            for agent_name in agents:
                stage_key = f"{stage_num}:{category}:{agent_name}"
                row_id = await self._db.fetch_val(
                    """
                    INSERT INTO stages
                        (task_id, stage_number, category, agent, status,
                         stage_key, iteration)
                    VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s,
                            'pending', %(stage_key)s, 1)
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """,
                    {
                        "task_id": task_id,
                        "stage": stage_num,
                        "category": category,
                        "agent": agent_name,
                        "stage_key": stage_key,
                    },
                )
                if row_id is not None:
                    stage_ids[stage_key] = row_id
        return stage_ids

    async def create_iteration_stage(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agent: str,
        iteration: int,
    ) -> tuple[str, int | None]:
        """Create a new stage row for a condition-driven revisit.

        Returns ``(stage_key, stages.id)``.  ``stages.id`` is ``None``
        when the row already exists (ON CONFLICT DO NOTHING).
        """
        stage_key = f"{stage_num}:{category}:{agent}"
        row_id = await self._db.fetch_val(
            """
            INSERT INTO stages
                (task_id, stage_number, category, agent, status,
                 stage_key, iteration)
            VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s,
                    'pending', %(stage_key)s, %(iteration)s)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            {
                "task_id": task_id,
                "stage": stage_num,
                "category": category,
                "agent": agent,
                "stage_key": stage_key,
                "iteration": iteration,
            },
        )
        return stage_key, row_id

    async def update_stage_live_output(
        self,
        task_id: str,
        stage_key: str,
        iteration: int,
        run: int,
        live_output: str,
        *,
        stage_id: int | None = None,
    ) -> None:
        """Append an NDJSON line to raw_output and update live_output with the latest line.

        Also incrementally updates spending columns when the line contains
        assistant-message token usage.

        Deduplication: the Claude CLI streaming protocol may emit the same
        ``message.id`` multiple times with cumulative (not delta) token counts.
        This method uses the ``msg_spending_state`` JSONB column to track
        per-message-id max values. The actual delta added to spending columns
        is ``new_value - previous_max`` for each token field, ensuring that
        duplicate message IDs don't cause overcounting.
        """
        from .spending import get_pricing

        # Parse spending from this NDJSON line
        delta_input = 0
        delta_output = 0
        delta_cache_read = 0
        delta_cache_write = 0
        delta_cost = 0.0
        msg_id: str | None = None
        raw_input = 0
        raw_output_tokens = 0
        raw_cache_read = 0
        raw_cache_write = 0
        model = ""
        try:
            msg = json.loads(live_output)
            if isinstance(msg, dict) and msg.get("type") == "assistant":
                message = msg.get("message", {})
                usage = message.get("usage") if isinstance(message, dict) else None
                if isinstance(usage, dict):
                    raw_input = usage.get("input_tokens", 0)
                    raw_output_tokens = usage.get("output_tokens", 0)
                    raw_cache_read = usage.get("cache_read_input_tokens", 0)
                    raw_cache_write = usage.get("cache_creation_input_tokens", 0)
                    model = message.get("model", "") if isinstance(message, dict) else ""
                    msg_id = message.get("id") if isinstance(message, dict) else None
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

        has_usage = raw_input or raw_output_tokens or raw_cache_read or raw_cache_write

        if has_usage and msg_id:
            # Deduplication path: compute delta as (new_max - old_max) using
            # msg_spending_state JSONB column atomically in SQL.
            # The JSONB column stores: {"msg_id": {"i": N, "o": N, "cr": N, "cw": N}, ...}
            pricing = get_pricing(model)
            spending_sql = """,
                    msg_spending_state = (
                        SELECT jsonb_set(
                            COALESCE(stages.msg_spending_state, '{}'::jsonb),
                            ARRAY[%(msg_id)s],
                            jsonb_build_object(
                                'i', GREATEST(COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'i')::int, 0), %(raw_input)s),
                                'o', GREATEST(COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'o')::int, 0), %(raw_output)s),
                                'cr', GREATEST(COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'cr')::int, 0), %(raw_cache_read)s),
                                'cw', GREATEST(COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'cw')::int, 0), %(raw_cache_write)s)
                            )
                        )
                    ),
                    tokens_input = COALESCE(tokens_input, 0) + GREATEST(%(raw_input)s - COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'i')::int, 0), 0),
                    tokens_output = COALESCE(tokens_output, 0) + GREATEST(%(raw_output)s - COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'o')::int, 0), 0),
                    cache_read_tokens = COALESCE(cache_read_tokens, 0) + GREATEST(%(raw_cache_read)s - COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'cr')::int, 0), 0),
                    cache_write_tokens = COALESCE(cache_write_tokens, 0) + GREATEST(%(raw_cache_write)s - COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'cw')::int, 0), 0),
                    cost_usd = COALESCE(cost_usd, 0) + (
                        GREATEST(%(raw_input)s - COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'i')::int, 0), 0) * %(price_input)s
                        + GREATEST(%(raw_output)s - COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'o')::int, 0), 0) * %(price_output)s
                        + GREATEST(%(raw_cache_read)s - COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'cr')::int, 0), 0) * %(price_cache_read)s
                        + GREATEST(%(raw_cache_write)s - COALESCE((COALESCE(stages.msg_spending_state, '{}'::jsonb) -> %(msg_id)s ->> 'cw')::int, 0), 0) * %(price_cache_write)s
                    )"""
            spending_params = {
                "msg_id": msg_id,
                "raw_input": raw_input,
                "raw_output": raw_output_tokens,
                "raw_cache_read": raw_cache_read,
                "raw_cache_write": raw_cache_write,
                "price_input": pricing["input"] / 1_000_000,
                "price_output": pricing["output"] / 1_000_000,
                "price_cache_read": pricing["cache_read"] / 1_000_000,
                "price_cache_write": pricing["cache_write"] / 1_000_000,
            }
        elif has_usage:
            # No message ID — treat as unique, add full values (no dedup needed)
            pricing = get_pricing(model)
            delta_input = raw_input
            delta_output = raw_output_tokens
            delta_cache_read = raw_cache_read
            delta_cache_write = raw_cache_write
            delta_cost = (
                delta_input * pricing["input"] / 1_000_000
                + delta_output * pricing["output"] / 1_000_000
                + delta_cache_read * pricing["cache_read"] / 1_000_000
                + delta_cache_write * pricing["cache_write"] / 1_000_000
            )
            spending_sql = """,
                    tokens_input = COALESCE(tokens_input, 0) + %(delta_input)s,
                    tokens_output = COALESCE(tokens_output, 0) + %(delta_output)s,
                    cache_read_tokens = COALESCE(cache_read_tokens, 0) + %(delta_cache_read)s,
                    cache_write_tokens = COALESCE(cache_write_tokens, 0) + %(delta_cache_write)s,
                    cost_usd = COALESCE(cost_usd, 0) + %(delta_cost)s"""
            spending_params = {
                "delta_input": delta_input,
                "delta_output": delta_output,
                "delta_cache_read": delta_cache_read,
                "delta_cache_write": delta_cache_write,
                "delta_cost": delta_cost,
            }
        else:
            # No usage data in this line — no spending update
            spending_sql = ""
            spending_params = {}

        if stage_id is not None:
            await self._db.execute(
                f"""
                UPDATE stages
                SET live_output = %(line)s,
                    raw_output = COALESCE(raw_output || E'\\n', '') || %(line)s{spending_sql}
                WHERE id = %(id)s
                """,
                {"id": stage_id, "line": live_output, **spending_params},
            )
        else:
            await self._db.execute(
                f"""
                UPDATE stages
                SET live_output = %(line)s,
                    raw_output = COALESCE(raw_output || E'\\n', '') || %(line)s{spending_sql}
                WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                      AND iteration = %(iteration)s AND run = %(run)s
                """,
                {
                    "task_id": task_id,
                    "stage_key": stage_key,
                    "iteration": iteration,
                    "run": run,
                    "line": live_output,
                    **spending_params,
                },
            )

    async def get_latest_stage_run(
        self,
        task_id: str,
        stage_key: str,
        iteration: int = 1,
    ) -> dict[str, Any] | None:
        """Return the latest run's id, status, run number, and session_id for a stage."""
        return await self._db.fetch_one(
            """
            SELECT id, status, run, error_message, session_id
            FROM stages
            WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                  AND iteration = %(iteration)s
            ORDER BY run DESC
            LIMIT 1
            """,
            {"task_id": task_id, "stage_key": stage_key, "iteration": iteration},
        )

    async def create_rerun_stage(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agent: str,
        stage_key: str,
        iteration: int,
        run: int,
    ) -> int:
        """Insert a new stage row for a rerun.  Returns ``stages.id``."""
        return await self._db.fetch_val(
            """
            INSERT INTO stages
                (task_id, stage_number, category, agent, status,
                 stage_key, iteration, run)
            VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s,
                    'pending', %(stage_key)s, %(iteration)s, %(run)s)
            RETURNING id
            """,
            {
                "task_id": task_id,
                "stage": stage_num,
                "category": category,
                "agent": agent,
                "stage_key": stage_key,
                "iteration": iteration,
                "run": run,
            },
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

    async def get_max_iteration(self, task_id: str, stage_key: str) -> int:
        """Get the current max iteration count for a stage_key."""
        result = await self._db.fetch_val(
            """
            SELECT COALESCE(MAX(iteration), 0)
            FROM stages
            WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
            """,
            {"task_id": task_id, "stage_key": stage_key},
        )
        return int(result) if result else 0

    async def get_stage_number_for_id(self, stage_id: int) -> int | None:
        """Resolve a stages.id to its stage_number."""
        result = await self._db.fetch_val(
            "SELECT stage_number FROM stages WHERE id = %(id)s",
            {"id": stage_id},
        )
        return int(result) if result is not None else None

    async def get_max_execution_order(self, task_id: str) -> int:
        """Return the current maximum execution_order for a task, or 0 if none set."""
        result = await self._db.fetch_val(
            """
            SELECT COALESCE(MAX(execution_order), 0)
            FROM stages
            WHERE task_id = %(task_id)s AND execution_order IS NOT NULL
            """,
            {"task_id": task_id},
        )
        return int(result) if result else 0

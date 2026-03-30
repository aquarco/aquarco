"""Task queue operations - all PostgreSQL task and poll state queries."""

from __future__ import annotations

import json
from typing import Any

from .database import Database
from .logging import get_logger
from .models import Task, TaskPhase, TaskStatus, ValidationItem

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
            RETURNING id, title, pipeline, repository, source, source_ref,
                      status, phase, priority, initial_context, planned_stages,
                      created_at, updated_at,
                      started_at, completed_at, assigned_agent, current_stage,
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

        if status == TaskStatus.EXECUTING:
            query = """
                UPDATE tasks
                SET status = %(status)s, updated_at = NOW(),
                    started_at = NOW(), error_message = NULL
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
            SELECT id, title, status, phase, priority, source, source_ref,
                   pipeline, repository, initial_context, planned_stages,
                   created_at, updated_at,
                   started_at, completed_at, assigned_agent, current_stage,
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

    async def rate_limit_task(
        self, task_id: str, error_message: str, *, max_rate_limit_retries: int = 24,
    ) -> None:
        """Mark task as rate-limited, or fail it if retries exhausted.

        Each rate-limit hit increments ``rate_limit_count``.  After
        *max_rate_limit_retries* (default 24 ≈ 1 day at 1 h cooldown) the
        task is marked ``failed`` instead.
        """
        await self._db.execute(
            """
            UPDATE tasks
            SET rate_limit_count = rate_limit_count + 1,
                error_message = %(error)s,
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
            {"id": task_id, "error": error_message, "max": max_rate_limit_retries},
        )
        row = await self._db.fetch_one(
            "SELECT status, rate_limit_count FROM tasks WHERE id = %(id)s",
            {"id": task_id},
        )
        if row and row["status"] == "failed":
            log.warning(
                "task_rate_limit_exhausted",
                task_id=task_id,
                rate_limit_count=row["rate_limit_count"],
            )
        else:
            log.warning(
                "task_rate_limited",
                task_id=task_id,
                rate_limit_count=row["rate_limit_count"] if row else 0,
            )

    async def get_rate_limited_tasks(self, cooldown_minutes: int = 60) -> list[str]:
        """Return task IDs that have been rate-limited for longer than cooldown."""
        rows = await self._db.fetch_all(
            """
            SELECT id FROM tasks
            WHERE status = 'rate_limited'
              AND updated_at < NOW() - make_interval(mins := %(cooldown)s)
            ORDER BY updated_at ASC
            """,
            {"cooldown": cooldown_minutes},
        )
        return [r["id"] for r in rows]

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
        stage_key: str | None = None,
        iteration: int = 1,
        run: int = 1,
        validation_items_in: list[dict[str, Any]] | None = None,
        validation_items_out: list[dict[str, Any]] | None = None,
    ) -> None:
        """Upsert a completed stage record and advance the task's current_stage."""
        # raw_output is no longer set by _execute_agent (replaced by live_output
        # streaming). Pop to keep the interface consistent; value will be NULL in DB.
        raw_output = output.pop("_raw_output", None)
        structured_json = json.dumps(output)
        if stage_key:
            # New path: use stage_key + iteration + run for upsert
            await self._db.execute(
                """
                UPDATE stages
                SET agent = %(agent)s, status = 'completed',
                    structured_output = %(output)s::jsonb,
                    raw_output = %(raw)s,
                    validation_items_in = %(vi_in)s::jsonb,
                    validation_items_out = %(vi_out)s::jsonb,
                    live_output = NULL,
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
                    "output": structured_json,
                    "raw": raw_output,
                    "vi_in": json.dumps(validation_items_in) if validation_items_in else None,
                    "vi_out": json.dumps(validation_items_out) if validation_items_out else None,
                },
            )
        else:
            # Legacy path: use (task_id, stage_number)
            await self._db.execute(
                """
                INSERT INTO stages (task_id, stage_number, category, agent, status,
                                   structured_output, raw_output, started_at, completed_at)
                VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s, 'completed',
                        %(output)s::jsonb, %(raw)s, NOW(), NOW())
                ON CONFLICT (task_id, stage_number) DO UPDATE
                SET agent = %(agent)s, status = 'completed',
                    structured_output = %(output)s::jsonb,
                    raw_output = %(raw)s,
                    started_at = COALESCE(stages.started_at, NOW()),
                    completed_at = NOW()
                """,
                {
                    "task_id": task_id,
                    "stage": stage_num,
                    "category": category,
                    "agent": agent,
                    "output": structured_json,
                    "raw": raw_output,
                },
            )
        await self._db.execute(
            """
            UPDATE tasks SET current_stage = %(next_stage)s, updated_at = NOW()
            WHERE id = %(task_id)s
            """,
            {"task_id": task_id, "next_stage": stage_num + 1},
        )

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

    async def assign_agent(self, task_id: str, agent_name: str) -> None:
        """Assign an agent to a task and set status to executing."""
        await self._db.execute(
            """
            UPDATE tasks
            SET assigned_agent = %(agent)s, status = 'executing',
                started_at = COALESCE(started_at, NOW()), updated_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": task_id, "agent": agent_name},
        )
        log.info("agent_assigned", task_id=task_id, agent=agent_name)

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
    ) -> None:
        """Insert a stage row for a system agent (planner, condition-evaluator).

        Unlike planned pipeline stages (created by create_planned_pending_stages),
        system agent stages are not pre-created.  This method INSERTs the row so
        that subsequent record_stage_executing / store_stage_output UPDATEs find it.
        """
        await self._db.execute(
            """
            INSERT INTO stages
                (task_id, stage_number, category, agent, status,
                 stage_key, iteration, run)
            VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s,
                    'pending', %(stage_key)s, %(iteration)s, %(run)s)
            ON CONFLICT DO NOTHING
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
        stage_key: str | None = None,
        iteration: int = 1,
        run: int = 1,
        input_context: dict[str, Any] | None = None,
    ) -> None:
        """Record that a stage is now executing."""
        if stage_key:
            # New path: update existing row created by create_planned_pending_stages
            await self._db.execute(
                """
                UPDATE stages
                SET agent = %(agent)s, status = 'executing', started_at = NOW(),
                    input = %(input)s::jsonb
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
                },
            )
        else:
            # Legacy path
            await self._db.execute(
                """
                INSERT INTO stages (task_id, stage_number, category, agent, status, started_at)
                VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s, 'executing', NOW())
                ON CONFLICT (task_id, stage_number) DO UPDATE
                SET agent = %(agent)s, status = 'executing', started_at = NOW()
                """,
                {
                    "task_id": task_id,
                    "stage": stage_num,
                    "category": category,
                    "agent": agent,
                },
            )

    async def record_stage_failed(
        self,
        task_id: str,
        stage_num: int,
        error_message: str,
        *,
        stage_key: str | None = None,
        iteration: int = 1,
        run: int = 1,
    ) -> None:
        """Record that a stage has failed."""
        if stage_key:
            await self._db.execute(
                """
                UPDATE stages
                SET status = 'failed', completed_at = NOW(), error_message = %(error)s
                WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                      AND iteration = %(iteration)s AND run = %(run)s
                """,
                {
                    "task_id": task_id,
                    "stage_key": stage_key,
                    "iteration": iteration,
                    "run": run,
                    "error": error_message,
                },
            )
        else:
            await self._db.execute(
                """
                UPDATE stages
                SET status = 'failed', completed_at = NOW(), error_message = %(error)s
                WHERE task_id = %(task_id)s AND stage_number = %(stage)s
                """,
                {"task_id": task_id, "stage": stage_num, "error": error_message},
            )

    async def record_stage_skipped(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        *,
        stage_key: str | None = None,
    ) -> None:
        """Record that a stage was skipped."""
        if stage_key:
            await self._db.execute(
                """
                UPDATE stages
                SET status = 'skipped', completed_at = NOW()
                WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                """,
                {"task_id": task_id, "stage_key": stage_key},
            )
        else:
            await self._db.execute(
                """
                INSERT INTO stages (task_id, stage_number, category, status, started_at, completed_at)
                VALUES (%(task_id)s, %(stage)s, %(category)s, 'skipped', NOW(), NOW())
                ON CONFLICT (task_id, stage_number) DO UPDATE
                SET status = 'skipped', completed_at = NOW()
                """,
                {"task_id": task_id, "stage": stage_num, "category": category},
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

    async def update_task_phase(self, task_id: str, phase: TaskPhase) -> None:
        """Update the pipeline phase for a task."""
        await self._db.execute(
            """
            UPDATE tasks SET phase = %(phase)s, updated_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": task_id, "phase": phase.value},
        )
        log.info("task_phase_updated", task_id=task_id, phase=phase.value)

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
    ) -> None:
        """Create stage rows from planner output.

        Each planned stage entry has: category, agents[], parallel.
        Creates one row per agent per category at iteration 1.
        """
        for stage_num, plan in enumerate(planned_stages):
            category = plan["category"]
            raw_agents = plan.get("agents", [])
            agents = [
                a["name"] if isinstance(a, dict) else a for a in raw_agents
            ]
            for agent_name in agents:
                stage_key = f"{stage_num}:{category}:{agent_name}"
                await self._db.execute(
                    """
                    INSERT INTO stages
                        (task_id, stage_number, category, agent, status,
                         stage_key, iteration)
                    VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s,
                            'pending', %(stage_key)s, 1)
                    ON CONFLICT DO NOTHING
                    """,
                    {
                        "task_id": task_id,
                        "stage": stage_num,
                        "category": category,
                        "agent": agent_name,
                        "stage_key": stage_key,
                    },
                )

    async def create_iteration_stage(
        self,
        task_id: str,
        stage_num: int,
        category: str,
        agent: str,
        iteration: int,
    ) -> str:
        """Create a new stage row for an iteration re-run. Returns stage_key."""
        stage_key = f"{stage_num}:{category}:{agent}"
        await self._db.execute(
            """
            INSERT INTO stages
                (task_id, stage_number, category, agent, status,
                 stage_key, iteration)
            VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s,
                    'pending', %(stage_key)s, %(iteration)s)
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
        return stage_key

    # --- Validation Items ---

    async def add_validation_item(
        self,
        task_id: str,
        stage_key: str | None,
        category: str,
        description: str,
    ) -> int:
        """Insert a new open validation item. Returns its ID."""
        row = await self._db.fetch_one(
            """
            INSERT INTO validation_items (task_id, stage_key, category, description)
            VALUES (%(task_id)s, %(stage_key)s, %(category)s, %(desc)s)
            RETURNING id
            """,
            {
                "task_id": task_id,
                "stage_key": stage_key,
                "category": category,
                "desc": description,
            },
        )
        vi_id: int = row["id"]
        return vi_id

    async def resolve_validation_item(
        self, item_id: int, resolved_by_stage_key: str
    ) -> None:
        """Mark a validation item as resolved."""
        await self._db.execute(
            """
            UPDATE validation_items
            SET status = 'resolved', resolved_by = %(resolved_by)s, resolved_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": item_id, "resolved_by": resolved_by_stage_key},
        )

    async def get_open_validation_items(
        self, task_id: str, category: str | None = None
    ) -> list[ValidationItem]:
        """Get open validation items for a task, optionally filtered by category."""
        if category:
            rows = await self._db.fetch_all(
                """
                SELECT id, task_id, stage_key, category, description, status,
                       resolved_by, resolved_at, created_at
                FROM validation_items
                WHERE task_id = %(task_id)s AND status = 'open' AND category = %(cat)s
                ORDER BY id
                """,
                {"task_id": task_id, "cat": category},
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT id, task_id, stage_key, category, description, status,
                       resolved_by, resolved_at, created_at
                FROM validation_items
                WHERE task_id = %(task_id)s AND status = 'open'
                ORDER BY id
                """,
                {"task_id": task_id},
            )
        return [ValidationItem.model_validate(r) for r in rows]

    async def update_stage_live_output(
        self,
        task_id: str,
        stage_key: str,
        iteration: int,
        run: int,
        live_output: str,
    ) -> None:
        """Update the live_output column for an executing stage."""
        await self._db.execute(
            """
            UPDATE stages
            SET live_output = %(live_output)s
            WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                  AND iteration = %(iteration)s AND run = %(run)s
            """,
            {
                "task_id": task_id,
                "stage_key": stage_key,
                "iteration": iteration,
                "run": run,
                "live_output": live_output,
            },
        )

    async def get_latest_stage_run(
        self,
        task_id: str,
        stage_key: str,
        iteration: int = 1,
    ) -> dict[str, Any] | None:
        """Return the latest run's status and run number for a stage."""
        return await self._db.fetch_one(
            """
            SELECT status, run, error_message
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
    ) -> None:
        """Insert a new stage row for a rerun."""
        await self._db.execute(
            """
            INSERT INTO stages
                (task_id, stage_number, category, agent, status,
                 stage_key, iteration, run)
            VALUES (%(task_id)s, %(stage)s, %(category)s, %(agent)s,
                    'pending', %(stage_key)s, %(iteration)s, %(run)s)
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
            SELECT id, title, source, source_ref, repository, pipeline, initial_context
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
                 initial_context, parent_task_id)
            VALUES (%(new_id)s, %(title)s, %(source)s, %(source_ref)s,
                    %(repository)s, %(pipeline)s, %(context)s::jsonb,
                    %(parent_id)s)
            """,
            {
                "new_id": new_id,
                "title": original["title"],
                "source": original["source"],
                "source_ref": original["source_ref"],
                "repository": original["repository"],
                "pipeline": original["pipeline"],
                "context": json.dumps(original["initial_context"] or {}),
                "parent_id": task_id,
            },
        )
        log.info("task_rerun_created", task_id=task_id, new_task_id=new_id)
        return new_id

    async def close_task(self, task_id: str) -> None:
        """Mark a task as closed and delete its checkpoint."""
        await self._db.execute(
            """
            UPDATE tasks SET status = 'closed', updated_at = NOW()
            WHERE id = %(id)s
            """,
            {"id": task_id},
        )
        await self._db.execute(
            "DELETE FROM pipeline_checkpoints WHERE task_id = %(id)s",
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

    # --- Pipeline Checkpoints ---

    async def checkpoint_pipeline(
        self, task_id: str, stage_num: int, data: dict[str, Any] | None = None
    ) -> None:
        """Save or update a pipeline execution checkpoint."""
        await self._db.execute(
            """
            INSERT INTO pipeline_checkpoints
                (task_id, last_completed_stage, checkpoint_data, created_at)
            VALUES (%(id)s, %(stage)s, %(data)s::jsonb, NOW())
            ON CONFLICT (task_id) DO UPDATE
            SET last_completed_stage = %(stage)s,
                checkpoint_data = %(data)s::jsonb,
                created_at = NOW()
            """,
            {"id": task_id, "stage": stage_num, "data": json.dumps(data or {})},
        )

    async def get_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """Get a pipeline checkpoint for a task."""
        return await self._db.fetch_one(
            """
            SELECT task_id, last_completed_stage, checkpoint_data, created_at
            FROM pipeline_checkpoints WHERE task_id = %(id)s
            """,
            {"id": task_id},
        )

    async def delete_checkpoint(self, task_id: str) -> None:
        """Delete a pipeline checkpoint."""
        await self._db.execute(
            "DELETE FROM pipeline_checkpoints WHERE task_id = %(id)s",
            {"id": task_id},
        )

"""Stage management operations - all PostgreSQL stage record queries.

Extracted from task_queue.py to follow low-coupling/high-cohesion principles.
The TaskQueue retains task lifecycle and poll-state methods; this module owns
every operation that touches the ``stages`` table.
"""

from __future__ import annotations

import json
from typing import Any

from .database import Database
from .logging import get_logger
from .spending import get_pricing, parse_ndjson_spending

log = get_logger("stage-manager")


# ---------------------------------------------------------------------------
# Stage status resolution (pure function)
# ---------------------------------------------------------------------------


def _resolve_stage_status(
    output: dict[str, Any],
    raw_output: str | None,
) -> tuple[str, str | None]:
    """Determine the correct stage status and error message from agent output.

    Rules (in priority order):
    - subtype="success", is_error=False  -> completed
    - subtype="error_max_turns"          -> max_turns
    - subtype="success", is_error=True   -> scan raw_output for rate_limit_event
                                           -> rate_limited (with resetsAt) or failed
    - anything else                      -> failed
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


# ---------------------------------------------------------------------------
# StageManager class
# ---------------------------------------------------------------------------


class StageManager:
    """Manages pipeline stage records in PostgreSQL."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # --- Stage output storage ---

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
        - subtype="success", is_error=False  -> completed
        - subtype="error_max_turns"          -> max_turns
        - subtype="success", is_error=True   -> rate_limited (rate_limit_event found) or failed
        - anything else                      -> failed
        """
        raw_output = output.pop("_raw_output", None)

        # Determine status before serialising so _subtype/_is_error are still present.
        status, error_msg = _resolve_stage_status(output, raw_output)

        # Extract spending metadata before serializing structured_output.
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

    # --- Task context & checkpoint ---

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

    # --- Stage creation ---

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

    # --- Live output streaming ---

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

    # --- Stage querying ---

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

"""Tests for update_stage_live_output and get_live_stage_spending behaviours.

Covers the commit: "fix: stream NDJSON lines into raw_output for live spending tracking"

Acceptance criteria:
- update_stage_live_output accumulates lines in raw_output (newline-joined)
- live_output holds only the most-recent line after multiple calls
- get_live_stage_spending reads from raw_output, not live_output
- NULL/empty raw_output returns None gracefully
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, call

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def task_queue(mock_db: AsyncMock) -> TaskQueue:
    return TaskQueue(mock_db, max_retries=3)


# ---------------------------------------------------------------------------
# update_stage_live_output – SQL structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_stage_live_output_sql_sets_live_output_to_line(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """live_output column should be set to the single line passed in."""
    line = '{"type":"assistant"}'
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    mock_db.execute.assert_called_once()
    sql, params = mock_db.execute.call_args[0]

    assert "live_output = %(line)s" in sql
    assert params["line"] == line


@pytest.mark.asyncio
async def test_update_stage_live_output_sql_appends_to_raw_output(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """raw_output column should use the COALESCE append pattern."""
    line = '{"type":"assistant"}'
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]

    # The COALESCE append idiom must be present
    assert "COALESCE" in sql
    assert "raw_output" in sql
    assert "%(line)s" in sql
    assert params["line"] == line


@pytest.mark.asyncio
async def test_update_stage_live_output_sql_targets_correct_row(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """WHERE clause must match task_id, stage_key, iteration, and run."""
    await task_queue.update_stage_live_output(
        task_id="task-42",
        stage_key="1:test:test-agent",
        iteration=2,
        run=3,
        live_output="line",
    )

    sql, params = mock_db.execute.call_args[0]

    assert "task_id = %(task_id)s" in sql
    assert "stage_key = %(stage_key)s" in sql
    assert "iteration = %(iteration)s" in sql
    assert "run = %(run)s" in sql

    assert params["task_id"] == "task-42"
    assert params["stage_key"] == "1:test:test-agent"
    assert params["iteration"] == 2
    assert params["run"] == 3


@pytest.mark.asyncio
async def test_update_stage_live_output_multiple_calls_accumulate_raw_output(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Calling update_stage_live_output three times produces three DB calls,
    each passing the respective line.  The COALESCE append logic in SQL means
    raw_output grows with each call; we verify each call carries the correct
    individual line (the DB handles the accumulation).
    """
    lines = [
        '{"type":"system","subtype":"init"}',
        '{"type":"assistant","message":{"usage":{"input_tokens":100,"output_tokens":50}}}',
        '{"type":"result","total_cost_usd":0.001}',
    ]

    for line in lines:
        await task_queue.update_stage_live_output(
            task_id="task-1",
            stage_key="0:review:review-agent",
            iteration=1,
            run=1,
            live_output=line,
        )

    assert mock_db.execute.call_count == 3

    for i, expected_line in enumerate(lines):
        _, params = mock_db.execute.call_args_list[i][0]
        assert params["line"] == expected_line, (
            f"Call {i}: expected line {expected_line!r}, got {params['line']!r}"
        )


@pytest.mark.asyncio
async def test_update_stage_live_output_last_call_sets_live_output_to_latest_line(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """After multiple calls, the last call's 'line' param is what live_output
    will be set to in the DB — confirming live_output holds only the latest line.
    """
    lines = ["first_line", "second_line", "third_line"]

    for line in lines:
        await task_queue.update_stage_live_output(
            task_id="task-1",
            stage_key="0:review:review-agent",
            iteration=1,
            run=1,
            live_output=line,
        )

    # The last call's line param is "third_line" (the most-recent line)
    _, last_params = mock_db.execute.call_args_list[-1][0]
    assert last_params["line"] == "third_line"


# ---------------------------------------------------------------------------
# get_live_stage_spending – reads from raw_output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_live_stage_spending_selects_raw_output_column(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """The SELECT must read raw_output, not live_output."""
    mock_db.fetch_one.return_value = {"raw_output": None}

    await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    sql, params = mock_db.fetch_one.call_args[0]

    assert "raw_output" in sql
    assert "live_output" not in sql
    assert params["id"] == "task-1"
    assert params["sk"] == "0:review:review-agent"


@pytest.mark.asyncio
async def test_get_live_stage_spending_returns_none_when_row_not_found(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Returns None when no executing stage row exists."""
    mock_db.fetch_one.return_value = None

    result = await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    assert result is None


@pytest.mark.asyncio
async def test_get_live_stage_spending_returns_none_when_raw_output_is_null(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Returns None gracefully when raw_output IS NULL in the row."""
    mock_db.fetch_one.return_value = {"raw_output": None}

    result = await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    assert result is None


@pytest.mark.asyncio
async def test_get_live_stage_spending_returns_none_when_raw_output_empty_string(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Returns None gracefully when raw_output is an empty string (falsy)."""
    mock_db.fetch_one.return_value = {"raw_output": ""}

    result = await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    assert result is None


@pytest.mark.asyncio
async def test_get_live_stage_spending_parses_raw_output_ndjson(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """When raw_output contains NDJSON, returns a spending summary dict."""
    ndjson = "\n".join([
        json.dumps({
            "type": "assistant",
            "message": {
                "model": "claude-sonnet-4-5",
                "usage": {
                    "input_tokens": 200,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 100,
                },
            },
        }),
        json.dumps({"type": "result", "total_cost_usd": 0.0025}),
    ])

    mock_db.fetch_one.return_value = {"raw_output": ndjson}

    result = await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    assert result is not None
    assert result["input_tokens"] == 200
    assert result["output_tokens"] == 100
    assert result["estimated_cost_usd"] >= 0
    assert result["turns"] == 1


@pytest.mark.asyncio
async def test_get_live_stage_spending_returns_expected_keys(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Result dict must contain all required spending keys."""
    ndjson = json.dumps({
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-4-5",
            "usage": {
                "input_tokens": 50,
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 5,
                "output_tokens": 20,
            },
        },
    })

    mock_db.fetch_one.return_value = {"raw_output": ndjson}

    result = await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    assert result is not None
    expected_keys = {
        "input_tokens", "cache_write_tokens", "cache_read_tokens",
        "output_tokens", "estimated_cost_usd", "model", "turns",
    }
    assert expected_keys == set(result.keys())


@pytest.mark.asyncio
async def test_get_live_stage_spending_filters_by_executing_status(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """SQL must filter on status = 'executing' so completed stages are excluded."""
    mock_db.fetch_one.return_value = None

    await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    sql, _ = mock_db.fetch_one.call_args[0]
    assert "executing" in sql


@pytest.mark.asyncio
async def test_get_live_stage_spending_multi_turn_accumulation(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """raw_output with multiple assistant turns (unique IDs) sums tokens correctly."""
    turns = [
        {"id": "msg_01", "input_tokens": 100, "output_tokens": 50},
        {"id": "msg_02", "input_tokens": 150, "output_tokens": 60},
        {"id": "msg_03", "input_tokens": 200, "output_tokens": 70},
    ]
    lines = [
        json.dumps({
            "type": "assistant",
            "message": {
                "id": t["id"],
                "model": "claude-sonnet-4-5",
                "usage": {
                    "input_tokens": t["input_tokens"],
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": t["output_tokens"],
                },
            },
        })
        for t in turns
    ]
    ndjson = "\n".join(lines)

    mock_db.fetch_one.return_value = {"raw_output": ndjson}

    result = await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    assert result is not None
    assert result["input_tokens"] == 450   # 100 + 150 + 200
    assert result["output_tokens"] == 180  # 50 + 60 + 70
    assert result["turns"] == 3


# ---------------------------------------------------------------------------
# update_stage_live_output – deduplication by message.id (issue #93)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_stage_live_output_dedup_uses_msg_spending_state(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """When a line contains message.id, the SQL should reference msg_spending_state
    for deduplication instead of naively adding deltas."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 0,
                "output_tokens": 20,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]
    # Should use msg_spending_state and GREATEST for dedup
    assert "msg_spending_state" in sql
    assert "GREATEST" in sql
    assert params["msg_id"] == "msg_01AAA"
    assert params["raw_input"] == 10
    assert params["raw_output"] == 20
    # Verify JSONB literals are valid JSON (not double-braced from f-string confusion)
    assert "'{}'::jsonb" in sql, "JSONB fallback must use valid JSON '{}', not '{{}}'::jsonb"
    assert "'{{}}'::jsonb" not in sql, "Found invalid '{{}}'::jsonb literal in SQL"


@pytest.mark.asyncio
async def test_update_stage_live_output_no_message_id_uses_simple_delta(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """When a line has no message.id, should fall back to simple delta addition."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 0,
                "output_tokens": 20,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]
    # Should use simple delta addition (no msg_spending_state)
    assert "msg_spending_state" not in sql
    assert params["delta_input"] == 10
    assert params["delta_output"] == 20


@pytest.mark.asyncio
async def test_update_stage_live_output_non_assistant_no_spending_update(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Non-assistant lines should not produce spending SQL."""
    line = json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"})
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]
    # Should not contain any spending update SQL
    assert "tokens_input" not in sql
    assert "cost_usd" not in sql


# ---------------------------------------------------------------------------
# Additional dedup edge-case tests (issue #93, test-agent stage)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_stage_live_output_dedup_with_stage_id(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """When stage_id is provided, dedup SQL should still be used and the WHERE
    clause should filter by id instead of task_id/stage_key."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_01BBB",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 50,
                "cache_creation_input_tokens": 500,
                "cache_read_input_tokens": 0,
                "output_tokens": 30,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
        stage_id=42,
    )

    sql, params = mock_db.execute.call_args[0]
    # Should use dedup SQL
    assert "msg_spending_state" in sql
    assert "GREATEST" in sql
    # Should filter by id, not task_id/stage_key
    assert "WHERE id = %(id)s" in sql
    assert params["id"] == 42
    assert params["msg_id"] == "msg_01BBB"


@pytest.mark.asyncio
async def test_update_stage_live_output_zero_usage_with_msg_id_no_spending(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """A message with an ID but all-zero usage should NOT produce spending SQL.
    has_usage is falsy when all token fields are 0."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_zero",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]
    # No spending update — all token fields are 0
    assert "tokens_input" not in sql
    assert "msg_spending_state" not in sql
    assert "cost_usd" not in sql


@pytest.mark.asyncio
async def test_update_stage_live_output_dedup_pricing_params(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Verify that the dedup path includes correct pricing parameters for cost
    computation in the SQL."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_01CCC",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 300,
                "output_tokens": 400,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]
    # Sonnet pricing: input=$3, output=$15, cache_write=$3.75, cache_read=$0.30 per MTok
    assert params["price_input"] == 3 / 1_000_000
    assert params["price_output"] == 15 / 1_000_000
    assert params["price_cache_read"] == 0.30 / 1_000_000
    assert params["price_cache_write"] == 3.75 / 1_000_000
    # SQL must reference price params
    assert "%(price_input)s" in sql
    assert "%(price_output)s" in sql
    assert "%(price_cache_read)s" in sql
    assert "%(price_cache_write)s" in sql


@pytest.mark.asyncio
async def test_update_stage_live_output_no_id_delta_cost_computed_correctly(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """No-ID fallback path should compute delta_cost from token values and pricing."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]
    # delta_cost for 1M input tokens at Sonnet rate ($3/MTok) = $3.00
    assert params["delta_cost"] == 3.0
    assert params["delta_input"] == 1_000_000


@pytest.mark.asyncio
async def test_update_stage_live_output_dedup_sql_jsonb_set_structure(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Verify that the dedup SQL uses jsonb_set and jsonb_build_object for
    atomic state updates."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_01DDD",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 0,
                "output_tokens": 20,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]
    # Verify the SQL uses proper JSONB functions for atomic update
    assert "jsonb_set" in sql
    assert "jsonb_build_object" in sql
    assert "ARRAY[%(msg_id)s]" in sql
    # Verify cost_usd is updated in the dedup path
    assert "cost_usd" in sql


@pytest.mark.asyncio
async def test_update_stage_live_output_result_line_no_spending(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """A result line should not produce spending SQL — only assistant lines do."""
    line = json.dumps({
        "type": "result",
        "subtype": "success",
        "total_cost_usd": 0.25,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, params = mock_db.execute.call_args[0]
    assert "tokens_input" not in sql
    assert "msg_spending_state" not in sql


@pytest.mark.asyncio
async def test_update_stage_live_output_malformed_json_no_spending(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Malformed JSON lines should not crash and should not produce spending SQL."""
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output="not valid json {{{",
    )

    sql, params = mock_db.execute.call_args[0]
    assert "tokens_input" not in sql
    assert "msg_spending_state" not in sql
    # Line still appended to raw_output
    assert "raw_output" in sql
    assert params["line"] == "not valid json {{{"


@pytest.mark.asyncio
async def test_update_stage_live_output_dedup_raw_token_params(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Verify all raw token params are correctly extracted and passed for dedup."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_01EEE",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 11,
                "cache_creation_input_tokens": 222,
                "cache_read_input_tokens": 333,
                "output_tokens": 44,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    _, params = mock_db.execute.call_args[0]
    assert params["raw_input"] == 11
    assert params["raw_output"] == 44
    assert params["raw_cache_read"] == 333
    assert params["raw_cache_write"] == 222


# ---------------------------------------------------------------------------
# Additional dedup tests (test-agent stage 4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_stage_live_output_dedup_opus_pricing(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Dedup path with an Opus model should use Opus pricing in params."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_opus",
            "model": "claude-opus-4-6",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 200,
                "cache_read_input_tokens": 300,
                "output_tokens": 400,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    _, params = mock_db.execute.call_args[0]
    # Opus 4.5/4.6: input=$5, output=$25, cache_write=$6.25, cache_read=$0.50 per MTok
    assert params["price_input"] == 5 / 1_000_000
    assert params["price_output"] == 25 / 1_000_000
    assert params["price_cache_write"] == 6.25 / 1_000_000
    assert params["price_cache_read"] == 0.50 / 1_000_000


@pytest.mark.asyncio
async def test_update_stage_live_output_no_id_haiku_pricing(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """No-ID fallback path with Haiku model should compute delta_cost with Haiku pricing."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "model": "claude-haiku-4-5",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    _, params = mock_db.execute.call_args[0]
    # Haiku 4.5: $1/MTok input
    assert params["delta_cost"] == 1.0


@pytest.mark.asyncio
async def test_update_stage_live_output_dedup_sql_has_all_four_token_fields(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """The dedup SQL must update all four token columns and cost_usd."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_complete",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 30,
                "output_tokens": 40,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, _ = mock_db.execute.call_args[0]
    # All four token columns must be updated
    assert "tokens_input" in sql
    assert "tokens_output" in sql
    assert "cache_read_tokens" in sql
    assert "cache_write_tokens" in sql
    assert "cost_usd" in sql
    assert "msg_spending_state" in sql


@pytest.mark.asyncio
async def test_update_stage_live_output_dedup_sql_no_double_brace_anywhere(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Regression guard: the spending_sql must not contain any '{{' or '}}' sequences
    that would produce invalid JSONB literals in PostgreSQL."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_brace_check",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 1,
                "cache_creation_input_tokens": 1,
                "cache_read_input_tokens": 1,
                "output_tokens": 1,
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    sql, _ = mock_db.execute.call_args[0]
    # Count all occurrences of '{}'::jsonb — should be present
    assert sql.count("'{}'::jsonb") >= 1, "Expected valid '{}'::jsonb literals"
    # Must not contain '{{}}' anywhere (the fixed bug)
    assert "{{" not in sql, "SQL contains '{{' — possible f-string escaping regression"
    assert "}}" not in sql, "SQL contains '}}' — possible f-string escaping regression"


@pytest.mark.asyncio
async def test_update_stage_live_output_usage_missing_some_fields(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Message with usage dict missing some optional token fields should default to 0."""
    line = json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_sparse",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 50,
                "output_tokens": 25,
                # cache fields omitted
            },
        },
    })
    await task_queue.update_stage_live_output(
        task_id="task-1",
        stage_key="0:review:review-agent",
        iteration=1,
        run=1,
        live_output=line,
    )

    _, params = mock_db.execute.call_args[0]
    # Missing cache fields default to 0
    assert params["raw_input"] == 50
    assert params["raw_output"] == 25
    assert params["raw_cache_read"] == 0
    assert params["raw_cache_write"] == 0
    assert params["msg_id"] == "msg_sparse"


@pytest.mark.asyncio
async def test_update_stage_live_output_consecutive_dedup_calls(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Two consecutive calls with the same message.id should both use dedup SQL,
    verifying the path is consistent across calls."""
    for tokens in [10, 20]:
        line = json.dumps({
            "type": "assistant",
            "message": {
                "id": "msg_repeat",
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": tokens,
                },
            },
        })
        await task_queue.update_stage_live_output(
            task_id="task-1",
            stage_key="0:review:review-agent",
            iteration=1,
            run=1,
            live_output=line,
        )

    assert mock_db.execute.call_count == 2
    # Both calls should use dedup path
    for i in range(2):
        sql, params = mock_db.execute.call_args_list[i][0]
        assert "msg_spending_state" in sql
        assert "GREATEST" in sql
        assert params["msg_id"] == "msg_repeat"


@pytest.mark.asyncio
async def test_get_live_stage_spending_with_deduped_duplicates(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """get_live_stage_spending reading raw_output with duplicate message IDs
    should return deduped totals."""
    lines = [
        json.dumps({"type": "assistant", "message": {
            "id": "msg_dup", "model": "claude-sonnet-4-5",
            "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "output_tokens": 50}}}),
        json.dumps({"type": "assistant", "message": {
            "id": "msg_dup", "model": "claude-sonnet-4-5",
            "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "output_tokens": 50}}}),
    ]
    mock_db.fetch_one.return_value = {"raw_output": "\n".join(lines)}

    result = await task_queue.get_live_stage_spending("task-1", "0:review:review-agent")

    assert result is not None
    # Deduped: max(100,100)=100, max(50,50)=50 — NOT 200,100
    assert result["input_tokens"] == 100
    assert result["output_tokens"] == 50
    assert result["turns"] == 2  # turns still count both emissions

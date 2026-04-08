"""Additional tests for issue #93 — incremental spending dedup.

Covers gaps identified by the test-agent review:
- _compute_deduped_totals with empty input
- stage_id path for no-ID fallback and no-spending lines
- task_queue exception handling (TypeError, KeyError) in live_output parsing
- Model propagation from system init message through dedup pricing
- Estimated cost accuracy using deduped (not raw) totals
- Batch parser: result message with total_cost_usd overrides estimated_cost
- Live output: consecutive dedup calls with decreasing values (non-monotonic)
- Migration SQL: rollback file exists and is well-formed
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.spending import (
    _compute_deduped_totals,
    get_pricing,
    parse_ndjson_spending,
)
from aquarco_supervisor.task_queue import TaskQueue


def _ndjson(*objects: dict) -> str:
    return "\n".join(json.dumps(obj) for obj in objects)


def _assistant_msg(
    msg_id: str | None,
    inp: int,
    cw: int,
    cr: int,
    out: int,
    model: str = "claude-sonnet-4-6",
) -> dict:
    message: dict = {
        "model": model,
        "usage": {
            "input_tokens": inp,
            "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": cr,
            "output_tokens": out,
        },
    }
    if msg_id is not None:
        message["id"] = msg_id
    return {"type": "assistant", "message": message}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def task_queue(mock_db: AsyncMock) -> TaskQueue:
    return TaskQueue(mock_db, max_retries=3)


# ===========================================================================
# _compute_deduped_totals: empty dict
# ===========================================================================


def test_compute_deduped_totals_empty_dict() -> None:
    """An empty msg_maxes dict should return all-zero totals."""
    result = _compute_deduped_totals({})
    assert result == (0, 0, 0, 0)


# ===========================================================================
# stage_id path: no-ID fallback
# ===========================================================================


@pytest.mark.asyncio
async def test_stage_id_no_id_fallback_uses_simple_delta(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """When stage_id is provided and message has no ID, the simple delta
    path should be used with WHERE id = %(id)s."""
    line = json.dumps(_assistant_msg(None, inp=50, cw=100, cr=25, out=10))
    await task_queue.update_stage_live_output(
        task_id="t1",
        stage_key="0:a:a",
        iteration=1,
        run=1,
        live_output=line,
        stage_id=42,
    )
    sql, params = mock_db.execute.call_args[0]
    # Should use id-based WHERE, not task_id-based
    assert "WHERE id = %(id)s" in sql
    assert params["id"] == 42
    # Simple delta path — no msg_spending_state
    assert "msg_spending_state" not in sql
    assert params["delta_input"] == 50
    assert params["delta_output"] == 10
    assert params["delta_cache_read"] == 25
    assert params["delta_cache_write"] == 100


@pytest.mark.asyncio
async def test_stage_id_no_spending_line(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Non-assistant line with stage_id should update live_output/raw_output
    but not include spending SQL."""
    line = json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"})
    await task_queue.update_stage_live_output(
        task_id="t1",
        stage_key="0:a:a",
        iteration=1,
        run=1,
        live_output=line,
        stage_id=99,
    )
    sql, params = mock_db.execute.call_args[0]
    assert "WHERE id = %(id)s" in sql
    assert params["id"] == 99
    # No spending columns in SET clause
    assert "tokens_input" not in sql
    assert "cost_usd" not in sql


# ===========================================================================
# Exception handling in live_output parsing
# ===========================================================================


@pytest.mark.asyncio
async def test_live_output_type_error_in_parsing(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """If the JSON is valid but causes a TypeError during attribute access,
    the line should still be written (no spending update)."""
    # A list is valid JSON but msg.get("type") would fail if not caught
    line = json.dumps([1, 2, 3])
    await task_queue.update_stage_live_output(
        task_id="t1",
        stage_key="0:a:a",
        iteration=1,
        run=1,
        live_output=line,
    )
    sql, params = mock_db.execute.call_args[0]
    # Still updates live_output and raw_output
    assert "live_output = %(line)s" in sql
    assert params["line"] == line
    # No spending columns
    assert "tokens_input" not in sql


@pytest.mark.asyncio
async def test_live_output_completely_invalid_json(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Completely invalid JSON should be handled gracefully with no spending."""
    line = "this is not json at all {{{"
    await task_queue.update_stage_live_output(
        task_id="t1",
        stage_key="0:a:a",
        iteration=1,
        run=1,
        live_output=line,
    )
    sql, params = mock_db.execute.call_args[0]
    assert "live_output = %(line)s" in sql
    assert "tokens_input" not in sql


@pytest.mark.asyncio
async def test_live_output_assistant_with_non_dict_message(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Assistant message where 'message' is a string, not a dict."""
    line = json.dumps({"type": "assistant", "message": "text content"})
    await task_queue.update_stage_live_output(
        task_id="t1",
        stage_key="0:a:a",
        iteration=1,
        run=1,
        live_output=line,
    )
    sql, _ = mock_db.execute.call_args[0]
    assert "tokens_input" not in sql


@pytest.mark.asyncio
async def test_live_output_assistant_with_non_dict_usage(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """Assistant message where 'usage' is a number, not a dict."""
    line = json.dumps({"type": "assistant", "message": {"usage": 42}})
    await task_queue.update_stage_live_output(
        task_id="t1",
        stage_key="0:a:a",
        iteration=1,
        run=1,
        live_output=line,
    )
    sql, _ = mock_db.execute.call_args[0]
    assert "tokens_input" not in sql


# ===========================================================================
# Model propagation and pricing with dedup
# ===========================================================================


def test_init_model_used_for_dedup_cost_estimation() -> None:
    """The model set by system/init should be used for cost estimation
    even when assistant messages have deduplication applied."""
    ndjson = _ndjson(
        {"type": "system", "subtype": "init", "model": "claude-opus-4-6"},
        _assistant_msg("msg_A", inp=1_000_000, cw=0, cr=0, out=0, model="claude-opus-4-6"),
        _assistant_msg("msg_A", inp=1_000_000, cw=0, cr=0, out=0, model="claude-opus-4-6"),
    )
    result = parse_ndjson_spending(ndjson)
    # Opus 4.6: $5/MTok input. Deduped = 1M tokens.
    assert result.estimated_cost_usd == 5.0
    assert result.model == "claude-opus-4-6"


def test_no_model_anywhere_uses_default_pricing() -> None:
    """If no model is set anywhere, the default (Sonnet) pricing is used."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    assert result.model is None
    # Default Sonnet: $3/MTok input
    assert result.estimated_cost_usd == 3.0


# ===========================================================================
# Estimated cost uses deduped totals, not raw sums
# ===========================================================================


def test_estimated_cost_based_on_deduped_totals() -> None:
    """estimated_cost_usd must be calculated from deduped totals, not
    the sum of all turn tokens (which would double-count duplicates)."""
    # Two emissions: same ID, same values → should be counted once
    ndjson = _ndjson(
        _assistant_msg("msg_1", inp=500_000, cw=500_000, cr=500_000, out=500_000),
        _assistant_msg("msg_1", inp=500_000, cw=500_000, cr=500_000, out=500_000),
    )
    result = parse_ndjson_spending(ndjson)
    # Sonnet: input=$3, cache_write=$3.75, cache_read=$0.30, output=$15
    # 500K tokens for each bucket → cost = (3 + 3.75 + 0.30 + 15) * 0.5 = $11.025
    expected = (3.0 + 3.75 + 0.30 + 15.0) * 0.5
    assert abs(result.estimated_cost_usd - expected) < 0.001


def test_result_total_cost_overrides_estimated_cost() -> None:
    """When result has total_cost_usd, estimated_cost_usd is still computed
    but total_cost_usd is the authoritative value."""
    ndjson = _ndjson(
        _assistant_msg("msg_A", inp=1_000_000, cw=0, cr=0, out=0),
        {"type": "result", "subtype": "success", "total_cost_usd": 1.23},
    )
    result = parse_ndjson_spending(ndjson)
    assert result.total_cost_usd == 1.23
    # estimated_cost_usd is still computed from deduped totals
    assert result.estimated_cost_usd == 3.0  # Sonnet: $3/MTok input


# ===========================================================================
# Live output: consecutive dedup calls with decreasing values
# ===========================================================================


@pytest.mark.asyncio
async def test_dedup_sql_params_with_decreasing_values(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """When the same msg_id is emitted with lower values (non-monotonic),
    the SQL GREATEST ensures the stored max doesn't decrease.
    Verify that raw_* params reflect the actual emission, not adjusted values."""
    # First emission: high values
    line1 = json.dumps(_assistant_msg("msg_D", inp=100, cw=500, cr=200, out=50))
    await task_queue.update_stage_live_output(
        task_id="t1", stage_key="0:a:a", iteration=1, run=1,
        live_output=line1,
    )
    _, params1 = mock_db.execute.call_args[0]
    assert params1["raw_input"] == 100
    assert params1["raw_cache_write"] == 500

    # Second emission: lower values for some fields
    line2 = json.dumps(_assistant_msg("msg_D", inp=80, cw=600, cr=150, out=60))
    await task_queue.update_stage_live_output(
        task_id="t1", stage_key="0:a:a", iteration=1, run=1,
        live_output=line2,
    )
    _, params2 = mock_db.execute.call_args[0]
    # raw_* params are the actual values from this emission
    assert params2["raw_input"] == 80
    assert params2["raw_cache_write"] == 600
    # The SQL GREATEST handles clamping — we just verify params are passed correctly
    assert params2["msg_id"] == "msg_D"


# ===========================================================================
# Live output: result-type and system-type lines produce no spending
# ===========================================================================


@pytest.mark.asyncio
async def test_live_output_result_line_no_spending(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """A result line should not trigger spending SQL."""
    line = json.dumps({
        "type": "result",
        "subtype": "success",
        "total_cost_usd": 5.00,
        "usage": {"input_tokens": 1000, "output_tokens": 500},
    })
    await task_queue.update_stage_live_output(
        task_id="t1", stage_key="0:a:a", iteration=1, run=1,
        live_output=line,
    )
    sql, _ = mock_db.execute.call_args[0]
    assert "tokens_input" not in sql
    assert "cost_usd" not in sql


@pytest.mark.asyncio
async def test_live_output_system_init_line_no_spending(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """A system/init line should not trigger spending SQL."""
    line = json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"})
    await task_queue.update_stage_live_output(
        task_id="t1", stage_key="0:a:a", iteration=1, run=1,
        live_output=line,
    )
    sql, _ = mock_db.execute.call_args[0]
    assert "tokens_input" not in sql


# ===========================================================================
# Batch parser: turns list preserves all emissions including duplicates
# ===========================================================================


def test_turns_list_includes_all_emissions_even_duplicates() -> None:
    """Every assistant emission should produce a TurnSpending entry in
    summary.turns, even if the message ID is duplicated. This is
    intentional for debugging/logging purposes."""
    ndjson = _ndjson(
        _assistant_msg("msg_A", inp=10, cw=100, cr=0, out=5),
        _assistant_msg("msg_A", inp=10, cw=100, cr=0, out=8),
        _assistant_msg("msg_A", inp=10, cw=100, cr=0, out=12),
    )
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 3
    # But deduped totals use max
    assert result.total_output == 12


# ===========================================================================
# Migration SQL correctness
# ===========================================================================


def test_migration_039_rollback_exists() -> None:
    """Rollback file must exist in archive and contain DROP COLUMN."""
    import os
    rollback_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "db", "migrations", "archive",
        "039_add_stage_msg_spending_state.rollback.sql",
    )
    rollback_path = os.path.normpath(rollback_path)
    assert os.path.exists(rollback_path), f"Rollback file missing: {rollback_path}"
    with open(rollback_path) as f:
        content = f.read()
    assert "DROP COLUMN" in content.upper() or "drop column" in content.lower()


def test_migration_039_has_if_not_exists() -> None:
    """Migration must use IF NOT EXISTS for idempotency (archived)."""
    import os
    migration_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "db", "migrations", "archive",
        "039_add_stage_msg_spending_state.sql",
    )
    migration_path = os.path.normpath(migration_path)
    with open(migration_path) as f:
        content = f.read()
    assert "IF NOT EXISTS" in content


# ===========================================================================
# get_pricing: additional edge cases
# ===========================================================================


def test_get_pricing_opus_4_1() -> None:
    """Opus 4.1 should use the older (more expensive) tier."""
    p = get_pricing("claude-opus-4-1")
    assert p["input"] == 15
    assert p["output"] == 75


def test_get_pricing_empty_string() -> None:
    """Empty model string should return default (Sonnet) pricing."""
    p = get_pricing("")
    assert p["input"] == 3
    assert p["output"] == 15


# ===========================================================================
# Dedup with only cache tokens (no input/output)
# ===========================================================================


def test_dedup_cache_only_tokens() -> None:
    """Messages that only have cache tokens (input=0, output=0)
    should still be deduped correctly."""
    ndjson = _ndjson(
        _assistant_msg("msg_C", inp=0, cw=5000, cr=10000, out=0),
        _assistant_msg("msg_C", inp=0, cw=5000, cr=10000, out=0),
    )
    result = parse_ndjson_spending(ndjson)
    assert result.total_input == 0
    assert result.total_cache_write == 5000
    assert result.total_cache_read == 10000
    assert result.total_output == 0


@pytest.mark.asyncio
async def test_live_output_cache_only_uses_dedup_path(
    task_queue: TaskQueue, mock_db: AsyncMock
) -> None:
    """has_usage should be truthy when only cache tokens are present,
    and with a msg_id, should use the dedup path."""
    line = json.dumps(_assistant_msg("msg_C", inp=0, cw=5000, cr=10000, out=0))
    await task_queue.update_stage_live_output(
        task_id="t1", stage_key="0:a:a", iteration=1, run=1,
        live_output=line,
    )
    sql, params = mock_db.execute.call_args[0]
    assert "msg_spending_state" in sql
    assert params["msg_id"] == "msg_C"
    assert params["raw_cache_write"] == 5000
    assert params["raw_cache_read"] == 10000

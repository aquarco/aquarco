"""Acceptance tests for GitHub issue #93 — incremental spendings fix.

These tests validate the core acceptance criteria from the issue:
1. Duplicate message IDs with cumulative values use MAX per field, not SUM
2. Incremental accumulation converges monotonically to the final total
3. Deduplication works correctly in both batch (spending.py) and live (task_queue.py) paths
4. The fix for the JSONB literal bug ('{}'::jsonb vs '{{}}'::jsonb) holds

Test organization:
- AC1: Batch parser dedup with realistic streaming patterns
- AC2: Live output dedup SQL correctness
- AC3: End-to-end property tests (monotonic convergence, idempotency)
- AC4: Regression guards for the JSONB literal fix
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


def _assistant_msg(msg_id: str | None, inp: int, cw: int, cr: int, out: int,
                   model: str = "claude-sonnet-4-6") -> dict:
    """Helper to build an assistant message dict."""
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
# AC1: Batch parser — realistic streaming patterns with progressive duplicates
# ===========================================================================


class TestBatchParserStreamingPatterns:
    """Simulate realistic CLI streaming: each message ID emitted multiple times
    with progressively higher (cumulative) token counts."""

    def test_progressive_duplicate_emissions_use_max(self) -> None:
        """A single message ID emitted 3 times with increasing output_tokens.
        The deduped total should be the final (max) value, not the sum."""
        ndjson = _ndjson(
            _assistant_msg("msg_A", inp=3, cw=15078, cr=0, out=10),
            _assistant_msg("msg_A", inp=3, cw=15078, cr=0, out=18),
            _assistant_msg("msg_A", inp=3, cw=15078, cr=0, out=23),
        )
        result = parse_ndjson_spending(ndjson)
        assert result.total_input == 3
        assert result.total_cache_write == 15078
        assert result.total_output == 23  # max, not 10+18+23=51

    def test_issue93_full_streaming_simulation(self) -> None:
        """Simulate the exact streaming pattern from issue #93 where each
        message ID appears multiple times with cumulative token counts.
        The 'accumulate values as they occur' table from the issue should
        produce the same final totals as single emissions."""
        # Simulate progressive emissions for each message
        emissions = [
            # msg_01RR: emitted twice (first partial, then complete)
            _assistant_msg("msg_01RR", inp=3, cw=10000, cr=0, out=15),
            _assistant_msg("msg_01RR", inp=3, cw=15078, cr=0, out=23),
            # msg_01TL: emitted twice
            _assistant_msg("msg_01TL", inp=1, cw=12000, cr=0, out=30),
            _assistant_msg("msg_01TL", inp=1, cw=15757, cr=0, out=64),
            # msg_01XL: emitted once (final values)
            _assistant_msg("msg_01XL", inp=1, cw=7028, cr=15757, out=37),
            # msg_01SL: emitted three times
            _assistant_msg("msg_01SL", inp=1, cw=3000, cr=15000, out=5),
            _assistant_msg("msg_01SL", inp=1, cw=5000, cr=20000, out=7),
            _assistant_msg("msg_01SL", inp=1, cw=6019, cr=22785, out=9),
        ]
        ndjson = _ndjson(*emissions)
        result = parse_ndjson_spending(ndjson)

        # Expected: max per field per unique ID, then sum across IDs
        # msg_01RR: (3, 15078, 0, 23)
        # msg_01TL: (1, 15757, 0, 64)
        # msg_01XL: (1, 7028, 15757, 37)
        # msg_01SL: (1, 6019, 22785, 9)
        assert result.total_input == 6      # 3+1+1+1
        assert result.total_cache_write == 43882  # 15078+15757+7028+6019
        assert result.total_cache_read == 38542   # 0+0+15757+22785
        assert result.total_output == 133   # 23+64+37+9

    def test_non_monotonic_field_values_across_emissions(self) -> None:
        """Token values can decrease between emissions (e.g., cache_read might
        be reported lower in a retry). MAX should handle this correctly."""
        ndjson = _ndjson(
            _assistant_msg("msg_X", inp=100, cw=500, cr=200, out=50),
            _assistant_msg("msg_X", inp=80, cw=600, cr=150, out=60),  # inp and cr decreased
            _assistant_msg("msg_X", inp=90, cw=400, cr=250, out=55),  # cw decreased
        )
        result = parse_ndjson_spending(ndjson)
        # MAX per field: inp=100, cw=600, cr=250, out=60
        assert result.total_input == 100
        assert result.total_cache_write == 600
        assert result.total_cache_read == 250
        assert result.total_output == 60

    def test_mixed_messages_with_without_ids_and_duplicates(self) -> None:
        """Realistic scenario: some messages have IDs (streamed), some don't
        (older CLI), and some IDs are duplicated."""
        ndjson = _ndjson(
            _assistant_msg("msg_A", inp=10, cw=100, cr=0, out=5),
            _assistant_msg(None, inp=20, cw=200, cr=0, out=10),      # no ID → unique
            _assistant_msg("msg_A", inp=10, cw=100, cr=0, out=8),    # dup of msg_A
            _assistant_msg("msg_B", inp=30, cw=300, cr=50, out=15),
            _assistant_msg(None, inp=40, cw=400, cr=0, out=20),      # no ID → unique
        )
        result = parse_ndjson_spending(ndjson)
        # msg_A: max(10,10)=10, max(100,100)=100, max(0,0)=0, max(5,8)=8
        # no-id-1: 20, 200, 0, 10
        # msg_B: 30, 300, 50, 15
        # no-id-2: 40, 400, 0, 20
        assert result.total_input == 100    # 10+20+30+40
        assert result.total_cache_write == 1000  # 100+200+300+400
        assert result.total_cache_read == 50     # 0+0+50+0
        assert result.total_output == 53    # 8+10+15+20


# ===========================================================================
# AC2: Live output dedup SQL — interleaving and edge cases
# ===========================================================================


class TestLiveOutputDedupSQL:
    """Tests for the task_queue.update_stage_live_output dedup SQL path."""

    @pytest.mark.asyncio
    async def test_interleaved_dedup_and_no_id_calls(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """Mixed sequence: msg with ID, msg without ID, msg with same ID again.
        Each call should use the correct SQL path."""
        lines = [
            json.dumps(_assistant_msg("msg_A", inp=10, cw=0, cr=0, out=5)),
            json.dumps(_assistant_msg(None, inp=20, cw=0, cr=0, out=10)),
            json.dumps(_assistant_msg("msg_A", inp=15, cw=0, cr=0, out=8)),
        ]
        for line in lines:
            await task_queue.update_stage_live_output(
                task_id="t1", stage_key="0:a:a", iteration=1, run=1,
                live_output=line,
            )

        assert mock_db.execute.call_count == 3
        # Call 0: dedup (has msg_id)
        sql0, p0 = mock_db.execute.call_args_list[0][0]
        assert "msg_spending_state" in sql0
        assert p0["msg_id"] == "msg_A"
        # Call 1: simple delta (no msg_id)
        sql1, p1 = mock_db.execute.call_args_list[1][0]
        assert "msg_spending_state" not in sql1
        assert p1["delta_input"] == 20
        # Call 2: dedup again (same msg_id)
        sql2, p2 = mock_db.execute.call_args_list[2][0]
        assert "msg_spending_state" in sql2
        assert p2["msg_id"] == "msg_A"
        assert p2["raw_input"] == 15

    @pytest.mark.asyncio
    async def test_dedup_sql_contains_greatest_for_all_token_deltas(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """The dedup SQL must use GREATEST for all four token delta computations
        to ensure negative deltas (from non-monotonic emissions) are clamped to 0."""
        line = json.dumps(_assistant_msg("msg_X", inp=10, cw=20, cr=30, out=40))
        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line,
        )
        sql, _ = mock_db.execute.call_args[0]
        # Count GREATEST occurrences — should be at least 8:
        # 4 for jsonb_build_object (new max per field) + 4 for token column deltas + cost deltas
        greatest_count = sql.count("GREATEST")
        assert greatest_count >= 8, f"Expected >=8 GREATEST calls, got {greatest_count}"

    @pytest.mark.asyncio
    async def test_dedup_sql_coalesce_prevents_null_arithmetic(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """COALESCE must wrap msg_spending_state reads to handle NULL (first emission)."""
        line = json.dumps(_assistant_msg("msg_new", inp=5, cw=10, cr=15, out=20))
        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line,
        )
        sql, _ = mock_db.execute.call_args[0]
        # COALESCE(stages.msg_spending_state, '{}'::jsonb) pattern must exist
        assert "COALESCE(stages.msg_spending_state" in sql
        # Multiple COALESCE calls for each field read
        coalesce_count = sql.count("COALESCE")
        assert coalesce_count >= 10, f"Expected many COALESCE calls, got {coalesce_count}"

    @pytest.mark.asyncio
    async def test_different_model_pricing_in_consecutive_dedup_calls(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """If model changes between emissions (unlikely but possible), each call
        should use the pricing from its own message."""
        line_sonnet = json.dumps(_assistant_msg("msg_1", inp=100, cw=0, cr=0, out=50,
                                                model="claude-sonnet-4-6"))
        line_opus = json.dumps(_assistant_msg("msg_2", inp=100, cw=0, cr=0, out=50,
                                              model="claude-opus-4-6"))
        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line_sonnet,
        )
        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line_opus,
        )
        _, p_sonnet = mock_db.execute.call_args_list[0][0]
        _, p_opus = mock_db.execute.call_args_list[1][0]
        # Sonnet: $3/MTok input
        assert p_sonnet["price_input"] == 3 / 1_000_000
        # Opus 4.6: $5/MTok input
        assert p_opus["price_input"] == 5 / 1_000_000

    @pytest.mark.asyncio
    async def test_no_id_fallback_cost_includes_all_four_buckets(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """The no-ID fallback should compute delta_cost from all four token types."""
        line = json.dumps(_assistant_msg(None, inp=1_000_000, cw=1_000_000,
                                         cr=1_000_000, out=1_000_000))
        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line,
        )
        _, params = mock_db.execute.call_args[0]
        # Sonnet pricing: input=$3, cache_write=$3.75, cache_read=$0.30, output=$15
        expected_cost = 3.0 + 3.75 + 0.30 + 15.0
        assert abs(params["delta_cost"] - expected_cost) < 0.001


# ===========================================================================
# AC3: Property tests — monotonic convergence and idempotency
# ===========================================================================


class TestMonotonicConvergence:
    """Core property from issue #93: incremental dedup totals converge
    monotonically to the final value and never exceed it."""

    def test_single_id_progressive_emissions_converge(self) -> None:
        """A single message ID emitted with progressively higher values.
        Each prefix of emissions should produce totals that monotonically increase."""
        emissions = [
            (3, 10000, 0, 10),
            (3, 12000, 0, 15),
            (3, 14000, 0, 18),
            (3, 15078, 0, 23),
        ]
        prev = (0, 0, 0, 0)
        for step in range(1, len(emissions) + 1):
            ndjson = _ndjson(*[
                _assistant_msg("msg_01", *e) for e in emissions[:step]
            ])
            r = parse_ndjson_spending(ndjson)
            current = (r.total_input, r.total_cache_write, r.total_cache_read, r.total_output)
            for i, (c, p) in enumerate(zip(current, prev)):
                assert c >= p, f"Step {step}, field {i}: {c} < {p}"
            prev = current
        # Final should match the max emission values
        assert prev == (3, 15078, 0, 23)

    def test_multiple_ids_independent_convergence(self) -> None:
        """Multiple message IDs each converge independently. Adding emissions
        for one ID should not affect another."""
        # First: only msg_A
        r1 = parse_ndjson_spending(_ndjson(
            _assistant_msg("msg_A", inp=10, cw=100, cr=0, out=5),
        ))
        # Add msg_B
        r2 = parse_ndjson_spending(_ndjson(
            _assistant_msg("msg_A", inp=10, cw=100, cr=0, out=5),
            _assistant_msg("msg_B", inp=20, cw=200, cr=50, out=10),
        ))
        # msg_A totals unchanged, msg_B adds to overall
        assert r2.total_input == r1.total_input + 20
        assert r2.total_cache_write == r1.total_cache_write + 200
        assert r2.total_output == r1.total_output + 10

    def test_idempotent_duplicate_emissions(self) -> None:
        """Emitting the same message N times (same ID, same values) should
        produce the same result as emitting it once — idempotent dedup."""
        single = parse_ndjson_spending(_ndjson(
            _assistant_msg("msg_A", inp=42, cw=1000, cr=500, out=99),
        ))
        for n in [2, 5, 10, 50]:
            multi = parse_ndjson_spending(_ndjson(
                *[_assistant_msg("msg_A", inp=42, cw=1000, cr=500, out=99)] * n
            ))
            assert multi.total_input == single.total_input, f"n={n}"
            assert multi.total_cache_write == single.total_cache_write, f"n={n}"
            assert multi.total_cache_read == single.total_cache_read, f"n={n}"
            assert multi.total_output == single.total_output, f"n={n}"
            assert multi.estimated_cost_usd == single.estimated_cost_usd, f"n={n}"

    def test_cost_never_inflated_by_duplicates(self) -> None:
        """The estimated cost with duplicates must equal the cost without."""
        msg = _assistant_msg("msg_X", inp=1_000_000, cw=0, cr=0, out=0,
                             model="claude-sonnet-4-6")
        r_single = parse_ndjson_spending(_ndjson(msg))
        r_triple = parse_ndjson_spending(_ndjson(msg, msg, msg))
        # Both should be $3.00 (sonnet input pricing)
        assert r_single.estimated_cost_usd == 3.0
        assert r_triple.estimated_cost_usd == 3.0


# ===========================================================================
# AC4: Regression guards for JSONB literal fix
# ===========================================================================


class TestJSONBLiteralRegression:
    """Guard against regression of the '{{}}'::jsonb -> '{}'::jsonb fix."""

    @pytest.mark.asyncio
    async def test_jsonb_literal_validity_in_dedup_sql(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """All JSONB empty-object literals in the SQL must be valid JSON."""
        line = json.dumps(_assistant_msg("msg_check", inp=1, cw=1, cr=1, out=1))
        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line,
        )
        sql, _ = mock_db.execute.call_args[0]

        # Positive: valid literals present
        valid_count = sql.count("'{}'::jsonb")
        assert valid_count >= 1, "No valid '{}'::jsonb literals found in SQL"

        # Negative: no invalid double-brace literals
        assert "'{{}}'::jsonb" not in sql, "Found invalid '{{}}'::jsonb"
        assert "'{{' " not in sql, "Found stray '{{' in SQL"

    @pytest.mark.asyncio
    async def test_spending_sql_is_not_fstring(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """The spending_sql variable must use %(param)s placeholders, not
        f-string interpolation, to prevent SQL injection and brace confusion."""
        line = json.dumps(_assistant_msg("msg_safe", inp=10, cw=0, cr=0, out=5))
        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line,
        )
        sql, _ = mock_db.execute.call_args[0]

        # Must use psycopg-style parameterized placeholders
        assert "%(msg_id)s" in sql
        assert "%(raw_input)s" in sql
        assert "%(raw_output)s" in sql
        # Must NOT contain f-string artifacts like {msg_id} without %()
        # (the only { in the SQL should be in '{}'::jsonb)
        import re
        # Find all {word} patterns that aren't %(word)s
        bare_interpolations = re.findall(r'(?<!%)(?<!\')(?<!\{)\{[a-z_]+\}(?!\')', sql)
        assert bare_interpolations == [], f"Found bare f-string interpolations: {bare_interpolations}"


# ===========================================================================
# Additional edge cases from review findings
# ===========================================================================


class TestReviewFindings:
    """Tests addressing specific findings from the code review."""

    def test_result_total_cost_usd_without_usage_dict(self) -> None:
        """Result message with total_cost_usd but no usage dict should
        set authoritative cost but use deduped totals for tokens."""
        ndjson = _ndjson(
            _assistant_msg("msg_A", inp=100, cw=500, cr=200, out=50),
            _assistant_msg("msg_A", inp=100, cw=500, cr=200, out=50),  # dup
            {"type": "result", "subtype": "success", "total_cost_usd": 0.50},
        )
        result = parse_ndjson_spending(ndjson)
        assert result.total_cost_usd == 0.50
        # Deduped totals used (no usage in result)
        assert result.total_input == 100
        assert result.total_cache_write == 500
        assert result.total_cache_read == 200
        assert result.total_output == 50

    def test_has_usage_falsy_when_all_zero_with_msg_id(self) -> None:
        """A message with ID but all-zero usage should not track spending.
        A later emission with non-zero values should work correctly."""
        # First: all zeros with ID → skipped
        # Second: non-zero with same ID → treated as first occurrence
        ndjson = _ndjson(
            _assistant_msg("msg_Z", inp=0, cw=0, cr=0, out=0),
            _assistant_msg("msg_Z", inp=100, cw=500, cr=200, out=50),
        )
        result = parse_ndjson_spending(ndjson)
        # The all-zero emission still creates a turn but doesn't affect msg_maxes
        # because it has 0 for all fields. The second emission sets the max.
        # Actually, looking at the code: the all-zero emission still enters msg_maxes
        # because the dedup happens in the assistant message handler regardless of
        # has_usage. has_usage is only for task_queue.py.
        # In spending.py, all assistant messages with usage dicts are processed.
        assert result.total_input == 100
        assert result.total_cache_write == 500
        assert result.total_cache_read == 200
        assert result.total_output == 50

    @pytest.mark.asyncio
    async def test_live_output_zero_usage_then_nonzero_same_id(
        self, task_queue: TaskQueue, mock_db: AsyncMock
    ) -> None:
        """In task_queue: all-zero usage with msg_id is skipped (has_usage falsy).
        A later emission with non-zero values is treated as first occurrence."""
        line_zero = json.dumps(_assistant_msg("msg_Z", inp=0, cw=0, cr=0, out=0))
        line_real = json.dumps(_assistant_msg("msg_Z", inp=100, cw=500, cr=200, out=50))

        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line_zero,
        )
        await task_queue.update_stage_live_output(
            task_id="t1", stage_key="0:a:a", iteration=1, run=1,
            live_output=line_real,
        )

        assert mock_db.execute.call_count == 2
        # First call: no spending SQL (all-zero usage)
        sql0, _ = mock_db.execute.call_args_list[0][0]
        assert "msg_spending_state" not in sql0

        # Second call: dedup SQL (non-zero usage with msg_id)
        sql1, p1 = mock_db.execute.call_args_list[1][0]
        assert "msg_spending_state" in sql1
        assert p1["msg_id"] == "msg_Z"
        assert p1["raw_input"] == 100

    def test_compute_deduped_totals_with_extra_keys_ignored(self) -> None:
        """Extra keys in the per-message dict should be harmlessly ignored."""
        msg_maxes = {
            "msg_01": {
                "input_tokens": 10,
                "cache_write_tokens": 20,
                "cache_read_tokens": 30,
                "output_tokens": 40,
                "extra_field": 999,
            }
        }
        result = _compute_deduped_totals(msg_maxes)
        assert result == (10, 20, 30, 40)

    def test_dedup_with_very_large_token_counts(self) -> None:
        """Verify no overflow or precision issues with large token counts."""
        large = 100_000_000  # 100M tokens
        ndjson = _ndjson(
            _assistant_msg("msg_big", inp=large, cw=large, cr=large, out=large),
            _assistant_msg("msg_big", inp=large, cw=large, cr=large, out=large),
        )
        result = parse_ndjson_spending(ndjson)
        assert result.total_input == large
        assert result.total_cache_write == large
        assert result.total_cache_read == large
        assert result.total_output == large

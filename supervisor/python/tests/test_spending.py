"""Tests for the NDJSON spending parser."""

from __future__ import annotations

import json

from aquarco_supervisor.spending import (
    SpendingSummary,
    TurnSpending,
    _compute_deduped_totals,
    parse_ndjson_spending,
    get_pricing,
)


def _ndjson(*objects: dict) -> str:
    """Build an NDJSON string from dicts."""
    return "\n".join(json.dumps(obj) for obj in objects)


def test_empty_input() -> None:
    result = parse_ndjson_spending("")
    assert result.turns == []
    assert result.total_input == 0
    assert result.total_cost_usd is None


def test_single_assistant_turn() -> None:
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 5000,
                "cache_read_input_tokens": 0,
                "output_tokens": 50,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 1
    assert result.total_input == 100
    assert result.total_cache_write == 5000
    assert result.total_cache_read == 0
    assert result.total_output == 50
    assert result.total_cost_usd is None
    assert result.model == "claude-sonnet-4-6"


def test_multiple_turns_accumulate() -> None:
    ndjson = _ndjson(
        {"type": "assistant", "message": {"model": "claude-sonnet-4-6", "usage": {
            "input_tokens": 10, "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 0, "output_tokens": 20,
        }}},
        {"type": "assistant", "message": {"model": "claude-sonnet-4-6", "usage": {
            "input_tokens": 5, "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 200, "output_tokens": 30,
        }}},
    )
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 2
    # Per-turn sums
    assert result.total_input == 15
    assert result.total_cache_write == 150
    assert result.total_cache_read == 200
    assert result.total_output == 50


def test_result_message_overrides_totals() -> None:
    """Result message usage replaces per-turn sums with billing aggregate."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {"model": "claude-sonnet-4-6", "usage": {
            "input_tokens": 10, "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 500, "output_tokens": 20,
        }}},
        {"type": "result", "subtype": "success",
         "total_cost_usd": 0.25,
         "usage": {
             "input_tokens": 5,
             "cache_creation_input_tokens": 80,
             "cache_read_input_tokens": 400,
             "output_tokens": 15,
         }},
    )
    result = parse_ndjson_spending(ndjson)
    assert result.total_cost_usd == 0.25
    # Totals come from result, not per-turn sums
    assert result.total_input == 5
    assert result.total_cache_write == 80
    assert result.total_cache_read == 400
    assert result.total_output == 15
    # Turns are still tracked
    assert len(result.turns) == 1


def test_init_message_sets_model() -> None:
    ndjson = _ndjson(
        {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
        {"type": "assistant", "message": {"usage": {
            "input_tokens": 10, "output_tokens": 5,
        }}},
    )
    result = parse_ndjson_spending(ndjson)
    assert result.model == "claude-sonnet-4-6"


def test_estimated_cost_uses_model_pricing() -> None:
    ndjson = _ndjson(
        {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
        {"type": "assistant", "message": {"model": "claude-sonnet-4-6", "usage": {
            "input_tokens": 1_000_000,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 0,
        }}},
    )
    result = parse_ndjson_spending(ndjson)
    # Sonnet: $3/MTok input
    assert result.estimated_cost_usd == 3.0
    assert result.total_cost_usd is None


def test_malformed_lines_skipped() -> None:
    ndjson = "not json\n" + _ndjson(
        {"type": "assistant", "message": {"usage": {
            "input_tokens": 10, "output_tokens": 5,
        }}},
    ) + "\nalso not json"
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 1


def testget_pricing_opus() -> None:
    p = get_pricing("claude-opus-4-6")
    assert p["input"] == 5
    assert p["output"] == 25


def testget_pricing_sonnet_default() -> None:
    p = get_pricing("claude-sonnet-4-6")
    assert p["input"] == 3
    assert p["output"] == 15


def testget_pricing_haiku() -> None:
    p = get_pricing("claude-haiku-4-5")
    assert p["input"] == 1
    assert p["output"] == 5


def testget_pricing_unknown_defaults_to_sonnet() -> None:
    p = get_pricing("unknown-model")
    assert p["input"] == 3


# ---------------------------------------------------------------------------
# Deduplication by message.id (issue #93)
# ---------------------------------------------------------------------------


def test_duplicate_message_id_takes_max_not_sum() -> None:
    """When the same message.id appears multiple times with cumulative values,
    the parser should take the MAX per field, not sum them.
    """
    ndjson = _ndjson(
        # First emission of msg_01AAA — partial values
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 15078,
                "cache_read_input_tokens": 0,
                "output_tokens": 10,
            },
        }},
        # Second emission of msg_01AAA — cumulative (higher) values
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 15078,
                "cache_read_input_tokens": 0,
                "output_tokens": 23,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # Should take max, not sum: input=3 (not 6), cache_write=15078 (not 30156), output=23 (not 33)
    assert result.total_input == 3
    assert result.total_cache_write == 15078
    assert result.total_cache_read == 0
    assert result.total_output == 23
    # Both emissions still produce turn entries (for logging/debugging)
    assert len(result.turns) == 2


def test_multiple_unique_message_ids_sum_correctly() -> None:
    """Different message.id values should have their MAX values summed."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 15078,
                "cache_read_input_tokens": 0,
                "output_tokens": 23,
            },
        }},
        # Duplicate of msg_01AAA with same values (no change to max)
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 15078,
                "cache_read_input_tokens": 0,
                "output_tokens": 23,
            },
        }},
        {"type": "assistant", "message": {
            "id": "msg_01BBB",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 1,
                "cache_creation_input_tokens": 7028,
                "cache_read_input_tokens": 15757,
                "output_tokens": 37,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # msg_01AAA: max(3), max(15078), max(0), max(23)
    # msg_01BBB: max(1), max(7028), max(15757), max(37)
    # Total: 4, 22106, 15757, 60
    assert result.total_input == 4
    assert result.total_cache_write == 22106
    assert result.total_cache_read == 15757
    assert result.total_output == 60


def test_issue_93_example_data() -> None:
    """Reproduce the exact scenario from GitHub issue #93.

    Seven unique message IDs, each appearing with cumulative token counts.
    The correct totals should match the GROUP BY MAX approach.
    """
    # Each message appears once with its final (max) values from the issue
    messages = [
        ("msg_01RRjSF2uva5HRKurdkcStgH", 3, 15078, 0, 23),
        ("msg_01TLGaJaQZFGnMSjgtoJ7Efp", 1, 15757, 0, 64),
        ("msg_01XLR1TsFTuhP2vcV3bVs6LE", 1, 7028, 15757, 37),
        ("msg_01SLZd8wN1CASCxzWamjZA3C", 1, 6019, 22785, 9),
        ("msg_01Do3ihFfK3tBVMXQZFMx5ge", 1, 3559, 28804, 1),
        ("msg_01MSQDJAuKvw7va8gvBtZX2W", 1, 541, 32363, 60),
        ("msg_01UDc2i8TGAyrms2fy1sFLyE", 1, 3934, 32363, 2),
    ]
    ndjson = _ndjson(
        *[
            {"type": "assistant", "message": {
                "id": msg_id,
                "model": "claude-sonnet-4-6",
                "usage": {
                    "input_tokens": inp,
                    "cache_creation_input_tokens": cw,
                    "cache_read_input_tokens": cr,
                    "output_tokens": out,
                },
            }}
            for msg_id, inp, cw, cr, out in messages
        ]
    )
    result = parse_ndjson_spending(ndjson)
    # Expected totals (sum of max per unique message_id):
    assert result.total_input == 9         # 3+1+1+1+1+1+1
    assert result.total_cache_write == 51916   # sum of cache_creation values
    assert result.total_cache_read == 132072   # sum of cache_read values
    assert result.total_output == 196      # sum of output values


def test_messages_without_id_treated_as_unique() -> None:
    """Messages without a message.id should always have tokens added (no dedup)."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 0,
                "output_tokens": 20,
            },
        }},
        {"type": "assistant", "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 5,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 200,
                "output_tokens": 30,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # No message.id — both are treated as unique, so tokens are summed
    assert result.total_input == 15
    assert result.total_cache_write == 150
    assert result.total_cache_read == 200
    assert result.total_output == 50


def test_result_usage_overrides_deduped_totals() -> None:
    """When a result message includes usage, it takes precedence over dedup."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 500,
                "output_tokens": 20,
            },
        }},
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 500,
                "output_tokens": 20,
            },
        }},
        {"type": "result", "subtype": "success",
         "total_cost_usd": 0.25,
         "usage": {
             "input_tokens": 5,
             "cache_creation_input_tokens": 80,
             "cache_read_input_tokens": 400,
             "output_tokens": 15,
         }},
    )
    result = parse_ndjson_spending(ndjson)
    assert result.total_cost_usd == 0.25
    # Totals come from result, not deduped per-turn
    assert result.total_input == 5
    assert result.total_cache_write == 80
    assert result.total_cache_read == 400
    assert result.total_output == 15


# ---------------------------------------------------------------------------
# Additional deduplication edge-case tests (issue #93, test-agent stage)
# ---------------------------------------------------------------------------


def test_compute_deduped_totals_empty() -> None:
    """_compute_deduped_totals returns zeros for an empty dict."""
    result = _compute_deduped_totals({})
    assert result == (0, 0, 0, 0)


def test_compute_deduped_totals_single_entry() -> None:
    """_compute_deduped_totals sums a single entry correctly."""
    msg_maxes = {
        "msg_01": {
            "input_tokens": 5,
            "cache_write_tokens": 100,
            "cache_read_tokens": 200,
            "output_tokens": 30,
        }
    }
    result = _compute_deduped_totals(msg_maxes)
    assert result == (5, 100, 200, 30)


def test_compute_deduped_totals_multiple_entries() -> None:
    """_compute_deduped_totals sums across multiple entries."""
    msg_maxes = {
        "msg_01": {"input_tokens": 3, "cache_write_tokens": 100, "cache_read_tokens": 0, "output_tokens": 10},
        "msg_02": {"input_tokens": 1, "cache_write_tokens": 50, "cache_read_tokens": 200, "output_tokens": 20},
    }
    result = _compute_deduped_totals(msg_maxes)
    assert result == (4, 150, 200, 30)


def test_non_monotonic_values_takes_max_per_field() -> None:
    """When a later emission has lower values for some fields but higher for
    others, each field should independently take its max value.
    """
    ndjson = _ndjson(
        # First emission: high cache_write, low output
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 5000,
                "cache_read_input_tokens": 100,
                "output_tokens": 5,
            },
        }},
        # Second emission: lower cache_write (non-monotonic), higher output
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 8,
                "cache_creation_input_tokens": 3000,
                "cache_read_input_tokens": 200,
                "output_tokens": 25,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # Each field independently takes its max across emissions
    assert result.total_input == 10          # max(10, 8)
    assert result.total_cache_write == 5000  # max(5000, 3000)
    assert result.total_cache_read == 200    # max(100, 200)
    assert result.total_output == 25         # max(5, 25)


def test_mixed_messages_with_and_without_ids() -> None:
    """Messages with IDs are deduped; messages without IDs are always summed."""
    ndjson = _ndjson(
        # Message with ID — first emission
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 0,
                "output_tokens": 20,
            },
        }},
        # Same ID — duplicate (should take max, not add)
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 0,
                "output_tokens": 20,
            },
        }},
        # Message without ID — always counted
        {"type": "assistant", "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 5,
                "cache_creation_input_tokens": 50,
                "cache_read_input_tokens": 0,
                "output_tokens": 10,
            },
        }},
        # Another message without ID — also counted separately
        {"type": "assistant", "message": {
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 3,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 0,
                "output_tokens": 7,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # msg_01AAA: max(10,10)=10, max(100,100)=100, max(0,0)=0, max(20,20)=20
    # no-id-1: 5, 50, 0, 10
    # no-id-2: 3, 30, 0, 7
    # Total: 18, 180, 0, 37
    assert result.total_input == 18
    assert result.total_cache_write == 180
    assert result.total_cache_read == 0
    assert result.total_output == 37
    assert len(result.turns) == 4


def test_issue_93_with_duplicate_emissions() -> None:
    """Issue #93 scenario with duplicate emissions for the same message IDs.

    Simulates the real streaming protocol where each message.id appears
    multiple times with progressively increasing cumulative values.
    The totals must match the single-emission case (GROUP BY MAX).
    """
    # Simulate multiple emissions per message ID (partial → final)
    ndjson = _ndjson(
        # msg_01RR: partial, then final
        {"type": "assistant", "message": {"id": "msg_01RRjSF2uva5HRKurdkcStgH", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 2, "cache_creation_input_tokens": 10000, "cache_read_input_tokens": 0, "output_tokens": 10}}},
        {"type": "assistant", "message": {"id": "msg_01RRjSF2uva5HRKurdkcStgH", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 3, "cache_creation_input_tokens": 15078, "cache_read_input_tokens": 0, "output_tokens": 23}}},
        # msg_01TL: partial, then final
        {"type": "assistant", "message": {"id": "msg_01TLGaJaQZFGnMSjgtoJ7Efp", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "cache_creation_input_tokens": 8000, "cache_read_input_tokens": 0, "output_tokens": 30}}},
        {"type": "assistant", "message": {"id": "msg_01TLGaJaQZFGnMSjgtoJ7Efp", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "cache_creation_input_tokens": 15757, "cache_read_input_tokens": 0, "output_tokens": 64}}},
        # msg_01XL: single emission
        {"type": "assistant", "message": {"id": "msg_01XLR1TsFTuhP2vcV3bVs6LE", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "cache_creation_input_tokens": 7028, "cache_read_input_tokens": 15757, "output_tokens": 37}}},
        # msg_01SL: single emission
        {"type": "assistant", "message": {"id": "msg_01SLZd8wN1CASCxzWamjZA3C", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "cache_creation_input_tokens": 6019, "cache_read_input_tokens": 22785, "output_tokens": 9}}},
        # msg_01Do: single emission
        {"type": "assistant", "message": {"id": "msg_01Do3ihFfK3tBVMXQZFMx5ge", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "cache_creation_input_tokens": 3559, "cache_read_input_tokens": 28804, "output_tokens": 1}}},
        # msg_01MS: single emission
        {"type": "assistant", "message": {"id": "msg_01MSQDJAuKvw7va8gvBtZX2W", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "cache_creation_input_tokens": 541, "cache_read_input_tokens": 32363, "output_tokens": 60}}},
        # msg_01UD: single emission
        {"type": "assistant", "message": {"id": "msg_01UDc2i8TGAyrms2fy1sFLyE", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 1, "cache_creation_input_tokens": 3934, "cache_read_input_tokens": 32363, "output_tokens": 2}}},
    )
    result = parse_ndjson_spending(ndjson)
    # Same expected totals as single-emission test — duplicates don't inflate
    assert result.total_input == 9
    assert result.total_cache_write == 51916
    assert result.total_cache_read == 132072
    assert result.total_output == 196


def test_estimated_cost_with_deduped_totals() -> None:
    """Cost estimation should use deduped totals, not naive sums."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        }},
        # Duplicate emission — should not double cost
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # Sonnet: $3/MTok input. Deduped total is 1M tokens, not 2M.
    assert result.estimated_cost_usd == 3.0  # not 6.0


def test_three_emissions_same_id_progressive() -> None:
    """Three progressive emissions of the same ID should take the final max."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {"id": "msg_01", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 0, "output_tokens": 10}}},
        {"type": "assistant", "message": {"id": "msg_01", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 5, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 0, "output_tokens": 20}}},
        {"type": "assistant", "message": {"id": "msg_01", "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 5, "cache_creation_input_tokens": 300, "cache_read_input_tokens": 0, "output_tokens": 30}}},
    )
    result = parse_ndjson_spending(ndjson)
    assert result.total_input == 5
    assert result.total_cache_write == 300
    assert result.total_output == 30
    assert len(result.turns) == 3  # all turns recorded


def test_result_without_usage_still_uses_deduped_totals() -> None:
    """A result message with total_cost_usd but no usage dict should not
    override the deduped token totals."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                      "cache_read_input_tokens": 0, "output_tokens": 20},
        }},
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                      "cache_read_input_tokens": 0, "output_tokens": 20},
        }},
        {"type": "result", "subtype": "success", "total_cost_usd": 0.05},
    )
    result = parse_ndjson_spending(ndjson)
    # total_cost_usd from result is authoritative
    assert result.total_cost_usd == 0.05
    # But token totals come from dedup (no usage in result)
    assert result.total_input == 10
    assert result.total_cache_write == 100
    assert result.total_output == 20


def test_message_with_zero_usage_and_id() -> None:
    """A message with ID but all-zero usage should still be tracked in msg_maxes."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_zero",
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": 0, "output_tokens": 0},
        }},
        {"type": "assistant", "message": {
            "id": "msg_nonzero",
            "model": "claude-sonnet-4-6",
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 50,
                      "cache_read_input_tokens": 0, "output_tokens": 5},
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # msg_zero contributes 0; msg_nonzero contributes its values
    assert result.total_input == 10
    assert result.total_cache_write == 50
    assert result.total_output == 5

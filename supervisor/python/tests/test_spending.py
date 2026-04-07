"""Tests for the NDJSON spending parser."""

from __future__ import annotations

import json

from aquarco_supervisor.spending import (
    SpendingSummary,
    TurnSpending,
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

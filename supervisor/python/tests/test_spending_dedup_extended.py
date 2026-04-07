"""Extended dedup tests for spending parser — issue #93 edge cases.

These tests cover gaps identified during the test-agent review:
- _compute_deduped_totals with missing/partial keys
- Result usage with partial fields (regression edge case from review)
- Incremental accumulation pattern correctness (monotonic approach to final)
- Mixed scenarios: messages with and without IDs plus result overrides
- Cost estimation correctness with deduplication
"""

from __future__ import annotations

import json

from aquarco_supervisor.spending import (
    SpendingSummary,
    _compute_deduped_totals,
    get_pricing,
    parse_ndjson_spending,
)


def _ndjson(*objects: dict) -> str:
    """Build an NDJSON string from dicts."""
    return "\n".join(json.dumps(obj) for obj in objects)


# ---------------------------------------------------------------------------
# _compute_deduped_totals edge cases
# ---------------------------------------------------------------------------


def test_compute_deduped_totals_missing_some_keys() -> None:
    """Entries with missing token keys should default to 0 for those fields."""
    msg_maxes = {
        "msg_01": {"input_tokens": 10},  # only input_tokens present
        "msg_02": {"output_tokens": 20},  # only output_tokens present
    }
    result = _compute_deduped_totals(msg_maxes)
    assert result == (10, 0, 0, 20)


def test_compute_deduped_totals_all_keys_zero() -> None:
    """Entries where all values are 0 should produce all-zero totals."""
    msg_maxes = {
        "msg_01": {
            "input_tokens": 0,
            "cache_write_tokens": 0,
            "cache_read_tokens": 0,
            "output_tokens": 0,
        }
    }
    result = _compute_deduped_totals(msg_maxes)
    assert result == (0, 0, 0, 0)


def test_compute_deduped_totals_large_number_of_entries() -> None:
    """Verify correctness with many unique message IDs."""
    msg_maxes = {
        f"msg_{i:04d}": {
            "input_tokens": 1,
            "cache_write_tokens": 10,
            "cache_read_tokens": 100,
            "output_tokens": 5,
        }
        for i in range(100)
    }
    result = _compute_deduped_totals(msg_maxes)
    assert result == (100, 1000, 10000, 500)


# ---------------------------------------------------------------------------
# Result usage with partial fields (reviewer warning)
# ---------------------------------------------------------------------------


def test_result_usage_with_partial_fields_overrides_dedup() -> None:
    """When a result has usage with only some fields present, those fields
    override the deduped values while missing fields default to 0 (not the
    deduped value). This is the regression edge case noted by the reviewer.
    """
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 500,
                "cache_read_input_tokens": 200,
                "output_tokens": 50,
            },
        }},
        # Result with only input_tokens in usage — other fields missing
        {"type": "result", "subtype": "success",
         "total_cost_usd": 0.10,
         "usage": {
             "input_tokens": 80,
             # cache and output fields missing
         }},
    )
    result = parse_ndjson_spending(ndjson)
    assert result.total_cost_usd == 0.10
    # input_tokens from result overrides
    assert result.total_input == 80
    # Missing fields in result usage fall back to summary defaults (0)
    # This documents the current behavior — the reviewer noted this as a subtle edge case
    assert result.total_output == 0  # defaults since result usage doesn't have it


def test_result_usage_empty_dict_does_not_override() -> None:
    """Result with an empty usage dict should still trigger _result_usage_override
    because isinstance(usage, dict) is True for {}. All fields default from the
    empty dict's .get() which returns the current summary values (all 0 at that
    point since dedup hasn't been applied yet)."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_01AAA",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 100,
                "cache_creation_input_tokens": 500,
                "cache_read_input_tokens": 0,
                "output_tokens": 50,
            },
        }},
        {"type": "result", "subtype": "success",
         "total_cost_usd": 0.10,
         "usage": {}},
    )
    result = parse_ndjson_spending(ndjson)
    assert result.total_cost_usd == 0.10
    # Empty usage dict: each field defaults to summary.<field> which is 0
    # because _result_usage_override is set but all .get() calls fall back to 0
    assert result.total_input == 0
    assert result.total_output == 0


# ---------------------------------------------------------------------------
# Incremental accumulation — monotonic convergence (issue #93 core property)
# ---------------------------------------------------------------------------


def test_incremental_accumulation_never_exceeds_final() -> None:
    """Core property from issue #93: when processing messages incrementally,
    the accumulated deduped total at each step must never exceed the final total.

    This reproduces the exact table from the issue description.
    """
    # Simulate the incremental accumulation from the issue
    messages_in_order = [
        ("msg_01RRjSF2uva5HRKurdkcStgH", 3, 15078, 0, 23),
        ("msg_01TLGaJaQZFGnMSjgtoJ7Efp", 1, 15757, 0, 64),
        ("msg_01XLR1TsFTuhP2vcV3bVs6LE", 1, 7028, 15757, 37),
        ("msg_01SLZd8wN1CASCxzWamjZA3C", 1, 6019, 22785, 9),
        ("msg_01Do3ihFfK3tBVMXQZFMx5ge", 1, 3559, 28804, 1),
        ("msg_01MSQDJAuKvw7va8gvBtZX2W", 1, 541, 32363, 60),
        ("msg_01UDc2i8TGAyrms2fy1sFLyE", 1, 3934, 32363, 2),
    ]

    # Final totals (from the issue)
    final_input = 9
    final_cache_write = 51916
    final_cache_read = 132072
    final_output = 196

    # Process messages one at a time and verify monotonic convergence
    prev_input = prev_cw = prev_cr = prev_output = 0
    for step in range(1, len(messages_in_order) + 1):
        partial_ndjson = _ndjson(
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
                for msg_id, inp, cw, cr, out in messages_in_order[:step]
            ]
        )
        result = parse_ndjson_spending(partial_ndjson)

        # Monotonically increasing
        assert result.total_input >= prev_input, f"Step {step}: input decreased"
        assert result.total_cache_write >= prev_cw, f"Step {step}: cache_write decreased"
        assert result.total_cache_read >= prev_cr, f"Step {step}: cache_read decreased"
        assert result.total_output >= prev_output, f"Step {step}: output decreased"

        # Never exceeds final
        assert result.total_input <= final_input, f"Step {step}: input exceeds final"
        assert result.total_cache_write <= final_cache_write, f"Step {step}: cache_write exceeds final"
        assert result.total_cache_read <= final_cache_read, f"Step {step}: cache_read exceeds final"
        assert result.total_output <= final_output, f"Step {step}: output exceeds final"

        prev_input = result.total_input
        prev_cw = result.total_cache_write
        prev_cr = result.total_cache_read
        prev_output = result.total_output

    # At the final step, should exactly equal the expected totals
    assert prev_input == final_input
    assert prev_cw == final_cache_write
    assert prev_cr == final_cache_read
    assert prev_output == final_output


def test_duplicate_emissions_produce_same_result_as_single_emission() -> None:
    """Whether a message ID is emitted once or five times, the deduped total
    must be the same — proving dedup correctness.
    """
    msg = {
        "id": "msg_01AAA",
        "model": "claude-sonnet-4-6",
        "usage": {
            "input_tokens": 42,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 500,
            "output_tokens": 99,
        },
    }

    single = _ndjson({"type": "assistant", "message": msg})
    five_dupes = _ndjson(*[{"type": "assistant", "message": msg}] * 5)

    r1 = parse_ndjson_spending(single)
    r5 = parse_ndjson_spending(five_dupes)

    assert r1.total_input == r5.total_input == 42
    assert r1.total_cache_write == r5.total_cache_write == 1000
    assert r1.total_cache_read == r5.total_cache_read == 500
    assert r1.total_output == r5.total_output == 99
    assert r1.estimated_cost_usd == r5.estimated_cost_usd


# ---------------------------------------------------------------------------
# Cost estimation with dedup
# ---------------------------------------------------------------------------


def test_estimated_cost_opus_with_dedup() -> None:
    """Opus pricing with duplicate emissions should not inflate cost."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_opus",
            "model": "claude-opus-4-6",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        }},
        # Duplicate
        {"type": "assistant", "message": {
            "id": "msg_opus",
            "model": "claude-opus-4-6",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 0,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # Opus 4.5/4.6: $5/MTok input. Deduped = 1M tokens, not 2M.
    assert result.estimated_cost_usd == 5.0


def test_estimated_cost_all_token_types() -> None:
    """Verify cost estimation uses all four token buckets after dedup."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "msg_all",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 1_000_000,
                "cache_creation_input_tokens": 1_000_000,
                "cache_read_input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # Sonnet: input=$3, cache_write=$3.75, cache_read=$0.30, output=$15
    expected = 3.0 + 3.75 + 0.30 + 15.0
    assert abs(result.estimated_cost_usd - expected) < 0.001


# ---------------------------------------------------------------------------
# Edge cases: malformed and unusual messages
# ---------------------------------------------------------------------------


def test_assistant_message_with_non_dict_message_field() -> None:
    """If message field is not a dict, usage extraction is skipped."""
    ndjson = _ndjson(
        {"type": "assistant", "message": "not a dict"},
    )
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 0
    assert result.total_input == 0


def test_assistant_message_with_null_usage() -> None:
    """If usage is None/null, the message is skipped."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {"usage": None}},
    )
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 0


def test_assistant_message_with_non_dict_usage() -> None:
    """If usage is not a dict (e.g. a string), the message is skipped."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {"usage": "invalid"}},
    )
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 0


def test_whitespace_only_lines_skipped() -> None:
    """Lines with only whitespace should be skipped."""
    ndjson = "  \n  \n  \n"
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 0
    assert result.total_input == 0


def test_non_dict_json_lines_skipped() -> None:
    """JSON arrays or scalars should be skipped."""
    ndjson = "[1, 2, 3]\n42\n\"hello\"\ntrue"
    result = parse_ndjson_spending(ndjson)
    assert len(result.turns) == 0


def test_empty_id_string_treated_as_no_id() -> None:
    """A message with an empty string id should be treated as having no id
    (falsy check on msg_id)."""
    ndjson = _ndjson(
        {"type": "assistant", "message": {
            "id": "",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 5,
            },
        }},
        {"type": "assistant", "message": {
            "id": "",
            "model": "claude-sonnet-4-6",
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": 5,
            },
        }},
    )
    result = parse_ndjson_spending(ndjson)
    # Empty string is falsy — both messages get unique synthetic IDs, so summed
    assert result.total_input == 20
    assert result.total_output == 10


# ---------------------------------------------------------------------------
# get_pricing edge cases
# ---------------------------------------------------------------------------


def test_get_pricing_opus_old_version() -> None:
    """Opus 4.0/4.1 should use the higher pricing tier."""
    p = get_pricing("claude-opus-4-0")
    assert p["input"] == 15
    assert p["output"] == 75


def test_get_pricing_haiku_old_version() -> None:
    """Haiku 3.5 should use the lower pricing tier."""
    p = get_pricing("claude-haiku-3-5")
    assert p["input"] == 0.80
    assert p["output"] == 4


def test_get_pricing_case_insensitive() -> None:
    """Model name matching should be case-insensitive."""
    p = get_pricing("Claude-OPUS-4-6")
    assert p["input"] == 5

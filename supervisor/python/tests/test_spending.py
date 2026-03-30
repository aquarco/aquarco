"""Tests for the NDJSON spending parser."""

from __future__ import annotations

import json

from aquarco_supervisor.spending import (
    SpendingSummary,
    TurnSpending,
    parse_ndjson_spending,
    _get_pricing,
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


def test_get_pricing_opus() -> None:
    p = _get_pricing("claude-opus-4-6")
    assert p["input"] == 5
    assert p["output"] == 25


def test_get_pricing_sonnet_default() -> None:
    p = _get_pricing("claude-sonnet-4-6")
    assert p["input"] == 3
    assert p["output"] == 15


def test_get_pricing_haiku() -> None:
    p = _get_pricing("claude-haiku-4-5")
    assert p["input"] == 1
    assert p["output"] == 5


def test_get_pricing_unknown_defaults_to_sonnet() -> None:
    p = _get_pricing("unknown-model")
    assert p["input"] == 3

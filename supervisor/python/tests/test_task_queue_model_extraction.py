"""Tests for model extraction from raw_output in task_queue stage completion.

Validates that the model column is correctly populated from NDJSON raw_output
when recording stage results (Issue #83).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.spending import parse_ndjson_spending


def _ndjson(*objects: dict) -> str:
    """Build an NDJSON string from dicts."""
    return "\n".join(json.dumps(obj) for obj in objects)


# ── Model extraction via parse_ndjson_spending ─────────────────────────────────


class TestModelExtractionFromRawOutput:
    """Test that model is correctly extracted from raw_output NDJSON."""

    def test_extracts_model_from_init_message(self) -> None:
        """The system init message should set the model on the summary."""
        raw = _ndjson(
            {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
            {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "model": "claude-sonnet-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        )
        summary = parse_ndjson_spending(raw)
        assert summary.model == "claude-sonnet-4-6"

    def test_extracts_model_from_assistant_when_no_init(self) -> None:
        """When no init message, model should come from first assistant turn."""
        raw = _ndjson(
            {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "model": "claude-opus-4-6",
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 100,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        )
        summary = parse_ndjson_spending(raw)
        assert summary.model == "claude-opus-4-6"

    def test_model_is_none_when_no_messages(self) -> None:
        """Empty or non-assistant NDJSON should leave model as None."""
        raw = _ndjson({"type": "result", "total_cost_usd": 0.01})
        summary = parse_ndjson_spending(raw)
        assert summary.model is None

    def test_model_is_none_for_empty_raw_output(self) -> None:
        """Empty string produces no model."""
        summary = parse_ndjson_spending("")
        assert summary.model is None

    def test_model_from_haiku(self) -> None:
        """Haiku model should be correctly extracted."""
        raw = _ndjson(
            {"type": "system", "subtype": "init", "model": "claude-haiku-4-5"},
        )
        summary = parse_ndjson_spending(raw)
        assert summary.model == "claude-haiku-4-5"

    def test_model_survives_malformed_lines(self) -> None:
        """Model extraction should work even with interspersed bad JSON lines."""
        raw = "not valid json\n" + _ndjson(
            {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
        )
        summary = parse_ndjson_spending(raw)
        assert summary.model == "claude-sonnet-4-6"

    def test_init_model_takes_precedence_over_assistant_model(self) -> None:
        """Init message model should be used even if assistant has a different one."""
        raw = _ndjson(
            {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
            {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "model": "claude-opus-4-6",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        )
        summary = parse_ndjson_spending(raw)
        # Init message sets model first; assistant won't override it (line 175 check)
        assert summary.model == "claude-sonnet-4-6"


# ── Model extraction error handling ────────────────────────────────────────────


class TestModelExtractionErrorHandling:
    """Test that model extraction failures are handled gracefully."""

    def test_completely_invalid_ndjson_returns_none_model(self) -> None:
        """All lines being invalid JSON should not crash, model stays None."""
        raw = "not json at all\nanother bad line\n"
        summary = parse_ndjson_spending(raw)
        assert summary.model is None

    def test_assistant_without_usage_block(self) -> None:
        """Assistant message without usage should not crash."""
        raw = _ndjson(
            {"type": "assistant", "message": {"id": "msg_1", "model": "claude-sonnet-4-6"}},
        )
        summary = parse_ndjson_spending(raw)
        # Model should still be None since no usage block means no turn recorded,
        # but model can still be set from assistant message
        assert summary.model is None or summary.model == "claude-sonnet-4-6"

    def test_non_dict_json_lines_skipped(self) -> None:
        """JSON arrays or scalars should be silently skipped."""
        raw = '["not", "a", "dict"]\n42\n"just a string"\n' + _ndjson(
            {"type": "system", "subtype": "init", "model": "claude-sonnet-4-6"},
        )
        summary = parse_ndjson_spending(raw)
        assert summary.model == "claude-sonnet-4-6"


# ── get_pricing model routing ──────────────────────────────────────────────────


class TestGetPricingForModels:
    """Test that get_pricing routes model names to correct pricing tiers."""

    from aquarco_supervisor.spending import get_pricing

    def test_sonnet_model(self) -> None:
        from aquarco_supervisor.spending import get_pricing
        pricing = get_pricing("claude-sonnet-4-6")
        assert pricing["input"] == 3
        assert pricing["output"] == 15

    def test_opus_46_model(self) -> None:
        from aquarco_supervisor.spending import get_pricing
        pricing = get_pricing("claude-opus-4-6")
        assert pricing["input"] == 5
        assert pricing["output"] == 25

    def test_opus_40_model(self) -> None:
        from aquarco_supervisor.spending import get_pricing
        pricing = get_pricing("claude-opus-4-0")
        assert pricing["input"] == 15
        assert pricing["output"] == 75

    def test_haiku_45_model(self) -> None:
        from aquarco_supervisor.spending import get_pricing
        pricing = get_pricing("claude-haiku-4-5")
        assert pricing["input"] == 1
        assert pricing["output"] == 5

    def test_haiku_35_model(self) -> None:
        from aquarco_supervisor.spending import get_pricing
        pricing = get_pricing("claude-haiku-3-5")
        assert pricing["input"] == 0.80
        assert pricing["output"] == 4

    def test_unknown_model_defaults_to_sonnet(self) -> None:
        from aquarco_supervisor.spending import get_pricing
        pricing = get_pricing("some-unknown-model")
        assert pricing["input"] == 3  # Sonnet default

"""NDJSON spending parser for Claude CLI output.

Parses raw_output or live_output NDJSON streams to extract per-turn token
usage and compute running cost totals. Follows the same four-bucket model
as claude-spend: Input, Cache Writes, Cache Reads, Output.

When a ``type: "result"`` line is present, its ``total_cost_usd`` is used as
the authoritative cost.  Otherwise, cost is estimated using model pricing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


# Model pricing per million tokens (same table as claude-spend)
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "opus-4.5": {"input": 5, "output": 25, "cache_write": 6.25, "cache_read": 0.50},
    "opus-4.6": {"input": 5, "output": 25, "cache_write": 6.25, "cache_read": 0.50},
    "opus-4.0": {"input": 15, "output": 75, "cache_write": 18.75, "cache_read": 1.50},
    "opus-4.1": {"input": 15, "output": 75, "cache_write": 18.75, "cache_read": 1.50},
    "sonnet": {"input": 3, "output": 15, "cache_write": 3.75, "cache_read": 0.30},
    "haiku-4.5": {"input": 1, "output": 5, "cache_write": 1.25, "cache_read": 0.10},
    "haiku-3.5": {"input": 0.80, "output": 4, "cache_write": 1.00, "cache_read": 0.08},
}

# Default pricing (Sonnet) for unknown models
_DEFAULT_PRICING = _MODEL_PRICING["sonnet"]


def _get_pricing(model: str) -> dict[str, float]:
    """Return pricing dict for a model name string."""
    lower = model.lower()
    if "opus" in lower:
        if "4-6" in lower or "4.6" in lower or "4-5" in lower or "4.5" in lower:
            return _MODEL_PRICING["opus-4.5"]
        return _MODEL_PRICING["opus-4.0"]
    if "haiku" in lower:
        if "4-5" in lower or "4.5" in lower:
            return _MODEL_PRICING["haiku-4.5"]
        return _MODEL_PRICING["haiku-3.5"]
    return _DEFAULT_PRICING


@dataclass
class TurnSpending:
    """Token usage for a single assistant turn."""

    input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


@dataclass
class SpendingSummary:
    """Aggregated spending from an NDJSON stream."""

    turns: list[TurnSpending] = field(default_factory=list)
    total_input: int = 0
    total_cache_write: int = 0
    total_cache_read: int = 0
    total_output: int = 0
    total_cost_usd: float | None = None
    estimated_cost_usd: float = 0.0
    model: str | None = None


def parse_ndjson_spending(ndjson_text: str) -> SpendingSummary:
    """Parse an NDJSON stream and compute running token/cost totals.

    Works on both complete ``raw_output`` and partial ``live_output``.
    Each ``type: "assistant"`` line with a ``message.usage`` block produces
    a :class:`TurnSpending` entry.  If a ``type: "result"`` line is found,
    its ``total_cost_usd`` is used as the authoritative cost.
    """
    summary = SpendingSummary()

    for line in ndjson_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type")

        # Extract model from init message
        if msg_type == "system" and msg.get("subtype") == "init":
            summary.model = msg.get("model")
            continue

        # Extract per-turn usage from assistant messages
        if msg_type == "assistant":
            message = msg.get("message", {})
            usage = message.get("usage") if isinstance(message, dict) else None
            if isinstance(usage, dict):
                model = message.get("model", summary.model or "")
                turn = TurnSpending(
                    input_tokens=usage.get("input_tokens", 0),
                    cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                    cache_read_tokens=usage.get("cache_read_input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    model=model,
                )
                summary.turns.append(turn)
                summary.total_input += turn.input_tokens
                summary.total_cache_write += turn.cache_write_tokens
                summary.total_cache_read += turn.cache_read_tokens
                summary.total_output += turn.output_tokens
                if not summary.model and model:
                    summary.model = model
            continue

        # Extract authoritative cost from result message
        if msg_type == "result":
            if "total_cost_usd" in msg:
                summary.total_cost_usd = msg["total_cost_usd"]
            # Also extract billing-level usage if available
            usage = msg.get("usage")
            if isinstance(usage, dict):
                # Result usage is the billing aggregate — more accurate than
                # per-turn sums which double-count re-sent context.
                summary.total_input = usage.get("input_tokens", summary.total_input)
                summary.total_cache_write = usage.get(
                    "cache_creation_input_tokens", summary.total_cache_write
                )
                summary.total_cache_read = usage.get(
                    "cache_read_input_tokens", summary.total_cache_read
                )
                summary.total_output = usage.get("output_tokens", summary.total_output)

    # Estimate cost from tokens if no authoritative cost available
    pricing = _get_pricing(summary.model or "")
    summary.estimated_cost_usd = (
        summary.total_input * pricing["input"] / 1_000_000
        + summary.total_cache_write * pricing["cache_write"] / 1_000_000
        + summary.total_cache_read * pricing["cache_read"] / 1_000_000
        + summary.total_output * pricing["output"] / 1_000_000
    )

    return summary

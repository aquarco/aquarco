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


def get_pricing(model: str) -> dict[str, float]:
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


def _compute_deduped_totals(
    msg_maxes: dict[str, dict[str, int]],
) -> tuple[int, int, int, int]:
    """Sum the per-message-id max values across all unique IDs.

    Returns (total_input, total_cache_write, total_cache_read, total_output).
    """
    total_input = 0
    total_cache_write = 0
    total_cache_read = 0
    total_output = 0
    for maxes in msg_maxes.values():
        total_input += maxes.get("input_tokens", 0)
        total_cache_write += maxes.get("cache_write_tokens", 0)
        total_cache_read += maxes.get("cache_read_tokens", 0)
        total_output += maxes.get("output_tokens", 0)
    return total_input, total_cache_write, total_cache_read, total_output


def parse_ndjson_spending(ndjson_text: str) -> SpendingSummary:
    """Parse an NDJSON stream and compute running token/cost totals.

    Works on both complete ``raw_output`` and partial ``live_output``.
    Each ``type: "assistant"`` line with a ``message.usage`` block produces
    a :class:`TurnSpending` entry.  If a ``type: "result"`` line is found,
    its ``total_cost_usd`` is used as the authoritative cost.

    Deduplication: the Claude CLI streaming protocol may emit the same
    ``message.id`` multiple times with cumulative (not delta) token counts.
    This function takes the maximum value per token field per unique
    ``message.id``, then sums across all unique IDs — equivalent to
    ``SELECT message_id, MAX(input_tokens), … GROUP BY message_id``.
    Messages without an ``id`` field are treated as having a unique
    synthetic ID so their tokens are always added.
    """
    summary = SpendingSummary()

    # Track per-message-id max token values for deduplication.
    # Key: message.id (or synthetic unique key), Value: dict of max token fields.
    msg_maxes: dict[str, dict[str, int]] = {}
    _synthetic_counter = 0
    _result_usage_override = False

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
                input_tokens = usage.get("input_tokens", 0)
                cache_write_tokens = usage.get("cache_creation_input_tokens", 0)
                cache_read_tokens = usage.get("cache_read_input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)

                # Determine message ID for deduplication
                msg_id = message.get("id") if isinstance(message, dict) else None
                if not msg_id:
                    # No message ID — treat as unique
                    _synthetic_counter += 1
                    msg_id = f"_synthetic_{_synthetic_counter}"

                if msg_id in msg_maxes:
                    # Update to max of current vs previously seen values
                    prev = msg_maxes[msg_id]
                    prev["input_tokens"] = max(prev["input_tokens"], input_tokens)
                    prev["cache_write_tokens"] = max(prev["cache_write_tokens"], cache_write_tokens)
                    prev["cache_read_tokens"] = max(prev["cache_read_tokens"], cache_read_tokens)
                    prev["output_tokens"] = max(prev["output_tokens"], output_tokens)
                else:
                    msg_maxes[msg_id] = {
                        "input_tokens": input_tokens,
                        "cache_write_tokens": cache_write_tokens,
                        "cache_read_tokens": cache_read_tokens,
                        "output_tokens": output_tokens,
                    }

                turn = TurnSpending(
                    input_tokens=input_tokens,
                    cache_write_tokens=cache_write_tokens,
                    cache_read_tokens=cache_read_tokens,
                    output_tokens=output_tokens,
                    model=model,
                )
                summary.turns.append(turn)
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
                _result_usage_override = True
                summary.total_input = usage.get("input_tokens", summary.total_input)
                summary.total_cache_write = usage.get(
                    "cache_creation_input_tokens", summary.total_cache_write
                )
                summary.total_cache_read = usage.get(
                    "cache_read_input_tokens", summary.total_cache_read
                )
                summary.total_output = usage.get("output_tokens", summary.total_output)

    # Compute deduped totals from per-message-id max values.
    # Only apply if result message didn't provide authoritative usage.
    if not _result_usage_override:
        deduped = _compute_deduped_totals(msg_maxes)
        summary.total_input = deduped[0]
        summary.total_cache_write = deduped[1]
        summary.total_cache_read = deduped[2]
        summary.total_output = deduped[3]

    # Estimate cost from tokens if no authoritative cost available
    pricing = get_pricing(summary.model or "")
    summary.estimated_cost_usd = (
        summary.total_input * pricing["input"] / 1_000_000
        + summary.total_cache_write * pricing["cache_write"] / 1_000_000
        + summary.total_cache_read * pricing["cache_read"] / 1_000_000
        + summary.total_output * pricing["output"] / 1_000_000
    )

    return summary

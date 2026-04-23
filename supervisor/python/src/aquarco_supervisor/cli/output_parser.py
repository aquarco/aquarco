"""Pure parsing and error-detection functions for Claude CLI NDJSON output.

These functions are stateless and have no subprocess dependencies.  They
operate on in-memory strings/lists and are safe to call from any context.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# NDJSON parsing
# ---------------------------------------------------------------------------


def _parse_ndjson_output(lines: list[str], task_id: str, stage_num: int) -> dict[str, Any]:
    """Parse NDJSON stream lines and extract structured result.

    Iterates lines, JSON-parses each, finds the first {type: 'result'} event,
    and delegates to _extract_from_result_message(). Falls back to extracting
    JSON from assistant text blocks if no result line is found.
    """
    if not lines:
        return {"_no_structured_output": True}

    messages: list[Any] = []
    for line in lines:
        try:
            msg = json.loads(line)
            messages.append(msg)
        except json.JSONDecodeError:
            continue

    # Find the result message
    result_msg = _find_result_message(messages)

    # Prefer structured_output from result event (non-verbose mode)
    if result_msg and (result_msg.get("structured_output") or result_msg.get("result")):
        extracted = _extract_from_result_message(result_msg)
        if not extracted.get("_no_structured_output"):
            return extracted

    # Fallback 1: look for StructuredOutput tool_use in assistant messages
    # (with --verbose + --json-schema, the structured output is delivered as
    # a StructuredOutput tool call, not in the result event)
    so_output = _extract_structured_output_tool_use(messages)
    if so_output is not None:
        # Merge execution metadata from result_msg if available
        if result_msg:
            meta = _extract_result_metadata(result_msg)
            so_output.update(meta)
        return so_output

    # Result message exists but has no structured data — extract what we can
    if result_msg:
        return _extract_from_result_message(result_msg)

    # Fallback 2: concatenate all assistant text blocks
    texts = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text", ""))
            elif isinstance(content, str):
                texts.append(content)
    result_text = "\n".join(texts)
    if result_text:
        structured = _extract_json(result_text)
        if structured is not None:
            return structured
    return {"_no_structured_output": True, "_result_text": result_text[:2000]}


def _parse_output(raw_output: str, task_id: str, stage_num: int) -> dict[str, Any]:
    """Parse Claude CLI JSON output and extract structured result.

    Kept for backward compatibility with tests. New code uses _parse_ndjson_output.
    """
    if not raw_output.strip():
        return {"_no_structured_output": True}

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        return {"_no_structured_output": True}

    if isinstance(parsed, list):
        result_msg = _find_result_message(parsed)
        if result_msg:
            return _extract_from_result_message(result_msg)

        texts = []
        for msg in parsed:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            texts.append(block.get("text", ""))
                elif isinstance(content, str):
                    texts.append(content)
        result_text = "\n".join(texts)
        if result_text:
            structured = _extract_json(result_text)
            if structured is not None:
                return structured
        return {"_no_structured_output": True, "_result_text": result_text[:2000]}

    return _extract_from_result_message(parsed)


# ---------------------------------------------------------------------------
# Result message helpers
# ---------------------------------------------------------------------------


def _find_result_message(messages: list[Any]) -> dict[str, Any] | None:
    """Find the result message in a Claude CLI message list."""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("type") == "result":
            return msg
    return None


def _extract_structured_output_tool_use(messages: list[Any]) -> dict[str, Any] | None:
    """Extract input from the last StructuredOutput tool_use in assistant messages.

    With --verbose + --json-schema, Claude delivers structured output via a
    StructuredOutput tool call rather than in the result event.
    """
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        # --verbose wraps assistant messages as {type: "assistant", message: {role: "assistant", content: [...]}}
        inner = msg.get("message", msg)
        if not isinstance(inner, dict):
            continue
        if inner.get("role") != "assistant":
            continue
        content = inner.get("content", [])
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "StructuredOutput"
            ):
                inp = block.get("input")
                if isinstance(inp, dict):
                    return dict(inp)
    return None


def _extract_result_metadata(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract execution metadata from a result message (prefixed with _)."""
    meta: dict[str, Any] = {}
    if "subtype" in msg:
        meta["_subtype"] = msg["subtype"]
    if "total_cost_usd" in msg:
        meta["_cost_usd"] = msg["total_cost_usd"]
    if "usage" in msg:
        usage = msg["usage"]
        if isinstance(usage, dict):
            meta["_input_tokens"] = usage.get("input_tokens", 0)
            meta["_cache_read_tokens"] = usage.get("cache_read_input_tokens", 0)
            meta["_cache_write_tokens"] = usage.get("cache_creation_input_tokens", 0)
            meta["_output_tokens"] = usage.get("output_tokens", 0)
    if "duration_ms" in msg:
        meta["_duration_ms"] = msg["duration_ms"]
    if "num_turns" in msg:
        meta["_num_turns"] = msg["num_turns"]
    if "session_id" in msg:
        meta["_session_id"] = msg["session_id"]
    return meta


def _extract_from_result_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Extract structured output and metadata from a Claude CLI result message."""
    output: dict[str, Any] = {}

    # 1. Prefer structured_output (from --json-schema)
    structured = msg.get("structured_output")
    if isinstance(structured, dict):
        output.update(structured)
    elif isinstance(structured, str):
        try:
            parsed_structured = json.loads(structured)
            if isinstance(parsed_structured, dict):
                output.update(parsed_structured)
        except json.JSONDecodeError:
            pass

    # 2. If no structured_output, try to extract JSON from result text
    if not output:
        result_text = msg.get("result", "")
        if result_text:
            extracted = _extract_json(result_text)
            if isinstance(extracted, dict):
                output.update(extracted)
            else:
                output["_no_structured_output"] = True
                output["_result_text"] = result_text[:2000]
        else:
            # No parseable structured data (empty/missing result, no
            # structured_output). Fall through so step 3 populates the
            # prefixed metadata keys (_subtype, _is_error, _cost_usd,
            # _session_id, etc.) — these are the authoritative keys that
            # every downstream consumer reads. This path is hit for
            # ``error_max_turns`` results where the CLI emits
            # ``result=""`` with no structured_output block.
            output["_no_structured_output"] = True

    # 3. Add execution metadata (prefixed with _ to avoid collisions)
    if "subtype" in msg:
        output["_subtype"] = msg["subtype"]
    if "total_cost_usd" in msg:
        output["_cost_usd"] = msg["total_cost_usd"]
    if "usage" in msg:
        usage = msg["usage"]
        if isinstance(usage, dict):
            output["_input_tokens"] = usage.get("input_tokens", 0)
            output["_cache_read_tokens"] = usage.get("cache_read_input_tokens", 0)
            output["_cache_write_tokens"] = usage.get("cache_creation_input_tokens", 0)
            output["_output_tokens"] = usage.get("output_tokens", 0)
    if "duration_ms" in msg:
        output["_duration_ms"] = msg["duration_ms"]
    if "num_turns" in msg:
        output["_num_turns"] = msg["num_turns"]
    if "session_id" in msg:
        output["_session_id"] = msg["session_id"]
    if "is_error" in msg:
        output["_is_error"] = msg["is_error"]

    return output


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract JSON from text, trying code blocks first then raw JSON."""
    match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("{") or line.startswith("["):
            try:
                result = json.loads(line)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue

    return None


# ---------------------------------------------------------------------------
# Session ID extraction
# ---------------------------------------------------------------------------


def _extract_session_id_from_lines(lines: list[str]) -> str | None:
    """Scan NDJSON lines for the last message containing a session_id.

    The session_id appears in result events and possibly init/system events.
    When the CLI exits non-zero (rate limit, server error), there may be no
    result event but earlier messages might still carry the session_id.
    """
    for line in reversed(lines):
        try:
            msg = json.loads(line)
            if isinstance(msg, dict) and "session_id" in msg:
                return msg["session_id"]
        except (json.JSONDecodeError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Error detection — in-memory line scanning
# ---------------------------------------------------------------------------


def _is_rate_limited_in_lines(lines: list[str]) -> bool:
    """Check whether any NDJSON stdout line contains rate-limit indicators."""
    for line in lines:
        lower = line.lower()
        if "rate_limit_error" in lower or "status code 429" in lower:
            return True
    return False


def _is_server_error_in_lines(lines: list[str]) -> bool:
    """Check whether any NDJSON stdout line contains HTTP 500 / api_error indicators.

    Uses quoted-string matching for ``"api_error"`` to avoid false positives when
    an agent's text output contains the phrase (e.g. while discussing error handling).
    The token only matches when it appears as a JSON string value surrounded by
    double-quote characters, which is how the Claude API encodes error types in its
    NDJSON stdout stream.

    The ``"status code 500"`` branch is unquoted and therefore more prone to false
    positives (e.g. an agent discussing HTTP error codes in prose).  This is an
    accepted trade-off: missing a real 500 error is worse than an occasional spurious
    retry; callers should expect this branch to fire rarely in practice.
    """
    for line in lines:
        lower = line.lower()
        if '"api_error"' in lower or "status code 500" in lower:
            return True
    return False


def _is_overloaded_in_lines(lines: list[str]) -> bool:
    """Check whether any NDJSON stdout line contains HTTP 529 / overloaded_error indicators."""
    for line in lines:
        lower = line.lower()
        if "overloaded_error" in lower or "status code 529" in lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Error detection — debug log file scanning
# ---------------------------------------------------------------------------


def _is_rate_limited(debug_log: Path) -> bool:
    """Check whether the Claude CLI debug log contains 429 rate-limit errors.

    Only reads the last 32 KB to avoid high memory usage on large debug logs.
    """
    try:
        size = debug_log.stat().st_size
        read_size = min(size, 32768)
        with open(debug_log, "rb") as f:
            if size > read_size:
                f.seek(size - read_size)
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return "rate_limit_error" in text or "status code 429" in text.lower()


def _is_server_error(debug_log: Path) -> bool:
    """Check whether the Claude CLI debug log contains HTTP 500 / api_error signals.

    Only reads the last 32 KB to avoid high memory usage on large debug logs.
    Uses quoted-string matching for ``"api_error"`` to avoid false positives from
    agent text transcripts in the debug log that discuss API error handling.
    """
    try:
        size = debug_log.stat().st_size
        read_size = min(size, 32768)
        with open(debug_log, "rb") as f:
            if size > read_size:
                f.seek(size - read_size)
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    lower = text.lower()
    return '"api_error"' in lower or "status code 500" in lower


def _is_overloaded(debug_log: Path) -> bool:
    """Check whether the Claude CLI debug log contains HTTP 529 / overloaded_error signals.

    Only reads the last 32 KB to avoid high memory usage on large debug logs.
    """
    try:
        size = debug_log.stat().st_size
        read_size = min(size, 32768)
        with open(debug_log, "rb") as f:
            if size > read_size:
                f.seek(size - read_size)
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    lower = text.lower()
    return "overloaded_error" in lower or "status code 529" in lower


# ---------------------------------------------------------------------------
# Schema prompt formatting
# ---------------------------------------------------------------------------


def _format_schema_prompt(schema: dict[str, Any]) -> str:
    """Format an outputSchema dict as a human-readable prompt section."""
    parts = [
        "## Output Format",
        "",
        "You MUST respond with a JSON object conforming to this schema:",
        "",
        "```json",
        json.dumps(schema, indent=2),
        "```",
    ]
    return "\n".join(parts)

"""Extended tests for Claude CLI – covering uncovered _parse_output paths."""

from __future__ import annotations

import json

from aquarco_supervisor.cli.claude import (
    ClaudeOutput,
    _extract_from_result_message,
    _extract_json,
    _find_result_message,
    _parse_output,
)


# --- _find_result_message ---


def test_find_result_message_returns_result_type() -> None:
    messages = [
        {"type": "assistant", "content": "hello"},
        {"type": "result", "result": "done"},
    ]
    msg = _find_result_message(messages)
    assert msg is not None
    assert msg["type"] == "result"


def test_find_result_message_returns_none_when_absent() -> None:
    messages = [
        {"type": "assistant", "content": "hello"},
        {"type": "system", "subtype": "init"},
    ]
    assert _find_result_message(messages) is None


def test_find_result_message_skips_non_dict_entries() -> None:
    messages = ["not a dict", 42, {"type": "result", "result": "ok"}]
    msg = _find_result_message(messages)
    assert msg is not None
    assert msg["result"] == "ok"


def test_find_result_message_empty_list() -> None:
    assert _find_result_message([]) is None


# --- _extract_from_result_message ---


def test_extract_from_result_message_structured_dict() -> None:
    msg = {
        "structured_output": {"key": "val"},
        "result": "text",
        "total_cost_usd": 0.5,
    }
    output = _extract_from_result_message(msg)
    assert output["key"] == "val"
    assert output["_cost_usd"] == 0.5


def test_extract_from_result_message_structured_string() -> None:
    msg = {
        "structured_output": '{"parsed": true}',
        "result": "text",
    }
    output = _extract_from_result_message(msg)
    assert output["parsed"] is True


def test_extract_from_result_message_structured_invalid_string() -> None:
    """Invalid JSON string in structured_output falls through to result text."""
    msg = {
        "structured_output": "not json at all",
        "result": '{"fallback": true}',
    }
    output = _extract_from_result_message(msg)
    assert output["fallback"] is True


def test_extract_from_result_message_no_structured_no_result() -> None:
    """When no structured_output and no result, returns the dict as-is."""
    msg = {"type": "result", "custom": "data"}
    output = _extract_from_result_message(msg)
    assert output == {"type": "result", "custom": "data"}


def test_extract_from_result_message_empty_result_no_structured() -> None:
    """Empty result string with no structured_output returns dict as-is."""
    msg = {"result": ""}
    output = _extract_from_result_message(msg)
    assert output == {"result": ""}


def test_extract_from_result_message_no_structured_plain_text_result() -> None:
    """Plain text result without structured_output -> _no_structured_output."""
    msg = {"result": "Just plain text, no JSON."}
    output = _extract_from_result_message(msg)
    assert output["_no_structured_output"] is True
    assert output["_result_text"] == "Just plain text, no JSON."


def test_extract_from_result_message_no_structured_empty_structured() -> None:
    """When structured_output is None and result has no JSON."""
    msg = {
        "structured_output": None,
        "result": "no json here",
    }
    output = _extract_from_result_message(msg)
    assert output["_no_structured_output"] is True


def test_extract_from_result_message_usage_metadata() -> None:
    msg = {
        "structured_output": {"ok": True},
        "total_cost_usd": 1.23,
        "usage": {
            "input_tokens": 100,
            "cache_read_input_tokens": 200,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
        },
        "duration_ms": 3000,
        "num_turns": 5,
        "session_id": "sess-abc",
    }
    output = _extract_from_result_message(msg)
    assert output["_cost_usd"] == 1.23
    assert output["_input_tokens"] == 100
    assert output["_cache_read_tokens"] == 200
    assert output["_output_tokens"] == 50
    assert output["_cache_write_tokens"] == 10
    assert output["_duration_ms"] == 3000
    assert output["_num_turns"] == 5
    assert output["_session_id"] == "sess-abc"


def test_extract_from_result_message_usage_non_dict_ignored() -> None:
    """Non-dict usage value is ignored."""
    msg = {
        "structured_output": {"ok": True},
        "usage": "not a dict",
    }
    output = _extract_from_result_message(msg)
    assert "_input_tokens" not in output


# --- _parse_output: list format fallback (no result message) ---


def test_parse_output_list_no_result_message_extracts_assistant_text() -> None:
    """When list has no result message, concatenate assistant text blocks."""
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": '{"status": "ok"}'}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["status"] == "ok"


def test_parse_output_list_no_result_message_string_content() -> None:
    """Assistant message with string content (not list)."""
    messages = [
        {"role": "assistant", "content": '{"data": 42}'},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["data"] == 42


def test_parse_output_list_no_result_message_plain_text_fallback() -> None:
    """When no result message and no JSON in assistant text."""
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "No JSON here"}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "No JSON here"


def test_parse_output_list_no_result_no_assistant() -> None:
    """List with no result and no assistant messages."""
    messages = [
        {"type": "system", "subtype": "init"},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_list_multiple_assistant_blocks_concatenated() -> None:
    """Multiple assistant messages are concatenated."""
    messages = [
        {"role": "assistant", "content": [{"type": "text", "text": "Part 1\n"}]},
        {"role": "assistant", "content": [{"type": "text", "text": '{"combined": true}'}]},
    ]
    result = _parse_output(json.dumps(messages), "task-001", 0)
    assert result["combined"] is True


# --- _parse_output: single dict format ---


def test_parse_output_single_dict_with_structured_output() -> None:
    """Single dict (non-list) with structured_output."""
    raw = json.dumps({
        "type": "result",
        "structured_output": {"verdict": "pass"},
        "total_cost_usd": 0.1,
    })
    result = _parse_output(raw, "task-001", 0)
    assert result["verdict"] == "pass"
    assert result["_cost_usd"] == 0.1


# --- ClaudeOutput ---


def test_claude_output_is_dataclass() -> None:
    output = ClaudeOutput(structured={"a": 1}, raw="raw text")
    assert output.structured == {"a": 1}
    assert output.raw == "raw text"


def test_claude_output_default_values() -> None:
    output = ClaudeOutput()
    assert output.structured == {}
    assert output.raw == ""


# --- _extract_json edge cases ---


def test_extract_json_empty_string() -> None:
    assert _extract_json("") is None


def test_extract_json_code_block_with_dict() -> None:
    text = '```json\n{"nested": {"key": [1, 2, 3]}}\n```'
    result = _extract_json(text)
    assert result == {"nested": {"key": [1, 2, 3]}}


def test_extract_json_multiple_code_blocks_takes_first() -> None:
    text = '```json\n{"first": true}\n```\n\n```json\n{"second": true}\n```'
    result = _extract_json(text)
    assert result == {"first": True}

"""Tests for Claude CLI wrapper."""

from __future__ import annotations

import json

from aquarco_supervisor.cli.claude import (
    ClaudeOutput,
    _extract_from_result_message,
    _extract_json,
    _find_result_message,
    _parse_output,
)


def test_extract_json_code_block() -> None:
    text = 'Some text\n```json\n{"result": "ok"}\n```\nMore text'
    result = _extract_json(text)
    assert result == {"result": "ok"}


def test_extract_json_raw_line() -> None:
    text = 'Some text\n{"result": "ok"}\nMore text'
    result = _extract_json(text)
    assert result == {"result": "ok"}


def test_extract_json_no_json() -> None:
    text = "Just plain text with no JSON"
    result = _extract_json(text)
    assert result is None


def test_extract_json_array_ignored() -> None:
    text = '[1, 2, 3]'
    result = _extract_json(text)
    assert result is None


def test_extract_json_invalid_json_line() -> None:
    text = '{"broken: json'
    result = _extract_json(text)
    assert result is None


def test_parse_output_empty() -> None:
    result = _parse_output("", "task-1", 0)
    assert result["_no_structured_output"] is True


def test_parse_output_valid_json() -> None:
    raw = json.dumps({"result": '```json\n{"summary": "done"}\n```'})
    result = _parse_output(raw, "task-1", 0)
    assert result == {"summary": "done"}


def test_parse_output_no_result_key() -> None:
    raw = json.dumps({"other_field": "value"})
    result = _parse_output(raw, "task-1", 0)
    assert result == {"other_field": "value"}


def test_parse_output_plain_text_result() -> None:
    raw = json.dumps({"result": "Just text, no JSON"})
    result = _parse_output(raw, "task-1", 0)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "Just text, no JSON"


def test_parse_output_invalid_json_string() -> None:
    result = _parse_output("not json at all", "task-1", 0)
    assert result["_no_structured_output"] is True


# ── _find_result_message tests ──────────────────────────────────────────


def test_find_result_message_returns_result_type() -> None:
    msgs = [
        {"type": "assistant", "content": "hi"},
        {"type": "result", "result": "done"},
    ]
    assert _find_result_message(msgs) == {"type": "result", "result": "done"}


def test_find_result_message_returns_none_when_missing() -> None:
    msgs = [{"type": "assistant"}]
    assert _find_result_message(msgs) is None


def test_find_result_message_ignores_non_dict_entries() -> None:
    msgs = ["string-entry", None, 42, {"type": "result", "result": "ok"}]
    assert _find_result_message(msgs) == {"type": "result", "result": "ok"}


def test_find_result_message_empty_list() -> None:
    assert _find_result_message([]) is None


# ── _extract_from_result_message tests ──────────────────────────────────


def test_extract_from_result_structured_output_dict() -> None:
    msg = {"structured_output": {"summary": "done"}, "result": "text"}
    result = _extract_from_result_message(msg)
    assert result["summary"] == "done"


def test_extract_from_result_structured_output_string() -> None:
    msg = {"structured_output": json.dumps({"key": "val"}), "result": "text"}
    result = _extract_from_result_message(msg)
    assert result["key"] == "val"


def test_extract_from_result_structured_output_invalid_json_string() -> None:
    """When structured_output is an invalid JSON string, falls back to result text."""
    msg = {"structured_output": "not-json", "result": '```json\n{"x": 1}\n```'}
    result = _extract_from_result_message(msg)
    assert result["x"] == 1


def test_extract_from_result_no_structured_no_result() -> None:
    """When there's no result and no structured_output, return the message as-is."""
    msg = {"type": "result", "duration_ms": 100}
    result = _extract_from_result_message(msg)
    assert result == msg


def test_extract_from_result_empty_result_no_structured() -> None:
    """When result is empty and no structured_output, return the message dict."""
    msg = {"result": "", "type": "result"}
    result = _extract_from_result_message(msg)
    # Empty result with no structured_output returns the raw msg dict
    assert result == msg


def test_extract_from_result_empty_result_with_falsy_structured() -> None:
    """When result is empty and structured_output is None, return raw msg."""
    msg = {"result": "", "structured_output": None}
    result = _extract_from_result_message(msg)
    # None structured_output is falsy, empty result is falsy → returns dict(msg)
    assert result == msg


def test_extract_from_result_no_output_but_result_key_present() -> None:
    """When structured_output is absent, result key is present but empty → _no_structured_output.

    This hits the else branch (line 246) when result key exists (so msg.get('result')
    returns '' which is falsy) but structured is truthy (e.g. a non-dict string that
    didn't parse).
    """
    # structured_output is a non-parseable string → structured var is set but
    # output remains empty because JSON parse fails.
    # Then result_text is "" (falsy), not msg.get("result") is True,
    # but not structured is False → hits else branch.
    msg = {"structured_output": "unparseable", "result": ""}
    result = _extract_from_result_message(msg)
    assert result["_no_structured_output"] is True


def test_extract_from_result_result_text_no_json() -> None:
    """When result text has no JSON, mark _result_text and _no_structured_output."""
    msg = {"result": "Just plain text answer"}
    result = _extract_from_result_message(msg)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "Just plain text answer"


def test_extract_from_result_result_text_with_json() -> None:
    """When result text contains embedded JSON, extract it."""
    msg = {"result": 'Here is the result:\n{"status": "ok"}'}
    result = _extract_from_result_message(msg)
    assert result["status"] == "ok"


def test_extract_from_result_metadata_extraction() -> None:
    """Execution metadata fields are extracted with _ prefix."""
    msg = {
        "structured_output": {"answer": 42},
        "result": "text",
        "total_cost_usd": 0.05,
        "usage": {
            "input_tokens": 100,
            "cache_read_input_tokens": 50,
            "output_tokens": 200,
            "cache_creation_input_tokens": 10,
        },
        "duration_ms": 5000,
        "num_turns": 3,
        "session_id": "sess-123",
    }
    result = _extract_from_result_message(msg)
    assert result["answer"] == 42
    assert result["_cost_usd"] == 0.05
    assert result["_input_tokens"] == 100
    assert result["_cache_read_tokens"] == 50
    assert result["_output_tokens"] == 200
    assert result["_cache_write_tokens"] == 10
    assert result["_duration_ms"] == 5000
    assert result["_num_turns"] == 3
    assert result["_session_id"] == "sess-123"


def test_extract_from_result_usage_non_dict_ignored() -> None:
    """When usage is not a dict, it's skipped."""
    msg = {"structured_output": {"a": 1}, "result": "x", "usage": "bad"}
    result = _extract_from_result_message(msg)
    assert "_input_tokens" not in result


def test_extract_from_result_result_text_truncated() -> None:
    """Long result text is truncated to 2000 chars."""
    long_text = "x" * 3000
    msg = {"result": long_text}
    result = _extract_from_result_message(msg)
    assert result["_no_structured_output"] is True
    assert len(result["_result_text"]) == 2000


# ── _parse_output list format tests ────────────────────────────────────


def test_parse_output_list_with_result_message() -> None:
    """List format with a result message delegates to _extract_from_result_message."""
    raw = json.dumps([
        {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"type": "result", "result": '```json\n{"status": "ok"}\n```'},
    ])
    result = _parse_output(raw, "task-1", 0)
    assert result["status"] == "ok"


def test_parse_output_list_without_result_extracts_text() -> None:
    """List format without result message concatenates assistant text."""
    raw = json.dumps([
        {"role": "assistant", "content": [{"type": "text", "text": '{"done": true}'}]},
    ])
    result = _parse_output(raw, "task-1", 0)
    assert result["done"] is True


def test_parse_output_list_assistant_string_content() -> None:
    """List format with string content (not list) for assistant."""
    raw = json.dumps([
        {"role": "assistant", "content": '{"val": 99}'},
    ])
    result = _parse_output(raw, "task-1", 0)
    assert result["val"] == 99


def test_parse_output_list_no_json_in_text() -> None:
    """List format with no extractable JSON returns _no_structured_output."""
    raw = json.dumps([
        {"role": "assistant", "content": [{"type": "text", "text": "plain text"}]},
    ])
    result = _parse_output(raw, "task-1", 0)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "plain text"


def test_parse_output_list_empty_messages() -> None:
    """List format with no assistant messages returns no structured output."""
    raw = json.dumps([{"role": "user", "content": "hi"}])
    result = _parse_output(raw, "task-1", 0)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == ""


def test_parse_output_whitespace_only() -> None:
    """Whitespace-only input treated as empty."""
    result = _parse_output("   \n  ", "task-1", 0)
    assert result["_no_structured_output"] is True


# ── ClaudeOutput dataclass tests ───────────────────────────────────────


def test_claude_output_defaults() -> None:
    output = ClaudeOutput()
    assert output.structured == {}
    assert output.raw == ""


def test_claude_output_with_values() -> None:
    output = ClaudeOutput(structured={"key": "val"}, raw="raw text")
    assert output.structured["key"] == "val"
    assert output.raw == "raw text"


# ── _extract_json additional edge cases ────────────────────────────────


def test_extract_json_invalid_code_block_falls_through() -> None:
    """Invalid JSON in code block falls through to line-by-line parsing."""
    text = '```json\n{invalid}\n```\n{"fallback": true}'
    result = _extract_json(text)
    assert result == {"fallback": True}


def test_extract_json_multiple_lines_first_dict_wins() -> None:
    """When multiple JSON lines exist, first dict wins."""
    text = '{"first": 1}\n{"second": 2}'
    result = _extract_json(text)
    assert result == {"first": 1}


def test_extract_json_array_line_skipped_dict_found() -> None:
    """Array lines are skipped, dict line is returned."""
    text = '[1,2,3]\n{"found": true}'
    result = _extract_json(text)
    assert result == {"found": True}


def test_extract_json_invalid_lines_skipped() -> None:
    """Invalid JSON lines are skipped until a valid dict line is found."""
    text = '{bad\n{worse\n{"good": true}'
    result = _extract_json(text)
    assert result == {"good": True}

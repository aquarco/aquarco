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
    import json
    raw = json.dumps({"result": '```json\n{"summary": "done"}\n```'})
    result = _parse_output(raw, "task-1", 0)
    assert result == {"summary": "done"}


def test_parse_output_no_result_key() -> None:
    import json
    raw = json.dumps({"other_field": "value"})
    result = _parse_output(raw, "task-1", 0)
    assert result == {"other_field": "value"}


def test_parse_output_plain_text_result() -> None:
    import json
    raw = json.dumps({"result": "Just text, no JSON"})
    result = _parse_output(raw, "task-1", 0)
    assert result["_no_structured_output"] is True
    assert result["_result_text"] == "Just text, no JSON"


def test_parse_output_invalid_json_string() -> None:
    result = _parse_output("not json at all", "task-1", 0)
    assert result["_no_structured_output"] is True

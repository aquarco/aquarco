"""Tests for cli.output_parser — pure NDJSON parsing functions."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from aquarco_supervisor.cli.output_parser import (
    _extract_from_result_message,
    _extract_json,
    _extract_result_metadata,
    _extract_session_id_from_lines,
    _extract_structured_output_tool_use,
    _find_result_message,
    _format_schema_prompt,
    _is_overloaded,
    _is_overloaded_in_lines,
    _is_rate_limited,
    _is_rate_limited_in_lines,
    _is_server_error,
    _is_server_error_in_lines,
    _parse_ndjson_output,
    _parse_output,
)


# -----------------------------------------------------------------------
# _find_result_message
# -----------------------------------------------------------------------


class TestFindResultMessage:
    def test_finds_result_in_list(self):
        msgs = [
            {"type": "assistant", "message": {}},
            {"type": "result", "subtype": "success"},
        ]
        assert _find_result_message(msgs) == {"type": "result", "subtype": "success"}

    def test_returns_none_when_absent(self):
        assert _find_result_message([{"type": "assistant"}]) is None

    def test_returns_none_for_empty_list(self):
        assert _find_result_message([]) is None

    def test_skips_non_dict(self):
        assert _find_result_message(["string", 42, None]) is None


# -----------------------------------------------------------------------
# _extract_json
# -----------------------------------------------------------------------


class TestExtractJson:
    def test_extracts_from_code_block(self):
        text = 'some text\n```json\n{"key": "value"}\n```\nmore text'
        assert _extract_json(text) == {"key": "value"}

    def test_extracts_from_raw_json_line(self):
        text = 'Hello\n{"answer": 42}'
        assert _extract_json(text) == {"answer": 42}

    def test_returns_none_for_no_json(self):
        assert _extract_json("just plain text") is None

    def test_returns_none_for_array_json(self):
        text = '[1, 2, 3]'
        assert _extract_json(text) is None

    def test_prefers_code_block_over_raw(self):
        text = '{"wrong": true}\n```json\n{"right": true}\n```'
        assert _extract_json(text) == {"right": True}


# -----------------------------------------------------------------------
# _extract_result_metadata
# -----------------------------------------------------------------------


class TestExtractResultMetadata:
    def test_extracts_all_fields(self):
        msg = {
            "subtype": "success",
            "total_cost_usd": 0.05,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
            },
            "duration_ms": 3000,
            "num_turns": 4,
            "session_id": "sess-123",
        }
        meta = _extract_result_metadata(msg)
        assert meta["_subtype"] == "success"
        assert meta["_cost_usd"] == 0.05
        assert meta["_input_tokens"] == 100
        assert meta["_output_tokens"] == 50
        assert meta["_cache_read_tokens"] == 10
        assert meta["_cache_write_tokens"] == 5
        assert meta["_duration_ms"] == 3000
        assert meta["_num_turns"] == 4
        assert meta["_session_id"] == "sess-123"

    def test_empty_message(self):
        assert _extract_result_metadata({}) == {}


# -----------------------------------------------------------------------
# _extract_from_result_message
# -----------------------------------------------------------------------


class TestExtractFromResultMessage:
    def test_with_structured_output_dict(self):
        msg = {
            "type": "result",
            "structured_output": {"summary": "done"},
            "subtype": "success",
        }
        result = _extract_from_result_message(msg)
        assert result["summary"] == "done"
        assert result["_subtype"] == "success"

    def test_with_structured_output_string(self):
        msg = {
            "type": "result",
            "structured_output": '{"count": 5}',
            "subtype": "success",
        }
        result = _extract_from_result_message(msg)
        assert result["count"] == 5

    def test_fallback_to_result_text(self):
        msg = {
            "type": "result",
            "result": '```json\n{"key": "val"}\n```',
            "subtype": "success",
        }
        result = _extract_from_result_message(msg)
        assert result["key"] == "val"

    def test_no_structured_data(self):
        msg = {"type": "result", "result": "plain text", "subtype": "success"}
        result = _extract_from_result_message(msg)
        assert result.get("_no_structured_output") is True

    def test_no_result_no_structured(self):
        """Event with no ``result`` and no ``structured_output`` must
        still produce the prefixed metadata contract. This is the
        shape of every ``error_max_turns`` result on the wire. See
        issue #165."""
        msg = {"type": "result", "subtype": "error_max_turns"}
        result = _extract_from_result_message(msg)
        assert result["_no_structured_output"] is True
        assert result["_subtype"] == "error_max_turns"


# -----------------------------------------------------------------------
# _extract_structured_output_tool_use
# -----------------------------------------------------------------------


class TestExtractStructuredOutputToolUse:
    def test_extracts_from_assistant_message(self):
        msgs = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "StructuredOutput",
                            "input": {"tests_passed": 5},
                        }
                    ],
                },
            }
        ]
        result = _extract_structured_output_tool_use(msgs)
        assert result == {"tests_passed": 5}

    def test_returns_none_without_tool_use(self):
        msgs = [{"type": "assistant", "message": {"role": "assistant", "content": []}}]
        assert _extract_structured_output_tool_use(msgs) is None

    def test_returns_none_for_empty(self):
        assert _extract_structured_output_tool_use([]) is None

    def test_ignores_other_tool_names(self):
        msgs = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Read", "input": {"path": "/a"}},
                    ],
                },
            }
        ]
        assert _extract_structured_output_tool_use(msgs) is None


# -----------------------------------------------------------------------
# _parse_ndjson_output
# -----------------------------------------------------------------------


class TestParseNdjsonOutput:
    def test_empty_lines(self):
        result = _parse_ndjson_output([], "task-1", 0)
        assert result == {"_no_structured_output": True}

    def test_with_result_event(self):
        lines = [
            json.dumps({"type": "assistant", "message": {}}),
            json.dumps({"type": "result", "structured_output": {"answer": 42}, "subtype": "success"}),
        ]
        result = _parse_ndjson_output(lines, "task-1", 0)
        assert result["answer"] == 42
        assert result["_subtype"] == "success"

    def test_invalid_json_lines_skipped(self):
        lines = [
            "not json",
            json.dumps({"type": "result", "structured_output": {"ok": True}, "subtype": "success"}),
        ]
        result = _parse_ndjson_output(lines, "task-1", 0)
        assert result["ok"] is True


# -----------------------------------------------------------------------
# _parse_output (backward compat)
# -----------------------------------------------------------------------


class TestParseOutput:
    def test_empty_string(self):
        result = _parse_output("", "task-1", 0)
        assert result.get("_no_structured_output") is True

    def test_invalid_json(self):
        result = _parse_output("not json at all", "task-1", 0)
        assert result.get("_no_structured_output") is True

    def test_list_with_result(self):
        data = [{"type": "result", "structured_output": {"x": 1}, "subtype": "success"}]
        result = _parse_output(json.dumps(data), "task-1", 0)
        assert result["x"] == 1


# -----------------------------------------------------------------------
# _extract_session_id_from_lines
# -----------------------------------------------------------------------


class TestExtractSessionId:
    def test_extracts_from_last_line(self):
        lines = [
            json.dumps({"session_id": "first"}),
            json.dumps({"session_id": "last"}),
        ]
        assert _extract_session_id_from_lines(lines) == "last"

    def test_returns_none_when_absent(self):
        lines = [json.dumps({"type": "assistant"})]
        assert _extract_session_id_from_lines(lines) is None

    def test_handles_invalid_json(self):
        lines = ["not json", json.dumps({"session_id": "sess-1"})]
        assert _extract_session_id_from_lines(lines) == "sess-1"

    def test_empty_lines(self):
        assert _extract_session_id_from_lines([]) is None


# -----------------------------------------------------------------------
# In-memory line-based error detection
# -----------------------------------------------------------------------


class TestInMemoryErrorDetection:
    def test_rate_limited_429(self):
        assert _is_rate_limited_in_lines(["status code 429"]) is True

    def test_rate_limited_error(self):
        assert _is_rate_limited_in_lines(["rate_limit_error"]) is True

    def test_not_rate_limited(self):
        assert _is_rate_limited_in_lines(["all good"]) is False

    def test_server_error_api_error(self):
        assert _is_server_error_in_lines(['"api_error"']) is True

    def test_server_error_500(self):
        assert _is_server_error_in_lines(["status code 500"]) is True

    def test_not_server_error(self):
        assert _is_server_error_in_lines(["all good"]) is False

    def test_overloaded_529(self):
        assert _is_overloaded_in_lines(["status code 529"]) is True

    def test_overloaded_error(self):
        assert _is_overloaded_in_lines(["overloaded_error"]) is True

    def test_not_overloaded(self):
        assert _is_overloaded_in_lines(["all good"]) is False


# -----------------------------------------------------------------------
# File-based error detection
# -----------------------------------------------------------------------


class TestFileBasedErrorDetection:
    def test_rate_limited_in_file(self, tmp_path):
        f = tmp_path / "debug.log"
        f.write_text("line1\nrate_limit_error\nline3")
        assert _is_rate_limited(f) is True

    def test_not_rate_limited_in_file(self, tmp_path):
        f = tmp_path / "debug.log"
        f.write_text("everything is fine")
        assert _is_rate_limited(f) is False

    def test_missing_file(self, tmp_path):
        assert _is_rate_limited(tmp_path / "nonexistent") is False

    def test_server_error_in_file(self, tmp_path):
        f = tmp_path / "debug.log"
        f.write_text('"api_error" detected')
        assert _is_server_error(f) is True

    def test_overloaded_in_file(self, tmp_path):
        f = tmp_path / "debug.log"
        f.write_text("overloaded_error received")
        assert _is_overloaded(f) is True

    def test_overloaded_missing_file(self, tmp_path):
        assert _is_overloaded(tmp_path / "nope") is False


# -----------------------------------------------------------------------
# _format_schema_prompt
# -----------------------------------------------------------------------


class TestFormatSchemaPrompt:
    def test_produces_markdown(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        result = _format_schema_prompt(schema)
        assert "## Output Format" in result
        assert "```json" in result
        assert '"type": "object"' in result

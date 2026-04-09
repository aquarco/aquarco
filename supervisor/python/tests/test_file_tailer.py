"""Tests for cli.file_tailer — file reading and rate-limit scanning helpers.

Covers:
- _read_file_tail: reads last N bytes, handles missing/empty files
- _scan_file_for_rate_limit_event: stream-scans NDJSON for rate_limit_event
- _tail_file: async NDJSON tailing (basic scenarios)
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from aquarco_supervisor.cli.file_tailer import (
    _read_file_tail,
    _scan_file_for_rate_limit_event,
    _RAW_OUTPUT_MAX_BYTES,
    _RATE_LIMIT_SCAN_CHUNK,
)


# -----------------------------------------------------------------------
# _read_file_tail
# -----------------------------------------------------------------------


class TestReadFileTail:
    def test_reads_small_file_completely(self, tmp_path: Path):
        f = tmp_path / "output.ndjson"
        content = "line1\nline2\nline3\n"
        f.write_text(content)
        assert _read_file_tail(f) == content

    def test_reads_last_n_bytes_of_large_file(self, tmp_path: Path):
        f = tmp_path / "large.ndjson"
        # Write more than max_bytes
        line = "x" * 1000 + "\n"
        f.write_text(line * 200)  # 200KB+
        result = _read_file_tail(f, max_bytes=2048)
        assert len(result) <= 2048

    def test_returns_empty_string_for_missing_file(self, tmp_path: Path):
        f = tmp_path / "nonexistent.ndjson"
        assert _read_file_tail(f) == ""

    def test_returns_empty_string_for_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.ndjson"
        f.write_text("")
        assert _read_file_tail(f) == ""

    def test_reads_exact_max_bytes(self, tmp_path: Path):
        f = tmp_path / "exact.ndjson"
        content = "a" * 100
        f.write_text(content)
        result = _read_file_tail(f, max_bytes=50)
        # Should read last 50 bytes
        assert len(result) == 50
        assert result == "a" * 50

    def test_default_max_bytes_is_128kb(self):
        assert _RAW_OUTPUT_MAX_BYTES == 131072

    def test_handles_binary_safe_utf8(self, tmp_path: Path):
        f = tmp_path / "utf8.ndjson"
        content = "hello \u00e9\u00e8\u00ea world\n"
        f.write_text(content, encoding="utf-8")
        result = _read_file_tail(f)
        assert "hello" in result
        assert "world" in result


# -----------------------------------------------------------------------
# _scan_file_for_rate_limit_event
# -----------------------------------------------------------------------


class TestScanFileForRateLimitEvent:
    def test_finds_rate_limit_event(self, tmp_path: Path):
        f = tmp_path / "output.ndjson"
        event = {"type": "rate_limit_event", "rate_limit_info": {"resetsAt": "2026-04-08T12:00:00Z"}}
        lines = [
            json.dumps({"type": "assistant", "message": {}}),
            json.dumps(event),
            json.dumps({"type": "result", "subtype": "success"}),
        ]
        f.write_text("\n".join(lines) + "\n")
        result = _scan_file_for_rate_limit_event(f)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["type"] == "rate_limit_event"
        assert parsed["rate_limit_info"]["resetsAt"] == "2026-04-08T12:00:00Z"

    def test_returns_none_when_no_rate_limit(self, tmp_path: Path):
        f = tmp_path / "output.ndjson"
        lines = [
            json.dumps({"type": "assistant", "message": {}}),
            json.dumps({"type": "result", "subtype": "success"}),
        ]
        f.write_text("\n".join(lines) + "\n")
        assert _scan_file_for_rate_limit_event(f) is None

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        f = tmp_path / "nonexistent.ndjson"
        assert _scan_file_for_rate_limit_event(f) is None

    def test_returns_none_for_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.ndjson"
        f.write_text("")
        assert _scan_file_for_rate_limit_event(f) is None

    def test_ignores_invalid_json_lines(self, tmp_path: Path):
        f = tmp_path / "output.ndjson"
        f.write_text("not json\n{broken\n")
        assert _scan_file_for_rate_limit_event(f) is None

    def test_finds_event_in_large_file(self, tmp_path: Path):
        """Rate limit event buried after many normal lines."""
        f = tmp_path / "large.ndjson"
        lines = [json.dumps({"type": "assistant", "content": f"msg-{i}"}) for i in range(500)]
        lines.append(json.dumps({"type": "rate_limit_event", "info": "limit"}))
        f.write_text("\n".join(lines) + "\n")
        result = _scan_file_for_rate_limit_event(f)
        assert result is not None
        assert "rate_limit_event" in result

    def test_ignores_rate_limit_text_in_non_matching_json(self, tmp_path: Path):
        """A line containing 'rate_limit_event' as text but with different type."""
        f = tmp_path / "tricky.ndjson"
        line = json.dumps({"type": "assistant", "message": "rate_limit_event happened"})
        f.write_text(line + "\n")
        assert _scan_file_for_rate_limit_event(f) is None

    def test_finds_event_in_remainder(self, tmp_path: Path):
        """Rate limit event on the last line without trailing newline."""
        f = tmp_path / "no_trailing.ndjson"
        event = json.dumps({"type": "rate_limit_event", "info": "limit"})
        f.write_text(json.dumps({"type": "assistant"}) + "\n" + event)
        result = _scan_file_for_rate_limit_event(f)
        assert result is not None

    def test_chunk_size_constant(self):
        assert _RATE_LIMIT_SCAN_CHUNK == 65536

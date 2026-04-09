"""File-based stdout tailing and raw output helpers for the Claude CLI.

These functions handle the real-time tailing of NDJSON output files written by
the Claude CLI subprocess, as well as utilities for reading and scanning those
files after execution completes.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..logging import get_logger

log = get_logger("claude-cli")

# Seconds to wait for the process to exit after the result event is seen.
_POST_RESULT_GRACE_SECONDS = 90.0

# How often the file tailer checks for new bytes.
_TAIL_POLL_INTERVAL = 0.5

# Maximum bytes to read from the NDJSON stdout file for raw_output (128 KB).
_RAW_OUTPUT_MAX_BYTES = 131072

# Chunk size for stream-scanning the NDJSON file (64 KB).
_RATE_LIMIT_SCAN_CHUNK = 65536


async def _tail_file(
    path: Path,
    proc: asyncio.subprocess.Process,
    *,
    on_live_output: Callable[[str], Awaitable[None]] | None = None,
    timeout_seconds: float,
    task_id: str = "",
    stage_num: int = 0,
) -> tuple[list[str], str | None, bool]:
    """Tail an NDJSON file written by Claude CLI until the process exits.

    Returns (tail_lines, result_line, result_seen).

    tail_lines is the last 50 lines seen (for error reporting and output
    parsing).  result_line is the exact raw JSON string of the first
    ``{type: "result"}`` event, captured when first seen so it is always
    available even if later lines push it out of the tail window.

    The loop runs until one of:
    1. The process exits (``proc.returncode is not None``).
    2. The overall *timeout_seconds* elapses → process is killed.
    3. The result event is seen and the process doesn't exit within
       ``_POST_RESULT_GRACE_SECONDS`` → process is terminated then killed.
    """
    tail: deque[str] = deque(maxlen=50)
    result_line: str | None = None
    offset = 0
    result_seen = False
    result_seen_at: float | None = None
    partial = ""  # leftover bytes that don't end with newline yet
    loop = asyncio.get_running_loop()
    start_time = loop.time()

    # Keep a persistent file handle to avoid repeated open/close syscalls
    # on every poll cycle.
    tail_fh = open(path, "rb")

    try:
        while True:
            if proc.returncode is not None:
                break

            elapsed = loop.time() - start_time
            if elapsed >= timeout_seconds:
                log.warning(
                    "claude_timeout_killing",
                    task_id=task_id,
                    stage=stage_num,
                    seconds=timeout_seconds,
                )
                proc.kill()
                await proc.wait()
                break

            # Post-result grace period: result is done, wait for graceful exit
            if result_seen and result_seen_at is not None:
                since_result = loop.time() - result_seen_at
                if since_result >= _POST_RESULT_GRACE_SECONDS:
                    log.warning(
                        "claude_post_result_grace_expired",
                        task_id=task_id,
                        stage=stage_num,
                        grace_seconds=_POST_RESULT_GRACE_SECONDS,
                    )
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                    break

            # --- read new bytes from file ---
            try:
                size = path.stat().st_size
            except OSError:
                size = 0

            if size > offset:
                tail_fh.seek(offset)
                chunk = tail_fh.read(size - offset)
                offset = size
                text = partial + chunk.decode("utf-8", errors="replace")

                if text.endswith("\n"):
                    raw_lines = text.split("\n")
                    partial = ""
                else:
                    raw_lines = text.split("\n")
                    partial = raw_lines.pop()

                for raw_line in raw_lines:
                    line = raw_line.rstrip("\r")
                    if not line.strip():
                        continue
                    tail.append(line)

                    if not result_seen:
                        try:
                            msg = json.loads(line)
                            if isinstance(msg, dict) and msg.get("type") == "result":
                                result_seen = True
                                result_seen_at = loop.time()
                                result_line = line
                                log.info(
                                    "claude_result_event_seen",
                                    task_id=task_id,
                                    stage=stage_num,
                                )
                        except json.JSONDecodeError:
                            pass

                    if on_live_output is not None:
                        try:
                            await on_live_output(line)
                        except Exception:
                            pass
            else:
                await asyncio.sleep(_TAIL_POLL_INTERVAL)

        # --- final read after process exit ---
        try:
            final_size = path.stat().st_size
        except OSError:
            final_size = 0

        if final_size > offset:
            tail_fh.seek(offset)
            chunk = tail_fh.read()
            text = partial + chunk.decode("utf-8", errors="replace")
            for raw_line in text.split("\n"):
                line = raw_line.rstrip("\r")
                if not line.strip():
                    continue
                tail.append(line)
                if not result_seen:
                    try:
                        msg = json.loads(line)
                        if isinstance(msg, dict) and msg.get("type") == "result":
                            result_seen = True
                            result_line = line
                    except json.JSONDecodeError:
                        pass
                if on_live_output is not None:
                    try:
                        await on_live_output(line)
                    except Exception:
                        pass
        elif partial.strip():
            line = partial.rstrip("\r")
            tail.append(line)
            if on_live_output is not None:
                try:
                    await on_live_output(line)
                except Exception:
                    pass

    finally:
        tail_fh.close()

    return list(tail), result_line, result_seen


def _read_file_tail(path: Path, max_bytes: int = _RAW_OUTPUT_MAX_BYTES) -> str:
    """Read the last *max_bytes* of *path* and return as a UTF-8 string.

    Used to populate ClaudeOutput.raw without loading the full file into
    memory.  Returns an empty string if the file is missing or empty.
    """
    try:
        size = path.stat().st_size
        if size == 0:
            return ""
        read_from = max(0, size - max_bytes)
        with open(path, "rb") as fh:
            if read_from > 0:
                fh.seek(read_from)
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _scan_file_for_rate_limit_event(path: Path) -> str | None:
    """Stream-scan *path* in 64 KB chunks for a ``rate_limit_event`` NDJSON line.

    Returns the raw JSON line string if found, ``None`` otherwise.  Avoids
    loading the full file into memory when searching for a single event type.
    """
    try:
        remainder = ""
        with open(path, encoding="utf-8", errors="replace") as fh:
            while True:
                chunk = fh.read(_RATE_LIMIT_SCAN_CHUNK)
                if not chunk:
                    break
                text = remainder + chunk
                lines = text.split("\n")
                remainder = lines.pop()
                for line in lines:
                    line = line.strip()
                    if not line or "rate_limit_event" not in line:
                        continue
                    try:
                        msg = json.loads(line)
                        if isinstance(msg, dict) and msg.get("type") == "rate_limit_event":
                            return line
                    except json.JSONDecodeError:
                        continue
        remainder = remainder.strip()
        if remainder and "rate_limit_event" in remainder:
            try:
                msg = json.loads(remainder)
                if isinstance(msg, dict) and msg.get("type") == "rate_limit_event":
                    return remainder
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return None

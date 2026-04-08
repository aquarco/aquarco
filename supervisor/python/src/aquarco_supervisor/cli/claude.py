"""Claude CLI invocation wrapper.

Stdout is redirected to a temporary file (not a pipe) so that output is never
lost due to premature pipe EOF.  The supervisor tails the file like ``tail -f``
until the process exits, then performs one final read.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..exceptions import AgentExecutionError, AgentInactivityError, AgentTimeoutError, OverloadedError, RateLimitError, ServerError
from ..logging import get_logger
from .output_parser import (  # noqa: F401  — re-exported for backward compat
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


@dataclass
class ClaudeOutput:
    """Separated structured and raw output from Claude CLI."""

    structured: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    raw_output_path: str | None = None


log = get_logger("claude-cli")

# Seconds to wait for the process to exit after the result event is seen.
_POST_RESULT_GRACE_SECONDS = 90.0

# How often the file tailer checks for new bytes.
_TAIL_POLL_INTERVAL = 0.5

# Directory for debug/stderr logs (overridable in tests).
_LOG_DIR = Path("/var/log/aquarco")

# Maximum bytes to read from the NDJSON stdout file for raw_output (128 KB).
_RAW_OUTPUT_MAX_BYTES = 131072

# Chunk size for stream-scanning the NDJSON file (64 KB).
_RATE_LIMIT_SCAN_CHUNK = 65536


# ---------------------------------------------------------------------------
# File-based stdout tailing
# ---------------------------------------------------------------------------

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
    # on every poll cycle.  The file was already created by mkstemp before
    # the subprocess started so it is safe to open now.
    tail_fh = open(path, "rb")

    try:
        while True:
            # --- check process state ---
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

                # Split into lines; keep trailing partial if no final newline
                if text.endswith("\n"):
                    raw_lines = text.split("\n")
                    partial = ""
                else:
                    raw_lines = text.split("\n")
                    partial = raw_lines.pop()  # incomplete line

                for raw_line in raw_lines:
                    line = raw_line.rstrip("\r")
                    if not line.strip():
                        continue
                    tail.append(line)

                    # Check for result event
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

                    # Live output callback (best-effort)
                    if on_live_output is not None:
                        try:
                            await on_live_output(line)
                        except Exception:
                            pass
            else:
                await asyncio.sleep(_TAIL_POLL_INTERVAL)

        # --- final read after process exit (still inside try/finally) ---
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
            # Flush any remaining partial line
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


# ---------------------------------------------------------------------------
# Raw output file helpers
# ---------------------------------------------------------------------------


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
                remainder = lines.pop()  # incomplete last line
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
        # Check any remaining partial line
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def execute_claude(
    prompt_file: Path,
    context: dict[str, Any],
    work_dir: str,
    timeout_seconds: int = 1800,
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    task_id: str = "",
    stage_num: int = 0,
    extra_env: dict[str, str] | None = None,
    output_schema: dict[str, Any] | None = None,
    max_turns: int = 30,
    resume_session_id: str | None = None,
    on_live_output: Callable[[str], Awaitable[None]] | None = None,
    model: str | None = None,
) -> ClaudeOutput:
    """Invoke the Claude CLI and return structured output.

    Stdout is written to a temporary file and tailed in real time (like
    ``tail -f``).  This avoids the premature-EOF problem with pipes where
    the stdout fd can close while the process is still running.

    When resume_session_id is provided, uses --resume to continue a prior session
    instead of starting fresh (no --system-prompt-file or schema flags needed).

    Once the ``{type: "result"}`` NDJSON event is seen the process is given
    a 90-second grace period to exit cleanly.  If it doesn't, it receives
    SIGTERM followed by SIGKILL.

    Raises:
        RateLimitError: Claude API returned a 429 rate-limit response (detected via
            ``"rate_limit_error"`` or ``"status code 429"`` in stdout/debug log).
        ServerError: Claude API returned a 500 internal server error (detected via
            ``"api_error"`` or ``"status code 500"``). Safe to retry after backoff.
        OverloadedError: Claude API returned a 529 overloaded response (detected via
            ``"overloaded_error"`` or ``"status code 529"``). Retry with shorter backoff.
        AgentExecutionError: The CLI exited non-zero for any other reason.
        AgentTimeoutError: The overall subprocess wall-clock timeout was exceeded.
        AgentInactivityError: No NDJSON events arrived within the inactivity window
            after the result event was emitted.
    """
    if not resume_session_id and not prompt_file.exists():
        raise AgentExecutionError(f"Prompt file not found: {prompt_file}")

    # Compute safe_id early — used for both the context temp file name and the
    # named stdout file path.
    safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)

    # Write context to temp file (use mkstemp for security)
    fd, context_path = tempfile.mkstemp(suffix=".json", prefix="claude-ctx-")
    context_file = Path(context_path)

    # Named stdout file in the log directory.  Kept after execute_claude returns
    # so callers can reference it for debugging; deleted when the task is closed
    # via close_task_resources().
    stdout_file = _LOG_DIR / f"claude-raw-{safe_id}-stage{stage_num}.ndjson"

    try:
        with os.fdopen(fd, "w") as f:
            if resume_session_id:
                f.write(
                    "Continue where you left off. Complete the remaining work. "
                    "Remember to produce your final response using the structured "
                    "output format specified in your original instructions."
                )
            else:
                json.dump(context, f, indent=2)

        # Dry-run mode: swap claude binary for the logging stub script
        _claude_bin = "claude"
        if os.environ.get("CLAUDE_DRY_RUN"):
            _dry_run_script = Path(__file__).resolve().parents[4] / "scripts" / "claude-dry-run.sh"
            if _dry_run_script.exists():
                _claude_bin = str(_dry_run_script)

        _SESSION_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{8,128}$')
        if resume_session_id and not _SESSION_ID_RE.match(resume_session_id):
            log.warning("invalid_session_id_format", session_id=resume_session_id[:64])
            resume_session_id = None

        if resume_session_id:
            args = [
                _claude_bin,
                "--resume", resume_session_id,
                "--print",
                "--dangerously-skip-permissions",
                "--output-format", "stream-json",
                "--max-turns", str(max_turns),
                "--verbose",
            ]
        else:
            args = [
                _claude_bin,
                "--print",
                "--dangerously-skip-permissions",
                "--output-format", "stream-json",
                "--max-turns", str(max_turns),
                "--verbose",
                "--system-prompt-file", str(prompt_file),
            ]

        if model:
            args.extend(["--model", model])

        if not resume_session_id:
            if allowed_tools:
                args.extend(["--allowedTools", ",".join(allowed_tools)])
            if denied_tools:
                args.extend(["--disallowedTools", ",".join(denied_tools)])
            if output_schema:
                args.extend(["--append-system-prompt", _format_schema_prompt(output_schema)])
                args.extend(["--json-schema", json.dumps(output_schema)])

        debug_log = _LOG_DIR / f"claude-{safe_id}-stage{stage_num}.log"
        debug_log.parent.mkdir(parents=True, exist_ok=True)

        stderr_log = _LOG_DIR / f"claude-{safe_id}-stage{stage_num}.stderr"
        args.extend(["--debug-file", str(debug_log)])

        log.info(
            "executing_claude",
            task_id=task_id,
            stage=stage_num,
            work_dir=work_dir,
            timeout=timeout_seconds,
            resume=resume_session_id or "",
        )

        # Merge extra environment variables from agent definition
        proc_env: dict[str, str] | None = None
        if extra_env:
            proc_env = {**os.environ, **extra_env}

        # The subprocess writes its stdout to stdout_file on disk.  Close the
        # supervisor's file handle after fork so the child owns the only writer.
        # _tail_file reads the file on disk (not via fd) so closing here is safe.
        with (
            open(context_file) as stdin_f,
            open(stdout_file, "w") as stdout_f,
            open(stderr_log, "w") as stderr_f,
        ):
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=stdin_f,
                stdout=stdout_f,
                stderr=stderr_f,
                cwd=work_dir,
                env=proc_env,
                start_new_session=True,
            )

            tail_lines, result_line, result_seen = await _tail_file(
                stdout_file,
                proc,
                on_live_output=on_live_output,
                timeout_seconds=float(timeout_seconds),
                task_id=task_id,
                stage_num=stage_num,
            )

        # Read the last 128 KB of the stdout file for raw_output.  The full file
        # is kept on disk at stdout_file and cleaned up on task close.
        raw_output = _read_file_tail(stdout_file)
        # Build parse_lines: result event first (always available even if pushed
        # out of the tail window), then the last 50 lines for fallback parsing.
        parse_lines = ([result_line] if result_line else []) + tail_lines

        # If we got a result event, treat it as success regardless of
        # returncode (we may have killed the process after the grace period).
        if result_seen:
            structured = _parse_ndjson_output(parse_lines, task_id, stage_num)
            if proc.returncode not in (0, None):
                log.info(
                    "claude_exited_after_result",
                    task_id=task_id,
                    stage=stage_num,
                    returncode=proc.returncode,
                )
            return ClaudeOutput(
                structured=structured,
                raw=raw_output,
                raw_output_path=str(stdout_file),
            )

        # No result event — check for errors
        if proc.returncode != 0:
            raw_stdout = "\n".join(tail_lines[:5]) if tail_lines else ""
            raw_stderr = ""
            for log_file in (stderr_log, debug_log):
                try:
                    content = log_file.read_text().strip()
                    if content:
                        raw_stderr += content[-500:]
                        break
                except OSError:
                    pass
            log.warning(
                "claude_cli_failed",
                task_id=task_id,
                stage=stage_num,
                returncode=proc.returncode,
                stdout_tail=raw_stdout[:500],
                stderr_tail=raw_stderr,
            )

            # Try to salvage session_id from NDJSON lines so the executor
            # can resume the conversation on retry instead of starting fresh.
            _sid = _extract_session_id_from_lines(parse_lines)

            if _is_rate_limited_in_lines(tail_lines) or _is_rate_limited(debug_log):
                raise RateLimitError(
                    f"Claude API rate limited (429) "
                    f"(task={task_id}, stage={stage_num})",
                    session_id=_sid,
                )

            # Detect internal server errors (500)
            if _is_server_error_in_lines(tail_lines) or _is_server_error(debug_log):
                raise ServerError(
                    f"Claude API internal server error (500) "
                    f"(task={task_id}, stage={stage_num})",
                    session_id=_sid,
                )

            # Detect platform overload errors (529)
            if _is_overloaded_in_lines(tail_lines) or _is_overloaded(debug_log):
                raise OverloadedError(
                    f"Claude API overloaded (529) "
                    f"(task={task_id}, stage={stage_num})",
                    session_id=_sid,
                )

            raise AgentExecutionError(
                f"Claude CLI exited with code {proc.returncode} "
                f"(task={task_id}, stage={stage_num})"
            )

        # Process exited 0 but no result event (unlikely but handle it)
        structured = _parse_ndjson_output(parse_lines, task_id, stage_num)
        return ClaudeOutput(
            structured=structured,
            raw=raw_output,
            raw_output_path=str(stdout_file),
        )

    finally:
        context_file.unlink(missing_ok=True)
        # stdout_file is intentionally kept on disk for post-mortem debugging.
        # It is deleted when the task is closed via close_task_resources().

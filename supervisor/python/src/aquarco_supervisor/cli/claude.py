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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..exceptions import AgentExecutionError, AgentInactivityError, AgentTimeoutError, OverloadedError, RateLimitError, ServerError
from ..logging import get_logger


@dataclass
class ClaudeOutput:
    """Separated structured and raw output from Claude CLI."""

    structured: dict[str, Any] = field(default_factory=dict)
    raw: str = ""


log = get_logger("claude-cli")

# Seconds to wait for the process to exit after the result event is seen.
_POST_RESULT_GRACE_SECONDS = 90.0

# How often the file tailer checks for new bytes.
_TAIL_POLL_INTERVAL = 0.5

# Directory for debug/stderr logs (overridable in tests).
_LOG_DIR = Path("/var/log/aquarco")


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
) -> tuple[list[str], bool]:
    """Tail an NDJSON file written by Claude CLI until the process exits.

    Returns (lines, result_seen).

    The loop runs until one of:
    1. The process exits (``proc.returncode is not None``).
    2. The overall *timeout_seconds* elapses → process is killed.
    3. The result event is seen and the process doesn't exit within
       ``_POST_RESULT_GRACE_SECONDS`` → process is terminated then killed.
    """
    lines: list[str] = []
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
                    lines.append(line)

                    # Check for result event
                    if not result_seen:
                        try:
                            msg = json.loads(line)
                            if isinstance(msg, dict) and msg.get("type") == "result":
                                result_seen = True
                                result_seen_at = loop.time()
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
                lines.append(line)
                if not result_seen:
                    try:
                        msg = json.loads(line)
                        if isinstance(msg, dict) and msg.get("type") == "result":
                            result_seen = True
                    except json.JSONDecodeError:
                        pass
                if on_live_output is not None:
                    try:
                        await on_live_output(line)
                    except Exception:
                        pass
        elif partial.strip():
            # Flush any remaining partial line
            lines.append(partial.rstrip("\r"))
            if on_live_output is not None:
                try:
                    await on_live_output(partial.rstrip("\r"))
                except Exception:
                    pass

    finally:
        tail_fh.close()

    return lines, result_seen


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

    # Write context to temp file (use mkstemp for security)
    fd, context_path = tempfile.mkstemp(suffix=".json", prefix="claude-ctx-")
    context_file = Path(context_path)

    # Stdout capture file – we open the fd immediately via os.fdopen so that
    # if an exception occurs before the subprocess is launched the fd is
    # properly closed by the context manager (no leak).
    stdout_fd, stdout_path = tempfile.mkstemp(suffix=".ndjson", prefix="claude-out-")
    stdout_file = Path(stdout_path)

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

        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)
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

        # Wrap stdout_fd early so it is always cleaned up.  The subprocess
        # inherits the fd at fork time and gets its own copy, so closing the
        # supervisor's copy here is safe – the child keeps writing to its fd
        # independently.  _tail_file reads the *file on disk*, not the fd.
        with (
            open(context_file) as stdin_f,
            os.fdopen(stdout_fd, "w") as stdout_f,
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

            lines, result_seen = await _tail_file(
                stdout_file,
                proc,
                on_live_output=on_live_output,
                timeout_seconds=float(timeout_seconds),
                task_id=task_id,
                stage_num=stage_num,
            )

        raw_output = "\n".join(lines)

        # If we got a result event, treat it as success regardless of
        # returncode (we may have killed the process after the grace period).
        if result_seen:
            structured = _parse_ndjson_output(lines, task_id, stage_num)
            if proc.returncode not in (0, None):
                log.info(
                    "claude_exited_after_result",
                    task_id=task_id,
                    stage=stage_num,
                    returncode=proc.returncode,
                )
            return ClaudeOutput(structured=structured, raw=raw_output)

        # No result event — check for errors
        if proc.returncode != 0:
            raw_stdout = "\n".join(lines[:5]) if lines else ""
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
            _sid = _extract_session_id_from_lines(lines)

            if _is_rate_limited_in_lines(lines) or _is_rate_limited(debug_log):
                raise RateLimitError(
                    f"Claude API rate limited (429) "
                    f"(task={task_id}, stage={stage_num})",
                    session_id=_sid,
                )

            # Detect internal server errors (500)
            if _is_server_error_in_lines(lines) or _is_server_error(debug_log):
                raise ServerError(
                    f"Claude API internal server error (500) "
                    f"(task={task_id}, stage={stage_num})",
                    session_id=_sid,
                )

            # Detect platform overload errors (529)
            if _is_overloaded_in_lines(lines) or _is_overloaded(debug_log):
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
        structured = _parse_ndjson_output(lines, task_id, stage_num)
        return ClaudeOutput(structured=structured, raw=raw_output)

    finally:
        context_file.unlink(missing_ok=True)
        stdout_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# NDJSON parsing helpers
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
        elif not msg.get("result") and not structured:
            # No result and no structured_output
            return dict(msg)
        else:
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

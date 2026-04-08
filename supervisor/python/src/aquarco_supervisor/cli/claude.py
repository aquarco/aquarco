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

from ..exceptions import AgentExecutionError, AgentTimeoutError, OverloadedError, RateLimitError, ServerError
from ..logging import get_logger
from .file_tailer import (  # noqa: F401 — re-exported for backward compat
    _read_file_tail,
    _scan_file_for_rate_limit_event,
    _tail_file,
)
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

# Directory for debug/stderr logs (overridable in tests).
_LOG_DIR = Path("/var/log/aquarco")


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
        RateLimitError: Claude API returned a 429 rate-limit response.
        ServerError: Claude API returned a 500 internal server error.
        OverloadedError: Claude API returned a 529 overloaded response.
        AgentExecutionError: The CLI exited non-zero for any other reason.
        AgentTimeoutError: The overall subprocess wall-clock timeout was exceeded.
    """
    if not resume_session_id and not prompt_file.exists():
        raise AgentExecutionError(f"Prompt file not found: {prompt_file}")

    safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)

    # Write context to temp file (use mkstemp for security)
    fd, context_path = tempfile.mkstemp(suffix=".json", prefix="claude-ctx-")
    context_file = Path(context_path)

    # Named stdout file in the log directory — kept for debugging, deleted on task close.
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

        args = _build_cli_args(
            resume_session_id=resume_session_id,
            prompt_file=prompt_file,
            max_turns=max_turns,
            model=model,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            output_schema=output_schema,
        )

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

        proc_env: dict[str, str] | None = None
        if extra_env:
            proc_env = {**os.environ, **extra_env}

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

        raw_output = _read_file_tail(stdout_file)
        parse_lines = ([result_line] if result_line else []) + tail_lines

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
            _raise_cli_error(
                tail_lines, parse_lines, stderr_log, debug_log, task_id, stage_num,
                returncode=proc.returncode,
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_cli_args(
    *,
    resume_session_id: str | None,
    prompt_file: Path,
    max_turns: int,
    model: str | None,
    allowed_tools: list[str] | None,
    denied_tools: list[str] | None,
    output_schema: dict[str, Any] | None,
) -> list[str]:
    """Build the Claude CLI argument list."""
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

    return args


def _raise_cli_error(
    tail_lines: list[str],
    parse_lines: list[str],
    stderr_log: Path,
    debug_log: Path,
    task_id: str,
    stage_num: int,
    *,
    returncode: int | None,
) -> None:
    """Classify and raise the appropriate error for a failed CLI invocation."""
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
        returncode=returncode,
        stdout_tail=raw_stdout[:500],
        stderr_tail=raw_stderr,
    )

    _sid = _extract_session_id_from_lines(parse_lines)

    if _is_rate_limited_in_lines(tail_lines) or _is_rate_limited(debug_log):
        raise RateLimitError(
            f"Claude API rate limited (429) (task={task_id}, stage={stage_num})",
            session_id=_sid,
        )
    if _is_server_error_in_lines(tail_lines) or _is_server_error(debug_log):
        raise ServerError(
            f"Claude API internal server error (500) (task={task_id}, stage={stage_num})",
            session_id=_sid,
        )
    if _is_overloaded_in_lines(tail_lines) or _is_overloaded(debug_log):
        raise OverloadedError(
            f"Claude API overloaded (529) (task={task_id}, stage={stage_num})",
            session_id=_sid,
        )
    raise AgentExecutionError(
        f"Claude CLI exited with code {returncode} (task={task_id}, stage={stage_num})"
    )

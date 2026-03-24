"""Claude CLI invocation wrapper."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..exceptions import AgentExecutionError, AgentTimeoutError
from ..logging import get_logger


@dataclass
class ClaudeOutput:
    """Separated structured and raw output from Claude CLI."""

    structured: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

log = get_logger("claude-cli")


def _format_schema_prompt(schema: dict[str, Any]) -> str:
    """Format an outputSchema dict as a human-readable prompt section."""
    lines = [
        "## Output Format",
        "",
        "You MUST respond with a JSON object conforming to this schema:",
        "",
        "```json",
        json.dumps(schema, indent=2),
        "```",
    ]
    return "\n".join(lines)


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
) -> ClaudeOutput:
    """Invoke the Claude CLI and return structured output.

    Runs claude with --print --dangerously-skip-permissions --output-format json,
    feeding context via stdin.

    When resume_session_id is provided, uses --resume to continue a prior session
    instead of starting fresh (no --system-prompt-file or schema flags needed).
    """
    if not resume_session_id and not prompt_file.exists():
        raise AgentExecutionError(f"Prompt file not found: {prompt_file}")

    # Write context to temp file (use mkstemp for security)
    fd, context_path = tempfile.mkstemp(suffix=".json", prefix="claude-ctx-")
    context_file = Path(context_path)
    try:
        with os.fdopen(fd, "w") as f:
            if resume_session_id:
                # For resume, stdin is just a continuation prompt
                f.write("Continue where you left off. Complete the remaining work.")
            else:
                json.dump(context, f, indent=2)

        if resume_session_id:
            args = [
                "claude",
                "--resume", resume_session_id,
                "--print",
                "--dangerously-skip-permissions",
                "--output-format", "json",
                "--max-turns", str(max_turns),
                "--verbose",
            ]
        else:
            args = [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                "--output-format", "json",
                "--max-turns", str(max_turns),
                "--verbose",
                "--system-prompt-file", str(prompt_file),
            ]

            if allowed_tools:
                args.extend(["--allowedTools", ",".join(allowed_tools)])
            if denied_tools:
                args.extend(["--disallowedTools", ",".join(denied_tools)])
            if output_schema:
                args.extend(["--append-system-prompt", _format_schema_prompt(output_schema)])
                args.extend(["--json-schema", json.dumps(output_schema)])

        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id)
        debug_log = Path(f"/var/log/aquarco/claude-{safe_id}-stage{stage_num}.log")
        debug_log.parent.mkdir(parents=True, exist_ok=True)

        stderr_log = Path(f"/var/log/aquarco/claude-{safe_id}-stage{stage_num}.stderr")
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

        with open(context_file) as stdin_f, open(stderr_log, "w") as stderr_f:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=stdin_f,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr_f,
                cwd=work_dir,
                env=proc_env,
            )

            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise AgentTimeoutError(
                    f"Claude CLI timed out after {timeout_seconds}s "
                    f"(task={task_id}, stage={stage_num})"
                )

        if proc.returncode != 0:
            raw_stdout = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
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
            raise AgentExecutionError(
                f"Claude CLI exited with code {proc.returncode} "
                f"(task={task_id}, stage={stage_num})"
            )

        raw_output = stdout.decode("utf-8", errors="replace") if stdout else ""
        structured = _parse_output(raw_output, task_id, stage_num)
        return ClaudeOutput(structured=structured, raw=raw_output)

    finally:
        context_file.unlink(missing_ok=True)


def _parse_output(raw_output: str, task_id: str, stage_num: int) -> dict[str, Any]:
    """Parse Claude CLI JSON output and extract structured result.

    Returns only the structured data — raw output is stored separately.
    The result message from Claude CLI (--output-format json) contains:
    - structured_output: the JSON schema response (when --json-schema used)
    - result: the assistant's final text reply
    - total_cost_usd, usage, modelUsage: token/cost metrics
    - duration_ms, num_turns: execution metadata
    """
    if not raw_output.strip():
        return {"_no_structured_output": True}

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        return {"_no_structured_output": True}

    # Claude CLI --output-format json returns a list of message objects.
    # Find the "result" message which contains structured_output, usage, etc.
    if isinstance(parsed, list):
        result_msg = _find_result_message(parsed)
        if result_msg:
            return _extract_from_result_message(result_msg)

        # Fallback: concatenate all assistant text content
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

    # Single dict format (older CLI versions)
    return _extract_from_result_message(parsed)


def _find_result_message(messages: list[Any]) -> dict[str, Any] | None:
    """Find the result message in a Claude CLI message list."""
    for msg in messages:
        if isinstance(msg, dict) and msg.get("type") == "result":
            return msg
    return None


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
            if extracted is not None:
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
            output["_input_tokens"] = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
            output["_output_tokens"] = usage.get("output_tokens", 0)
            output["_cache_creation_tokens"] = usage.get("cache_creation_input_tokens", 0)
    if "duration_ms" in msg:
        output["_duration_ms"] = msg["duration_ms"]
    if "num_turns" in msg:
        output["_num_turns"] = msg["num_turns"]
    if "session_id" in msg:
        output["_session_id"] = msg["session_id"]

    return output


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract JSON from text, trying code blocks first then raw JSON."""
    # Try ```json ... ``` blocks
    match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
    if match:
        try:
            result: dict[str, Any] = json.loads(match.group(1))
            return result
        except json.JSONDecodeError:
            pass

    # Try each line that starts with { or [
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

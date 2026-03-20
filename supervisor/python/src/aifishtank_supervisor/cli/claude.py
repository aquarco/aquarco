"""Claude CLI invocation wrapper."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ..exceptions import AgentExecutionError, AgentTimeoutError
from ..logging import get_logger

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
) -> dict[str, Any]:
    """Invoke the Claude CLI and return structured output.

    Runs claude with --print --dangerously-skip-permissions --output-format json,
    feeding context via stdin.
    """
    if not prompt_file.exists():
        raise AgentExecutionError(f"Prompt file not found: {prompt_file}")

    # Write context to temp file (use mkstemp for security)
    fd, context_path = tempfile.mkstemp(suffix=".json", prefix="claude-ctx-")
    context_file = Path(context_path)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(context, f, indent=2)

        args = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "json",
            "--max-turns", "30",
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
        debug_log = Path(f"/var/log/aifishtank/claude-{safe_id}-stage{stage_num}.log")
        debug_log.parent.mkdir(parents=True, exist_ok=True)

        log.info(
            "executing_claude",
            task_id=task_id,
            stage=stage_num,
            work_dir=work_dir,
            timeout=timeout_seconds,
        )

        # Merge extra environment variables from agent definition
        proc_env: dict[str, str] | None = None
        if extra_env:
            proc_env = {**os.environ, **extra_env}

        with open(context_file) as stdin_f, open(debug_log, "w") as stderr_f:
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
            raise AgentExecutionError(
                f"Claude CLI exited with code {proc.returncode} "
                f"(task={task_id}, stage={stage_num})"
            )

        raw_output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return _parse_output(raw_output, task_id, stage_num)

    finally:
        context_file.unlink(missing_ok=True)


def _parse_output(raw_output: str, task_id: str, stage_num: int) -> dict[str, Any]:
    """Parse Claude CLI JSON output and extract structured result."""
    if not raw_output.strip():
        return {"_raw_output": "", "_no_structured_output": True}

    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        return {"_raw_output": raw_output, "_no_structured_output": True}

    # Claude CLI --output-format json may return a list of message objects
    # instead of a single dict. Normalise to a dict before proceeding.
    if isinstance(parsed, list):
        # Try to find the assistant result in the message list
        result_text = ""
        for msg in parsed:
            if isinstance(msg, dict) and msg.get("type") == "result":
                result_text = msg.get("result", "")
                break
        if not result_text:
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
        return {"_raw_output": raw_output, "_parsed_messages": parsed}

    # Extract the result text from the JSON output
    result_text = parsed.get("result", "")
    if not result_text:
        return dict(parsed)

    # Try to extract JSON from the result text
    structured = _extract_json(result_text)
    if structured is not None:
        return structured

    return {"_raw_output": result_text, "_no_structured_output": True}


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

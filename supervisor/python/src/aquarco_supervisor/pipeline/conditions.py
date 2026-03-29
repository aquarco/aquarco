"""Structured condition evaluation for pipeline exit gates.

Supports:
  - simple: expression-based conditions (==, !=, >=, >, <=, <, &&, ||, parentheses)
  - ai: Claude CLI-evaluated conditions

Each condition can have yes/no fields specifying target stage names for jumps,
and maxRepeats to limit how many times a jump target can be visited.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..logging import get_logger

log = get_logger("conditions")


@dataclass
class ConditionResult:
    """Result of evaluating a condition list."""

    jump_to: str | None = None
    matched: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate_conditions(
    conditions: list[dict[str, Any]],
    stage_outputs: dict[str, dict[str, Any]],
    current_output: dict[str, Any],
    repeat_counts: dict[str, int],
    ai_evaluator: Callable[[str, dict[str, Any]], Awaitable[bool]] | None = None,
) -> ConditionResult:
    """Evaluate a list of structured conditions as exit gates.

    Args:
        conditions: List of condition dicts from pipeline config.
        stage_outputs: Map of stage_name -> output for all completed stages.
        current_output: Output of the current stage.
        repeat_counts: Map of stage_name -> times visited.
        ai_evaluator: Async callable for AI conditions (prompt, context) -> bool.

    Returns:
        ConditionResult with jump_to stage name or None.
    """
    if not conditions:
        return ConditionResult()

    # Build a unified context for expression evaluation:
    # - unqualified names resolve from current_output
    # - qualified names (e.g., analysis.risks) resolve from stage_outputs
    context = _build_eval_context(stage_outputs, current_output)

    for cond in conditions:
        if not isinstance(cond, dict):
            continue

        result = await _evaluate_single_condition(cond, context, ai_evaluator)
        if result is None:
            # Condition couldn't be evaluated; skip
            continue

        # Determine jump target based on yes/no
        # Handle both string keys ("yes"/"no") and boolean keys (True/False)
        # that can occur from YAML parsing of unquoted yes/no
        if result:
            jump_target = cond.get("yes") or cond.get(True)
        else:
            jump_target = cond.get("no") or cond.get(False)

        if jump_target is None:
            # No jump specified for this outcome; try next condition
            continue

        # Check maxRepeats for the jump target
        max_repeats = cond.get("maxRepeats", 0)
        if max_repeats > 0:
            current_count = repeat_counts.get(jump_target, 0)
            if current_count >= max_repeats:
                log.info(
                    "condition_max_repeats_exceeded",
                    jump_to=jump_target,
                    max_repeats=max_repeats,
                    current_count=current_count,
                )
                continue  # Skip this condition, try next

        return ConditionResult(jump_to=jump_target, matched=True)

    return ConditionResult()


# ---------------------------------------------------------------------------
# Single condition evaluation
# ---------------------------------------------------------------------------


async def _evaluate_single_condition(
    cond: dict[str, Any],
    context: dict[str, Any],
    ai_evaluator: Callable[[str, dict[str, Any]], Awaitable[bool]] | None,
) -> bool | None:
    """Evaluate a single condition. Returns True/False or None if unevaluable."""
    if "simple" in cond:
        raw_expr = cond["simple"]
        # Handle boolean values (YAML `true`/`false` parsed as bool)
        if isinstance(raw_expr, bool):
            return raw_expr
        expr = str(raw_expr)
        try:
            return evaluate_simple_expression(expr, context)
        except Exception as e:
            log.warning("simple_condition_eval_error", expr=expr, error=str(e))
            return None

    if "ai" in cond:
        prompt = str(cond["ai"])
        if ai_evaluator is None:
            log.warning("ai_condition_no_evaluator", prompt=prompt)
            return None
        try:
            return await ai_evaluator(prompt, context)
        except Exception as e:
            log.warning("ai_condition_eval_error", prompt=prompt, error=str(e))
            return None

    return None


# ---------------------------------------------------------------------------
# Expression parser (recursive descent)
# ---------------------------------------------------------------------------

# Token types
_TOKEN_RE = re.compile(
    r"""
    \s*(?:
        (&&)            |  # AND
        (\|\|)          |  # OR
        (==)            |  # EQ
        (!=)            |  # NE
        (>=)            |  # GE
        (>)             |  # GT
        (<=)            |  # LE
        (<)             |  # LT
        (\()            |  # LPAREN
        (\))            |  # RPAREN
        ([a-zA-Z_][a-zA-Z0-9_.]*) |  # IDENT (including dotted paths)
        (-?\d+(?:\.\d+)?)  # NUMBER
    )\s*
    """,
    re.VERBOSE,
)

_TOKEN_NAMES = [
    "AND", "OR", "EQ", "NE", "GE", "GT", "LE", "LT",
    "LPAREN", "RPAREN", "IDENT", "NUMBER",
]


@dataclass
class _Token:
    type: str
    value: str


def _tokenize(expr: str) -> list[_Token]:
    """Tokenize a simple expression string."""
    tokens: list[_Token] = []
    pos = 0
    while pos < len(expr):
        # Skip whitespace
        while pos < len(expr) and expr[pos].isspace():
            pos += 1
        if pos >= len(expr):
            break

        m = _TOKEN_RE.match(expr, pos)
        if not m:
            raise ValueError(f"Unexpected character at position {pos}: '{expr[pos:]}'")

        for i, name in enumerate(_TOKEN_NAMES):
            if m.group(i + 1) is not None:
                tokens.append(_Token(type=name, value=m.group(i + 1)))
                break

        pos = m.end()

    return tokens


class _Parser:
    """Recursive descent parser for simple condition expressions.

    Grammar:
        expr     -> or_expr
        or_expr  -> and_expr ('||' and_expr)*
        and_expr -> cmp_expr ('&&' cmp_expr)*
        cmp_expr -> primary (('=='|'!='|'>='|'>'|'<='|'<') primary)?
        primary  -> '(' expr ')' | IDENT | NUMBER
    """

    def __init__(self, tokens: list[_Token], context: dict[str, Any]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.context = context

    def peek(self) -> _Token | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self, expected_type: str | None = None) -> _Token:
        token = self.peek()
        if token is None:
            raise ValueError("Unexpected end of expression")
        if expected_type and token.type != expected_type:
            raise ValueError(f"Expected {expected_type}, got {token.type}")
        self.pos += 1
        return token

    def parse(self) -> bool:
        result = self.or_expr()
        if self.pos < len(self.tokens):
            raise ValueError(f"Unexpected token: {self.tokens[self.pos].value}")
        return result

    def or_expr(self) -> bool:
        left = self.and_expr()
        while self.peek() and self.peek().type == "OR":  # type: ignore[union-attr]
            self.consume("OR")
            right = self.and_expr()
            left = left or right
        return left

    def and_expr(self) -> bool:
        left = self.cmp_expr()
        while self.peek() and self.peek().type == "AND":  # type: ignore[union-attr]
            self.consume("AND")
            right = self.cmp_expr()
            left = left and right
        return left

    def cmp_expr(self) -> bool:
        left = self.primary()
        tok = self.peek()
        if tok and tok.type in ("EQ", "NE", "GE", "GT", "LE", "LT"):
            op = self.consume()
            right = self.primary()
            return _compare(left, op.type, right)
        # Truthy check: a standalone value is truthy
        return _is_truthy(left)

    def primary(self) -> Any:
        tok = self.peek()
        if tok is None:
            raise ValueError("Unexpected end of expression")

        if tok.type == "LPAREN":
            self.consume("LPAREN")
            result = self.or_expr()
            self.consume("RPAREN")
            return result

        if tok.type == "NUMBER":
            self.consume()
            if "." in tok.value:
                return float(tok.value)
            return int(tok.value)

        if tok.type == "IDENT":
            self.consume()
            # Handle boolean literals
            if tok.value == "true":
                return True
            if tok.value == "false":
                return False
            # Resolve from context; if not found, treat as string literal
            resolved = _resolve_field(self.context, tok.value)
            if resolved is not None:
                return resolved
            return tok.value  # treat as string literal

        raise ValueError(f"Unexpected token: {tok.value}")


def evaluate_simple_expression(expr: str, context: dict[str, Any]) -> bool:
    """Evaluate a simple expression string against a context dict."""
    expr = expr.strip()
    if not expr:
        return True

    tokens = _tokenize(expr)
    if not tokens:
        return True

    parser = _Parser(tokens, context)
    return parser.parse()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_eval_context(
    stage_outputs: dict[str, dict[str, Any]],
    current_output: dict[str, Any],
) -> dict[str, Any]:
    """Build unified context: current output at top level, stage outputs nested."""
    context: dict[str, Any] = {}
    # Stage outputs are accessible via stage_name.field_name
    context.update(stage_outputs)
    # Current stage output fields are accessible directly (unqualified)
    context.update(current_output)
    return context


def _resolve_field(data: dict[str, Any], field_path: str) -> Any:
    """Resolve a dotted field path in a dict."""
    current: Any = data
    for key in field_path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


def _is_truthy(value: Any) -> bool:
    """Check if a value is truthy for condition evaluation."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() not in ("", "false", "0", "no", "none")
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return bool(value)


def _to_number(value: Any) -> float | None:
    """Try to convert a value to a number for comparison."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _compare(left: Any, op: str, right: Any) -> bool:
    """Compare two values with an operator."""
    # Try numeric comparison first
    left_num = _to_number(left)
    right_num = _to_number(right)

    if left_num is not None and right_num is not None:
        if op == "EQ":
            return left_num == right_num
        if op == "NE":
            return left_num != right_num
        if op == "GE":
            return left_num >= right_num
        if op == "GT":
            return left_num > right_num
        if op == "LE":
            return left_num <= right_num
        if op == "LT":
            return left_num < right_num

    # Fall back to string comparison
    left_str = str(left) if left is not None else ""
    right_str = str(right) if right is not None else ""

    if op == "EQ":
        return left_str == right_str
    if op == "NE":
        return left_str != right_str
    if op == "GE":
        return left_str >= right_str
    if op == "GT":
        return left_str > right_str
    if op == "LE":
        return left_str <= right_str
    if op == "LT":
        return left_str < right_str

    return False


# ---------------------------------------------------------------------------
# AI condition evaluator (Claude CLI)
# ---------------------------------------------------------------------------

_AI_CONDITION_SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "properties": {
        "answer": {
            "type": "boolean",
            "description": "true if the condition is met, false otherwise",
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of why the condition is or is not met",
        },
    },
}

# Inline fallback prompt — used when no prompts_dir is provided or the
# condition-evaluator-agent.md file is absent
_INLINE_SYSTEM_PROMPT = (
    "You are a pipeline condition evaluator. You will be given a question "
    "about pipeline stage outputs and must answer with a JSON object "
    "containing an 'answer' boolean field.\n\n"
    "Evaluate the condition based ONLY on the provided context data. "
    "If the context does not contain enough information to evaluate the "
    "condition, answer false.\n\n"
    "## Output Format\n\n"
    "You MUST respond with a JSON object conforming to this schema:\n\n"
    "```json\n"
    "{schema_json}\n"
    "```"
)


async def evaluate_ai_condition(
    prompt: str,
    context: dict[str, Any],
    *,
    work_dir: str = "/tmp",
    task_id: str = "",
    stage_num: int = 0,
    timeout_seconds: int = 120,
    extra_env: dict[str, str] | None = None,
    prompts_dir: "Path | None" = None,
) -> bool:
    """Evaluate an AI condition by asking Claude CLI a yes/no question.

    Constructs a minimal prompt with the condition question and accumulated
    pipeline context, then invokes Claude CLI with --max-turns 1 (no tools)
    and a boolean output schema.

    Args:
        prompts_dir: Optional path to the agents/prompts/ directory.  When
            provided and ``condition-evaluator-agent.md`` exists there, its
            content is used as the system prompt instead of the inline fallback.

    Returns True if the condition is met, False otherwise.
    """
    context_json = json.dumps(context, indent=2, default=str)

    # Try to load system prompt from the condition-evaluator agent definition
    system_prompt: str | None = None
    if prompts_dir is not None:
        prompt_path = Path(prompts_dir) / "condition-evaluator-agent.md"
        if prompt_path.exists():
            try:
                # The file-based prompt is used verbatim — no {schema_json}
                # substitution is applied (unlike the inline fallback below).
                # The schema is embedded statically in the .md file.
                # test_condition_evaluator_md_schema_matches_inline_schema
                # guards against drift between the two.
                system_prompt = prompt_path.read_text()
                log.debug("ai_condition_prompt_loaded", path=str(prompt_path))
            except OSError:
                pass

    if system_prompt is None:
        system_prompt = _INLINE_SYSTEM_PROMPT.format(
            schema_json=json.dumps(_AI_CONDITION_SCHEMA, indent=2)
        )

    stdin_content = (
        f"## Condition to evaluate\n\n{prompt}\n\n"
        f"## Pipeline context\n\n```json\n{context_json}\n```"
    )

    # Write system prompt and stdin to temp files
    fd_sys, sys_path = tempfile.mkstemp(suffix=".md", prefix="ai-cond-sys-")
    fd_in, in_path = tempfile.mkstemp(suffix=".txt", prefix="ai-cond-in-")
    try:
        with os.fdopen(fd_sys, "w") as f:
            f.write(system_prompt)
        with os.fdopen(fd_in, "w") as f:
            f.write(stdin_content)

        safe_id = re.sub(r"[^a-zA-Z0-9._-]", "-", task_id) if task_id else "cond"
        args = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--output-format", "stream-json",
            "--max-turns", "1",
            "--system-prompt-file", sys_path,
            "--json-schema", json.dumps(_AI_CONDITION_SCHEMA),
        ]

        proc_env: dict[str, str] | None = None
        if extra_env:
            proc_env = {**os.environ, **extra_env}

        log.info(
            "ai_condition_evaluating",
            task_id=task_id,
            stage=stage_num,
            prompt=prompt[:100],
        )

        with open(in_path) as stdin_f:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=stdin_f,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=proc_env,
            )

            # Read stdout (NDJSON stream) and stderr concurrently to avoid deadlock
            async def _read_stdout_lines() -> list[str]:
                result: list[str] = []
                assert proc.stdout is not None
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r")
                    if line.strip():
                        result.append(line)
                return result

            stdout_task: asyncio.Task[list[str]] = asyncio.create_task(_read_stdout_lines())
            stderr_task: asyncio.Task[bytes] = asyncio.create_task(
                proc.stderr.read()  # type: ignore[union-attr]
            )

            try:
                await asyncio.wait_for(
                    asyncio.shield(stdout_task), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                stdout_task.cancel()
                stderr_task.cancel()
                for t in (stdout_task, stderr_task):
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):
                        pass
                log.warning("ai_condition_timeout", task_id=task_id, prompt=prompt[:100])
                return False

            ndjson_lines: list[str] = []
            try:
                ndjson_lines = stdout_task.result()
            except Exception:
                pass

            stderr_bytes = b""
            if stderr_task.done():
                try:
                    stderr_bytes = stderr_task.result()
                except Exception:
                    pass
            else:
                stderr_task.cancel()
                try:
                    await stderr_task
                except (asyncio.CancelledError, Exception):
                    pass

            await proc.wait()

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace")[:500]
            log.warning(
                "ai_condition_cli_error",
                task_id=task_id,
                returncode=proc.returncode,
                stderr=stderr_text,
            )
            return False

        # Parse NDJSON stream: find the result message and extract 'answer'
        from ..cli.claude import _parse_ndjson_output  # noqa: PLC0415

        output_dict = _parse_ndjson_output(ndjson_lines, task_id, stage_num)
        answer = output_dict.get("answer")
        result = bool(answer)
        reasoning = output_dict.get("reasoning", "")
        log.info(
            "ai_condition_result",
            task_id=task_id,
            prompt=prompt[:100],
            answer=result,
            reasoning=str(reasoning)[:200],
        )
        return result

    finally:
        for p in (sys_path, in_path):
            try:
                os.unlink(p)
            except OSError:
                pass

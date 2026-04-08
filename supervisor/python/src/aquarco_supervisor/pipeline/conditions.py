"""Structured condition evaluation for pipeline exit gates.

Supports:
  - simple: expression-based conditions (==, !=, >=, >, <=, <, &&, ||, parentheses)
  - ai: Claude CLI-evaluated conditions

Each condition can have yes/no fields specifying target stage names for jumps,
and maxRepeats to limit how many times a jump target can be visited.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..cli.claude import execute_claude
from ..exceptions import RetryableError
from ..logging import get_logger
from ..models import Complexity

log = get_logger("conditions")


@dataclass
class ConditionResult:
    """Result of evaluating a condition list."""

    jump_to: str | None = None
    matched: bool = False
    message: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate_conditions(
    conditions: list[dict[str, Any]],
    stage_outputs: dict[str, dict[str, Any]],
    current_output: dict[str, Any],
    repeat_counts: dict[str, int],
    ai_evaluator: Callable[[str, dict[str, Any]], Awaitable[tuple[bool, str]]] | None = None,
) -> ConditionResult:
    """Evaluate a list of structured conditions as exit gates.

    Args:
        conditions: List of condition dicts from pipeline config.
        stage_outputs: Map of stage_name -> output for all completed stages.
        current_output: Output of the current stage.
        repeat_counts: Map of stage_name -> times visited.
        ai_evaluator: Async callable for AI conditions (prompt, context) -> (bool, message).

    Returns:
        ConditionResult with jump_to stage name, message, or None.
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

        eval_result = await _evaluate_single_condition(cond, context, ai_evaluator)
        if eval_result is None:
            # Condition couldn't be evaluated; skip
            continue

        result, message = eval_result

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

        return ConditionResult(jump_to=jump_target, matched=True, message=message)

    return ConditionResult()


# ---------------------------------------------------------------------------
# Single condition evaluation
# ---------------------------------------------------------------------------


async def _evaluate_single_condition(
    cond: dict[str, Any],
    context: dict[str, Any],
    ai_evaluator: Callable[[str, dict[str, Any]], Awaitable[tuple[bool, str]]] | None,
) -> tuple[bool, str] | None:
    """Evaluate a single condition. Returns (answer, message) or None if unevaluable."""
    if "simple" in cond:
        raw_expr = cond["simple"]
        # Handle boolean values (YAML `true`/`false` parsed as bool)
        if isinstance(raw_expr, bool):
            return (raw_expr, "")
        expr = str(raw_expr)
        try:
            return (evaluate_simple_expression(expr, context), "")
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
        except RetryableError:
            raise  # Let transient API errors (429/500/529) propagate for postpone
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
    "required": ["answer", "message"],
    "properties": {
        "answer": {
            "type": "boolean",
            "description": "true if the condition is met, false otherwise",
        },
        "message": {
            "type": "string",
            "description": (
                "Concise description of what was found and what the next stage "
                "should focus on. Include specific issues, missing items, or "
                "areas that need attention. This message is passed to the next "
                "pipeline stage as context."
            ),
        },
    },
}

# Inline fallback prompt — used when no prompts_dir is provided or the
# condition-evaluator-agent.md file is absent
_INLINE_SYSTEM_PROMPT = (
    "You are a pipeline condition evaluator. You will be given a question "
    "about pipeline stage outputs and must answer with a JSON object "
    "containing an 'answer' boolean and a 'message' string.\n\n"
    "Evaluate the condition based ONLY on the provided context data. "
    "If the context does not contain enough information to evaluate the "
    "condition, answer false.\n\n"
    "The 'message' field is passed to the next pipeline stage as context. "
    "It should describe what was found and what the next stage should focus on — "
    "specific issues, missing items, or areas needing attention.\n\n"
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
    max_turns: int = 1,
    extra_env: dict[str, str] | None = None,
    prompt_file: "Path | None" = None,
    on_live_output: "Callable[[str], Awaitable[None]] | None" = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Evaluate an AI condition by asking Claude CLI a yes/no question.

    Delegates to ``execute_claude`` so that spending, live output streaming,
    and error classification (rate limits, 429/500/529) are handled uniformly.

    Args:
        prompt_file: Path to the system prompt file for the condition evaluator.
            When ``None``, an inline fallback prompt is written to a temp file.
        max_turns: Max conversation turns (from agent definition).
        on_live_output: Optional callback for live output streaming.
        model: Claude model to use. When ``None``, the CLI uses its default.

    Returns a dict with at least ``answer`` (bool) and ``message`` (str),
    plus spending metadata (``_cost_usd``, ``_input_tokens``, etc.) and
    ``_raw_output`` from the CLI.
    """
    # Build the context dict that execute_claude will write as stdin JSON
    context_json = json.dumps(context, indent=2, default=str)
    eval_context = {
        "condition": prompt,
        "pipeline_context": context_json,
    }

    # Resolve system prompt file
    sys_prompt_tmp: str | None = None
    resolved_prompt_file: Path
    if prompt_file is not None and prompt_file.exists():
        resolved_prompt_file = prompt_file
    else:
        # Write inline fallback to temp file
        fallback = _INLINE_SYSTEM_PROMPT.format(
            schema_json=json.dumps(_AI_CONDITION_SCHEMA, indent=2)
        )
        fd_sys, sys_path = tempfile.mkstemp(suffix=".md", prefix="ai-cond-sys-")
        with os.fdopen(fd_sys, "w") as f:
            f.write(fallback)
        sys_prompt_tmp = sys_path
        resolved_prompt_file = Path(sys_path)

    log.info(
        "ai_condition_evaluating",
        task_id=task_id,
        stage=stage_num,
        prompt=prompt[:100],
    )

    try:
        claude_output = await execute_claude(
            prompt_file=resolved_prompt_file,
            context=eval_context,
            work_dir=work_dir,
            timeout_seconds=timeout_seconds,
            task_id=task_id,
            stage_num=stage_num,
            extra_env=extra_env,
            output_schema=_AI_CONDITION_SCHEMA,
            max_turns=max_turns,
            on_live_output=on_live_output,
            model=model,
        )
    finally:
        if sys_prompt_tmp:
            try:
                os.unlink(sys_prompt_tmp)
            except OSError:
                pass

    output = claude_output.structured
    output["_raw_output"] = claude_output.raw

    answer = bool(output.get("answer"))
    message = str(output.get("message", ""))
    log.info(
        "ai_condition_result",
        task_id=task_id,
        prompt=prompt[:100],
        answer=answer,
        message=message[:200],
    )
    return output


# ---------------------------------------------------------------------------
# Legacy condition bridge (sync)
# ---------------------------------------------------------------------------


def check_conditions(
    conditions: list[str] | list[dict[str, Any]], previous_output: dict[str, Any]
) -> bool:
    """Evaluate stage conditions against previous output (sync bridge).

    Supports both legacy string format ("field operator value") and
    new structured format (list of condition dicts with simple/ai keys).

    Note: ai: conditions are skipped in this sync bridge. Use
    evaluate_conditions() directly for full async AI support.
    """
    if not conditions:
        return True

    # Detect format: if first item is a dict, use new structured evaluation
    if conditions and isinstance(conditions[0], dict):
        context = _build_eval_context({}, previous_output)
        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            if "simple" in cond:
                raw = cond["simple"]
                val = raw if isinstance(raw, bool) else evaluate_simple_expression(str(raw), context)
                jump = cond.get("yes" if val else "no") or cond.get(True if val else False)
                if jump is not None:
                    return False  # jump means "don't proceed linearly"
        return True

    # Legacy string-based format
    for condition in conditions:
        if not isinstance(condition, str):
            continue
        parts = condition.split()
        if len(parts) < 3:
            continue

        field = parts[0]
        operator = parts[1]
        expected = " ".join(parts[2:])

        # Resolve field value via dot notation
        actual = _resolve_field(previous_output, field)
        if actual is None:
            return False

        actual_str = str(actual)

        if operator in ("==", "="):
            if actual_str != expected:
                return False
        elif operator == "!=":
            if actual_str == expected:
                return False
        elif operator in (">=", ">", "<=", "<"):
            if not _compare_complexity(actual_str, operator, expected):
                return False

    return True


def _compare_complexity(actual: str, operator: str, expected: str) -> bool:
    """Compare complexity values using ordered scale."""
    try:
        a = Complexity(actual.lower())
        b = Complexity(expected.lower())
    except ValueError:
        return False

    if operator == ">=":
        return a >= b
    elif operator == ">":
        return a > b
    elif operator == "<=":
        return a <= b
    elif operator == "<":
        return a < b
    return False

"""Tests for pipeline condition checking (both legacy and structured)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aquarco_supervisor.pipeline.executor import check_conditions
from aquarco_supervisor.pipeline.conditions import (
    ConditionResult,
    evaluate_conditions,
    evaluate_simple_expression,
)


# ---------------------------------------------------------------------------
# Legacy string-based conditions (backward compat)
# ---------------------------------------------------------------------------


def test_legacy_equality_condition() -> None:
    output = {"status": "pass"}
    assert check_conditions(["status == pass"], output) is True
    assert check_conditions(["status == fail"], output) is False


def test_legacy_inequality_condition() -> None:
    output = {"status": "pass"}
    assert check_conditions(["status != fail"], output) is True
    assert check_conditions(["status != pass"], output) is False


def test_legacy_complexity_gte() -> None:
    output = {"analysis": {"estimated_complexity": "high"}}
    assert check_conditions(["analysis.estimated_complexity >= medium"], output) is True
    assert check_conditions(["analysis.estimated_complexity >= epic"], output) is False


def test_legacy_empty_conditions() -> None:
    assert check_conditions([], {}) is True


def test_legacy_missing_field() -> None:
    assert check_conditions(["missing.field == value"], {}) is False


def test_legacy_nested_field_resolution() -> None:
    output = {"a": {"b": {"c": "value"}}}
    assert check_conditions(["a.b.c == value"], output) is True


# ---------------------------------------------------------------------------
# Simple expression parser
# ---------------------------------------------------------------------------


def test_simple_true_literal() -> None:
    assert evaluate_simple_expression("true", {}) is True


def test_simple_false_literal() -> None:
    assert evaluate_simple_expression("false", {}) is False


def test_simple_equality() -> None:
    ctx = {"severity": "major_issues"}
    assert evaluate_simple_expression("severity == major_issues", ctx) is True
    assert evaluate_simple_expression("severity == minor_issues", ctx) is False


def test_simple_inequality() -> None:
    ctx = {"severity": "major_issues"}
    assert evaluate_simple_expression("severity != minor_issues", ctx) is True
    assert evaluate_simple_expression("severity != major_issues", ctx) is False


def test_simple_numeric_comparison() -> None:
    ctx = {"coverage_percent": 90, "tests_failed": 0, "tests_added": 5}
    assert evaluate_simple_expression("coverage_percent >= 80", ctx) is True
    assert evaluate_simple_expression("coverage_percent < 80", ctx) is False
    assert evaluate_simple_expression("tests_failed == 0", ctx) is True
    assert evaluate_simple_expression("tests_added > 0", ctx) is True


def test_simple_or_expression() -> None:
    ctx = {"severity": "major_issues"}
    assert evaluate_simple_expression(
        "severity == major_issues || severity == blocking", ctx
    ) is True
    ctx2 = {"severity": "blocking"}
    assert evaluate_simple_expression(
        "severity == major_issues || severity == blocking", ctx2
    ) is True
    ctx3 = {"severity": "minor_issues"}
    assert evaluate_simple_expression(
        "severity == major_issues || severity == blocking", ctx3
    ) is False


def test_simple_and_expression() -> None:
    ctx = {"coverage_percent": 90, "tests_failed": 0}
    assert evaluate_simple_expression(
        "coverage_percent >= 80 && tests_failed == 0", ctx
    ) is True
    ctx2 = {"coverage_percent": 70, "tests_failed": 0}
    assert evaluate_simple_expression(
        "coverage_percent >= 80 && tests_failed == 0", ctx2
    ) is False


def test_simple_parentheses() -> None:
    ctx = {"tests_added": 0, "coverage_percent": 90, "tests_failed": 0}
    expr = "tests_added == 0 || (coverage_percent >= 80 && tests_failed == 0)"
    assert evaluate_simple_expression(expr, ctx) is True

    ctx2 = {"tests_added": 5, "coverage_percent": 90, "tests_failed": 0}
    assert evaluate_simple_expression(expr, ctx2) is True

    ctx3 = {"tests_added": 5, "coverage_percent": 70, "tests_failed": 2}
    assert evaluate_simple_expression(expr, ctx3) is False


def test_simple_cross_stage_reference() -> None:
    """Dotted field references resolve from nested stage outputs."""
    ctx = {"analysis": {"risks": ["risk1", "risk2"]}}
    # analysis.risks is a list, truthy
    assert evaluate_simple_expression("analysis.risks", ctx) is True


def test_simple_empty_expression() -> None:
    assert evaluate_simple_expression("", {}) is True


# ---------------------------------------------------------------------------
# Structured condition evaluation (evaluate_conditions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_conditions_simple_true_with_yes() -> None:
    """'simple: true' with 'yes: review' should jump to review."""
    conditions = [{"simple": "true", "yes": "review", "maxRepeats": 5}]
    result = await evaluate_conditions(conditions, {}, {}, {})
    assert result.jump_to == "review"
    assert result.matched is True


@pytest.mark.asyncio
async def test_evaluate_conditions_simple_false_with_no() -> None:
    """When condition evaluates to False, use the 'no' target."""
    conditions = [
        {"simple": "severity == major_issues || severity == blocking", "no": "test", "maxRepeats": 5}
    ]
    current_output = {"severity": "minor_issues"}
    result = await evaluate_conditions(conditions, {}, current_output, {})
    assert result.jump_to == "test"
    assert result.matched is True


@pytest.mark.asyncio
async def test_evaluate_conditions_no_jump_when_true_but_no_yes_field() -> None:
    """When condition is True but has no 'yes' field, skip to next condition."""
    conditions = [
        {"simple": "severity == major_issues", "no": "implementation"},
    ]
    current_output = {"severity": "major_issues"}
    result = await evaluate_conditions(conditions, {}, current_output, {})
    # True but no yes field -> skip; no more conditions -> no jump
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_max_repeats_exceeded() -> None:
    """When maxRepeats is exceeded, condition should be skipped."""
    conditions = [
        {"simple": "true", "yes": "review", "maxRepeats": 3}
    ]
    repeat_counts = {"review": 3}  # Already visited 3 times
    result = await evaluate_conditions(conditions, {}, {}, repeat_counts)
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_max_repeats_not_exceeded() -> None:
    conditions = [
        {"simple": "true", "yes": "review", "maxRepeats": 3}
    ]
    repeat_counts = {"review": 2}
    result = await evaluate_conditions(conditions, {}, {}, repeat_counts)
    assert result.jump_to == "review"
    assert result.matched is True


@pytest.mark.asyncio
async def test_evaluate_conditions_cross_stage_reference() -> None:
    """Cross-stage references like analysis.risks should resolve."""
    conditions = [
        {"simple": "analysis.estimated_complexity == high", "yes": "design"}
    ]
    stage_outputs = {"analysis": {"estimated_complexity": "high"}}
    result = await evaluate_conditions(conditions, stage_outputs, {}, {})
    assert result.jump_to == "design"


@pytest.mark.asyncio
async def test_evaluate_conditions_empty() -> None:
    result = await evaluate_conditions([], {}, {}, {})
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_multiple_fallthrough() -> None:
    """Multiple conditions: first match wins."""
    conditions = [
        {"simple": "severity == blocking", "no": "fix"},
        {"simple": "severity == major_issues", "no": "test"},
    ]
    current_output = {"severity": "minor_issues"}
    result = await evaluate_conditions(conditions, {}, current_output, {})
    # First condition: severity != blocking -> no: "fix"
    assert result.jump_to == "fix"


@pytest.mark.asyncio
async def test_evaluate_conditions_ai_without_evaluator() -> None:
    """AI conditions without an evaluator should be skipped."""
    conditions = [
        {"ai": "Is the code safe?", "yes": "deploy", "no": "fix"}
    ]
    result = await evaluate_conditions(conditions, {}, {}, {})
    assert result.jump_to is None
    assert result.matched is False


@pytest.mark.asyncio
async def test_evaluate_conditions_ai_with_evaluator() -> None:
    """AI conditions with evaluator should use the result."""
    async def mock_ai_evaluator(prompt: str, context: dict) -> bool:
        return True

    conditions = [
        {"ai": "Is the code safe?", "yes": "deploy", "no": "fix"}
    ]
    result = await evaluate_conditions(conditions, {}, {}, {}, ai_evaluator=mock_ai_evaluator)
    assert result.jump_to == "deploy"
    assert result.matched is True


@pytest.mark.asyncio
async def test_evaluate_conditions_ai_evaluator_returns_false() -> None:
    async def mock_ai_evaluator(prompt: str, context: dict) -> bool:
        return False

    conditions = [
        {"ai": "Is the code safe?", "yes": "deploy", "no": "fix"}
    ]
    result = await evaluate_conditions(conditions, {}, {}, {}, ai_evaluator=mock_ai_evaluator)
    assert result.jump_to == "fix"
    assert result.matched is True


# ---------------------------------------------------------------------------
# AI condition evaluator — prompts_dir loading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_ai_condition_uses_prompts_dir(tmp_path: Path) -> None:
    """evaluate_ai_condition loads system prompt from prompts_dir when the file exists."""
    import asyncio as _asyncio
    import json as _json
    import unittest.mock as mock

    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    custom_prompt = "Custom condition evaluator prompt for testing."
    (prompts_dir / "condition-evaluator-agent.md").write_text(custom_prompt)

    captured_sys_prompt: list[str] = []

    # Valid NDJSON result line that evaluate_ai_condition can parse
    ndjson_line = _json.dumps({
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": True, "reasoning": "mocked"},
    })

    class _AsyncLineIter:
        """Minimal async iterator over a list of byte lines."""

        def __init__(self, lines: list[bytes]) -> None:
            self._iter = iter(lines)

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    async def _fake_subprocess(*args, **kwargs):
        # Capture the system-prompt file content before the function deletes it
        args_list = list(args)
        for i, arg in enumerate(args_list):
            if arg == "--system-prompt-file" and i + 1 < len(args_list):
                try:
                    captured_sys_prompt.append(Path(args_list[i + 1]).read_text())
                except OSError:
                    pass
                break

        proc = mock.MagicMock()
        proc.returncode = 0
        proc.stdout = _AsyncLineIter([f"{ndjson_line}\n".encode()])
        proc.stderr = mock.AsyncMock()
        proc.stderr.read = mock.AsyncMock(return_value=b"")
        proc.kill = mock.MagicMock()
        proc.wait = mock.AsyncMock(return_value=None)
        return proc

    with mock.patch.object(_asyncio, "create_subprocess_exec", side_effect=_fake_subprocess):
        result = await evaluate_ai_condition(
            "Is this test working?",
            {"status": "ok"},
            prompts_dir=prompts_dir,
        )

    assert len(captured_sys_prompt) == 1, "System prompt file was not passed to subprocess"
    assert custom_prompt in captured_sys_prompt[0], (
        "Custom prompt from prompts_dir was not used as system prompt; "
        f"captured: {captured_sys_prompt[0][:200]!r}"
    )
    assert result is True


def test_inline_system_prompt_is_fallback(tmp_path: Path) -> None:
    """When prompts_dir has no condition-evaluator-agent.md, inline prompt is used."""
    from aquarco_supervisor.pipeline.conditions import _INLINE_SYSTEM_PROMPT
    # The inline prompt should be non-empty
    assert len(_INLINE_SYSTEM_PROMPT) > 50
    assert "condition evaluator" in _INLINE_SYSTEM_PROMPT.lower()
    assert "answer" in _INLINE_SYSTEM_PROMPT


def test_inline_system_prompt_contains_schema_placeholder(tmp_path: Path) -> None:
    """The inline prompt has a {schema_json} placeholder for formatting."""
    from aquarco_supervisor.pipeline.conditions import _INLINE_SYSTEM_PROMPT
    assert "{schema_json}" in _INLINE_SYSTEM_PROMPT


def test_condition_evaluator_md_schema_matches_inline_schema() -> None:
    """The schema hardcoded in condition-evaluator-agent.md must match _AI_CONDITION_SCHEMA.

    This test catches drift between the externalized prompt file and the inline
    fallback: if _AI_CONDITION_SCHEMA changes (e.g. new required fields), the
    .md file must be updated in lock-step.
    """
    import json as _json
    import re as _re

    from aquarco_supervisor.pipeline.conditions import _AI_CONDITION_SCHEMA

    # Locate the prompt file relative to the repo root (four levels up from this file)
    this_file = Path(__file__)
    repo_root = this_file.parents[4]  # tests/test_pipeline/ -> tests/ -> python/ -> supervisor/ -> repo root
    prompt_file = repo_root / "config" / "agents" / "prompts" / "condition-evaluator-agent.md"

    assert prompt_file.exists(), f"Prompt file not found: {prompt_file}"
    content = prompt_file.read_text()

    # Extract the first JSON code block from the markdown file
    match = _re.search(r"```json\s*([\s\S]*?)```", content)
    assert match is not None, "No JSON code block found in condition-evaluator-agent.md"

    embedded_schema = _json.loads(match.group(1).strip())
    assert embedded_schema == _AI_CONDITION_SCHEMA, (
        "Schema in condition-evaluator-agent.md has diverged from _AI_CONDITION_SCHEMA.\n"
        f"Embedded: {_json.dumps(embedded_schema, indent=2)}\n"
        f"Expected: {_json.dumps(_AI_CONDITION_SCHEMA, indent=2)}"
    )


@pytest.mark.asyncio
async def test_file_based_prompt_is_used_verbatim_no_schema_substitution(
    tmp_path: Path,
) -> None:
    """When prompts_dir provides condition-evaluator-agent.md, the file content
    is used as-is — the ``{schema_json}`` placeholder is NOT substituted.

    This is intentional: the .md file already contains the schema statically,
    so no runtime formatting is needed.  The inline fallback uses ``.format()``
    while the file path does not.
    """
    import asyncio as _asyncio
    import json as _json
    import unittest.mock as mock

    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    # Write a prompt file that contains a literal {schema_json} placeholder
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    placeholder_prompt = "Evaluate. Schema: {schema_json}"
    (prompts_dir / "condition-evaluator-agent.md").write_text(placeholder_prompt)

    ndjson_line = _json.dumps({
        "type": "result",
        "subtype": "success",
        "structured_output": {"answer": False, "reasoning": "mocked"},
    })
    captured_sys_prompt: list[str] = []

    class _AsyncLineIter:
        def __init__(self, lines: list[bytes]) -> None:
            self._iter = iter(lines)

        def __aiter__(self):
            return self

        async def __anext__(self) -> bytes:
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration

    async def _fake_subprocess(*args, **kwargs):
        args_list = list(args)
        for i, arg in enumerate(args_list):
            if arg == "--system-prompt-file" and i + 1 < len(args_list):
                try:
                    captured_sys_prompt.append(Path(args_list[i + 1]).read_text())
                except OSError:
                    pass
                break
        proc = mock.MagicMock()
        proc.returncode = 0
        proc.stdout = _AsyncLineIter([f"{ndjson_line}\n".encode()])
        proc.stderr = mock.AsyncMock()
        proc.stderr.read = mock.AsyncMock(return_value=b"")
        proc.kill = mock.MagicMock()
        proc.wait = mock.AsyncMock(return_value=None)
        return proc

    with mock.patch.object(_asyncio, "create_subprocess_exec", side_effect=_fake_subprocess):
        await evaluate_ai_condition(
            "Does this work?",
            {"status": "ok"},
            prompts_dir=prompts_dir,
        )

    assert len(captured_sys_prompt) == 1
    # The file was used verbatim — {schema_json} was NOT replaced
    assert captured_sys_prompt[0] == placeholder_prompt, (
        "File-based prompt must be used verbatim; "
        f"got: {captured_sys_prompt[0]!r}"
    )

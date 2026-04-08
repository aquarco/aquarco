"""Additional tests for push review coverage (commits 5ba4d0bf..1f63f261).

Covers gaps not addressed by test_model_per_agent.py:
  - RetryableError propagation in conditions.py (import fix)
  - Flat-directory agent discovery with model field (legacy support)
  - git push --force in executor (changed from --force-with-lease)
  - Model flag ordering in CLI args (placed before tool flags)
  - Condition evaluator model passthrough from executor
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from aquarco_supervisor.cli.claude import ClaudeOutput, execute_claude
from aquarco_supervisor.cli import claude as claude_mod
from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import (
    RateLimitError,
    RetryableError,
    ServerError,
    OverloadedError,
)
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry
from aquarco_supervisor.pipeline.conditions import (
    ConditionResult,
    evaluate_ai_condition,
    evaluate_conditions,
    _evaluate_single_condition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc_mock(returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=None)
    return proc


def _make_temp_file(path: Path) -> tuple[int, str]:
    fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600)
    return fd, str(path)


@pytest.fixture(autouse=True)
def _patch_log_dir(tmp_path: Path) -> Any:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    with patch.object(claude_mod, "LOG_DIR", log_dir):
        yield


# ===========================================================================
# 1. RetryableError propagation in conditions.py
# ===========================================================================


@pytest.mark.asyncio
async def test_ai_condition_propagates_rate_limit_error() -> None:
    """RateLimitError (a RetryableError subclass) must propagate from
    _evaluate_single_condition, not be swallowed by the generic except."""
    async def failing_evaluator(prompt: str, context: dict) -> tuple[bool, str]:
        raise RateLimitError("429 rate limited", session_id="sess123")

    cond = {"ai": "Is this ready?"}
    with pytest.raises(RateLimitError, match="429"):
        await _evaluate_single_condition(cond, {}, failing_evaluator)


@pytest.mark.asyncio
async def test_ai_condition_propagates_server_error() -> None:
    """ServerError (500) should propagate through conditions for retry."""
    async def failing_evaluator(prompt: str, context: dict) -> tuple[bool, str]:
        raise ServerError("500 internal server error")

    cond = {"ai": "Check quality"}
    with pytest.raises(ServerError, match="500"):
        await _evaluate_single_condition(cond, {}, failing_evaluator)


@pytest.mark.asyncio
async def test_ai_condition_propagates_overloaded_error() -> None:
    """OverloadedError (529) should propagate through conditions for retry."""
    async def failing_evaluator(prompt: str, context: dict) -> tuple[bool, str]:
        raise OverloadedError("529 overloaded")

    cond = {"ai": "Evaluate"}
    with pytest.raises(OverloadedError, match="529"):
        await _evaluate_single_condition(cond, {}, failing_evaluator)


@pytest.mark.asyncio
async def test_ai_condition_non_retryable_error_returns_none() -> None:
    """Non-RetryableError exceptions should be caught and return None."""
    async def failing_evaluator(prompt: str, context: dict) -> tuple[bool, str]:
        raise ValueError("unexpected error")

    cond = {"ai": "Evaluate"}
    result = await _evaluate_single_condition(cond, {}, failing_evaluator)
    assert result is None


@pytest.mark.asyncio
async def test_evaluate_conditions_propagates_retryable_from_ai() -> None:
    """evaluate_conditions (top-level) lets RetryableError bubble up from ai evaluator."""
    async def failing_evaluator(prompt: str, context: dict) -> tuple[bool, str]:
        raise RateLimitError("429 hit during condition eval")

    conditions = [
        {"ai": "Is the code ready?", "yes": "next-stage", "no": "retry-stage"},
    ]
    with pytest.raises(RateLimitError):
        await evaluate_conditions(
            conditions,
            stage_outputs={},
            current_output={"status": "ok"},
            repeat_counts={},
            ai_evaluator=failing_evaluator,
        )


@pytest.mark.asyncio
async def test_evaluate_ai_condition_retryable_propagates_through_execute_claude() -> None:
    """evaluate_ai_condition lets RateLimitError from execute_claude propagate."""
    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        new_callable=AsyncMock,
        side_effect=RateLimitError("429 from CLI", session_id="s1"),
    ):
        with pytest.raises(RateLimitError, match="429"):
            await evaluate_ai_condition(
                prompt="Is the implementation complete?",
                context={"task": "t1"},
                work_dir="/tmp/test",
                task_id="task-1",
                stage_num=0,
            )


# ===========================================================================
# 2. Flat-directory agent discovery preserves model field
# ===========================================================================


@pytest.mark.asyncio
async def test_flat_scan_discovery_loads_model(tmp_path: Path) -> None:
    """Flat-directory scan (no system/pipeline subdirs) preserves the model field."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)

    agent_md = (
        "---\n"
        "name: flat-model-agent\n"
        "model: claude-sonnet-4-6\n"
        "categories:\n"
        "  - implementation\n"
        "priority: 10\n"
        "---\n"
        "# Flat model agent prompt\n"
    )
    (agents_dir / "flat-model-agent.md").write_text(agent_md)

    db = AsyncMock(spec=Database)
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock(return_value=None)

    reg = AgentRegistry(db, str(agents_dir))
    await reg.load(str(tmp_path / "nonexistent-registry.json"))

    assert reg.get_agent_model("flat-model-agent") == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_flat_scan_discovery_model_absent_returns_none(tmp_path: Path) -> None:
    """Flat-scan agent without model field returns None from get_agent_model."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)

    agent_md = (
        "---\n"
        "name: no-model-agent\n"
        "categories:\n"
        "  - review\n"
        "priority: 20\n"
        "---\n"
        "# No model agent prompt\n"
    )
    (agents_dir / "no-model-agent.md").write_text(agent_md)

    db = AsyncMock(spec=Database)
    db.fetch_all = AsyncMock(return_value=[])
    db.execute = AsyncMock(return_value=None)

    reg = AgentRegistry(db, str(agents_dir))
    await reg.load(str(tmp_path / "nonexistent-registry.json"))

    assert reg.get_agent_model("no-model-agent") is None


# ===========================================================================
# 3. git push --force in executor (changed from --force-with-lease)
# ===========================================================================


@pytest.mark.asyncio
async def test_executor_push_uses_force_flag() -> None:
    """_create_pipeline_pr uses --force (not --force-with-lease) for push."""
    from aquarco_supervisor.pipeline.executor import PipelineExecutor
    from aquarco_supervisor.task_queue import TaskQueue

    mock_db = AsyncMock(spec=Database)
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = MagicMock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, [])

    # Mock task data — no head_branch means feature pipeline (creates PR)
    mock_task = MagicMock()
    mock_task.title = "Test feature"
    mock_task.pipeline = "feature-pipeline"
    mock_task.source_ref = None
    mock_task.initial_context = {}  # no head_branch → feature path
    mock_task.last_completed_stage = None

    mock_tq.get_task = AsyncMock(return_value=mock_task)
    mock_tq.store_pr_info = AsyncMock()

    captured_cmds: list[list[str]] = []

    async def mock_run_git(clone_dir: str, *args: str) -> str:
        captured_cmds.append([clone_dir, *args])
        return ""

    async def mock_get_ahead_count(clone_dir: str, branch: str, base: str) -> int:
        return 3

    async def mock_run_cmd(*args: str, check: bool = True) -> str:
        return ""

    with patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        side_effect=mock_run_git,
    ), patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        side_effect=mock_get_ahead_count,
    ), patch(
        "aquarco_supervisor.pipeline.executor._run_cmd",
        side_effect=mock_run_cmd,
    ):
        # Mock _get_repo_slug and _get_repo_branch
        executor._get_repo_slug = AsyncMock(return_value="org/repo")
        executor._get_repo_branch = AsyncMock(return_value="main")

        await executor._create_pipeline_pr(
            task_id="test-task",
            branch_name="aquarco/test-branch",
            clone_dir="/repos/test",
            stage_output={"summary": "Test PR"},
        )

    # Find the push command
    push_cmds = [cmd for cmd in captured_cmds if "push" in cmd]
    assert len(push_cmds) >= 1, f"Expected push command, got: {captured_cmds}"
    push_cmd = push_cmds[0]
    assert "--force" in push_cmd, f"Expected --force flag in push: {push_cmd}"
    assert "--force-with-lease" not in push_cmd, f"Should not use --force-with-lease: {push_cmd}"


# ===========================================================================
# 4. Model flag ordering in CLI args
# ===========================================================================


@pytest.mark.asyncio
async def test_model_flag_placed_before_tool_flags(tmp_path: Path) -> None:
    """--model flag appears before --allowedTools in CLI args."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            model="claude-sonnet-4-6",
            allowed_tools=["Read", "Write"],
        )

    args_list = [str(a) for a in captured_args]
    model_idx = args_list.index("--model")
    allowed_idx = args_list.index("--allowedTools")
    assert model_idx < allowed_idx, (
        f"--model (idx {model_idx}) should come before --allowedTools (idx {allowed_idx})"
    )


@pytest.mark.asyncio
async def test_model_flag_with_denied_tools(tmp_path: Path) -> None:
    """--model flag appears before --disallowedTools in CLI args."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            model="claude-haiku-4-5",
            denied_tools=["Bash"],
        )

    args_list = [str(a) for a in captured_args]
    model_idx = args_list.index("--model")
    denied_idx = args_list.index("--disallowedTools")
    assert model_idx < denied_idx


# ===========================================================================
# 5. Resume mode tool restriction scoping
# ===========================================================================


@pytest.mark.asyncio
async def test_resume_mode_skips_tool_restrictions(tmp_path: Path) -> None:
    """In resume mode, allowedTools/disallowedTools are not added to args."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            model="claude-sonnet-4-6",
            allowed_tools=["Read", "Write"],
            denied_tools=["Bash"],
            resume_session_id="abc12345",
        )

    args_str = " ".join(str(a) for a in captured_args)
    # Resume mode should skip tool restrictions
    assert "--allowedTools" not in args_str
    assert "--disallowedTools" not in args_str
    # But model should still be present
    assert "--model" in args_str
    # And --resume should be present
    assert "--resume" in args_str


@pytest.mark.asyncio
async def test_resume_mode_skips_output_schema(tmp_path: Path) -> None:
    """In resume mode, --append-system-prompt and --json-schema are not added."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], None, False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
            resume_session_id="abc12345",
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--append-system-prompt" not in args_str
    assert "--json-schema" not in args_str
    assert "--system-prompt-file" not in args_str


# ===========================================================================
# 7. Condition evaluator: model parameter forwarded to execute_claude kwargs
# ===========================================================================


@pytest.mark.asyncio
async def test_evaluate_ai_condition_forwards_all_kwargs() -> None:
    """evaluate_ai_condition passes extra_env, timeout, max_turns, AND model."""
    mock_output = ClaudeOutput(
        structured={"answer": True, "message": "looks good"},
        raw='{"answer": true, "message": "looks good"}',
    )

    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        new_callable=AsyncMock,
        return_value=mock_output,
    ) as mock_exec:
        await evaluate_ai_condition(
            prompt="Is the code reviewed?",
            context={"status": "done"},
            work_dir="/tmp/work",
            task_id="t-1",
            stage_num=2,
            timeout_seconds=60,
            max_turns=3,
            extra_env={"CUSTOM": "val"},
            model="claude-haiku-4-5",
        )

    call_kwargs = mock_exec.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5"
    assert call_kwargs["timeout_seconds"] == 60
    assert call_kwargs["max_turns"] == 3
    assert call_kwargs["extra_env"] == {"CUSTOM": "val"}
    assert call_kwargs["task_id"] == "t-1"
    assert call_kwargs["stage_num"] == 2

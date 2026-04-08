"""Tests for retryable-error routing in PipelineExecutor.

Covers:
  - _cooldown_for_error() dispatch for OverloadedError, ServerError, RateLimitError
  - _execute_running_phase() catches RetryableError subclasses and routes to postpone_task
  - _execute_running_phase() does NOT route non-retryable StageError to postpone_task
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.claude import ClaudeOutput
from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import (
    OverloadedError,
    RateLimitError,
    ServerError,
    StageError,
)
from aquarco_supervisor.exceptions import _cooldown_for_error
from aquarco_supervisor.pipeline.executor import PipelineExecutor
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# _cooldown_for_error (pure function — no mocks needed)
# ---------------------------------------------------------------------------


def test_cooldown_for_overloaded_error() -> None:
    """OverloadedError (529) gets 15-minute cooldown and 24 max retries."""
    cooldown, max_retries = _cooldown_for_error(OverloadedError("overloaded"))
    assert cooldown == 15
    assert max_retries == 24


def test_cooldown_for_server_error() -> None:
    """ServerError (500) gets 30-minute cooldown and 12 max retries."""
    cooldown, max_retries = _cooldown_for_error(ServerError("internal error"))
    assert cooldown == 30
    assert max_retries == 12


def test_cooldown_for_rate_limit_error() -> None:
    """RateLimitError (429) gets 60-minute cooldown and 24 max retries."""
    cooldown, max_retries = _cooldown_for_error(RateLimitError("rate limit"))
    assert cooldown == 60
    assert max_retries == 24


def test_cooldown_overloaded_has_shorter_cooldown_than_server() -> None:
    """Overloaded cooldown is shorter than ServerError cooldown (529 is transient)."""
    overloaded_minutes, _ = _cooldown_for_error(OverloadedError("x"))
    server_minutes, _ = _cooldown_for_error(ServerError("x"))
    assert overloaded_minutes < server_minutes


def test_cooldown_server_has_fewer_max_retries_than_others() -> None:
    """ServerError has fewer max retries than OverloadedError and RateLimitError."""
    _, server_max = _cooldown_for_error(ServerError("x"))
    _, overloaded_max = _cooldown_for_error(OverloadedError("x"))
    _, rate_max = _cooldown_for_error(RateLimitError("x"))
    assert server_max < overloaded_max
    assert server_max < rate_max


# ---------------------------------------------------------------------------
# Helpers for _execute_running_phase tests
# ---------------------------------------------------------------------------


def _make_executor(
    sample_pipelines: list[Any],
    *,
    execute_claude_side_effect: Any = None,
) -> tuple[PipelineExecutor, AsyncMock, AsyncMock]:
    """Build a PipelineExecutor with minimal mocks for running-phase tests."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_db.execute = AsyncMock()

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_task_context = AsyncMock(return_value={})
    mock_tq.update_checkpoint = AsyncMock()
    mock_tq.postpone_task = AsyncMock()
    mock_tq.fail_task = AsyncMock()
    mock_tq.update_task_status = AsyncMock()
    mock_tq.store_stage_output = AsyncMock()
    mock_tq.create_stage = AsyncMock()

    mock_registry = MagicMock()
    mock_registry.select_agent = AsyncMock(return_value="impl-agent")
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/impl.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    return executor, mock_tq, mock_db


def _minimal_planned_stages(category: str = "implement") -> list[dict[str, Any]]:
    """Single-stage planned stages list."""
    return [{"category": category, "agents": ["impl-agent"], "parallel": False}]


def _minimal_stage_defs(category: str = "implement") -> list[dict[str, Any]]:
    """Single-stage definitions with no exit conditions."""
    return [{"name": "impl", "category": category, "required": True, "conditions": []}]


# ---------------------------------------------------------------------------
# _execute_running_phase retryable routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_phase_routes_server_error_to_postpone(
    sample_pipelines: Any,
) -> None:
    """When _execute_planned_stage raises ServerError, postpone_task is called."""
    executor, mock_tq, _ = _make_executor(sample_pipelines)

    planned = _minimal_planned_stages()
    stage_defs = _minimal_stage_defs()

    with patch(
        "aquarco_supervisor.pipeline.executor.PipelineExecutor._execute_planned_stage",
        new_callable=AsyncMock,
        side_effect=ServerError("API 500"),
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ):
        failed = await executor._execute_running_phase(
            "task-500", planned, stage_defs, "/repos/test", "branch-x"
        )

    assert failed is True
    mock_tq.postpone_task.assert_awaited_once()
    call_kwargs = mock_tq.postpone_task.call_args
    assert call_kwargs.kwargs.get("cooldown_minutes") == 30
    assert call_kwargs.kwargs.get("max_retries") == 12


@pytest.mark.asyncio
async def test_running_phase_routes_overloaded_error_to_postpone(
    sample_pipelines: Any,
) -> None:
    """When _execute_planned_stage raises OverloadedError, postpone_task is called."""
    executor, mock_tq, _ = _make_executor(sample_pipelines)

    planned = _minimal_planned_stages()
    stage_defs = _minimal_stage_defs()

    with patch(
        "aquarco_supervisor.pipeline.executor.PipelineExecutor._execute_planned_stage",
        new_callable=AsyncMock,
        side_effect=OverloadedError("API 529"),
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ):
        failed = await executor._execute_running_phase(
            "task-529", planned, stage_defs, "/repos/test", "branch-x"
        )

    assert failed is True
    mock_tq.postpone_task.assert_awaited_once()
    call_kwargs = mock_tq.postpone_task.call_args
    assert call_kwargs.kwargs.get("cooldown_minutes") == 15
    assert call_kwargs.kwargs.get("max_retries") == 24


@pytest.mark.asyncio
async def test_running_phase_routes_rate_limit_error_to_postpone(
    sample_pipelines: Any,
) -> None:
    """When _execute_planned_stage raises RateLimitError, postpone_task is called."""
    executor, mock_tq, _ = _make_executor(sample_pipelines)

    planned = _minimal_planned_stages()
    stage_defs = _minimal_stage_defs()

    with patch(
        "aquarco_supervisor.pipeline.executor.PipelineExecutor._execute_planned_stage",
        new_callable=AsyncMock,
        side_effect=RateLimitError("API 429"),
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ):
        failed = await executor._execute_running_phase(
            "task-429", planned, stage_defs, "/repos/test", "branch-x"
        )

    assert failed is True
    mock_tq.postpone_task.assert_awaited_once()
    call_kwargs = mock_tq.postpone_task.call_args
    assert call_kwargs.kwargs.get("cooldown_minutes") == 60
    assert call_kwargs.kwargs.get("max_retries") == 24


@pytest.mark.asyncio
async def test_running_phase_retryable_does_not_call_fail_task(
    sample_pipelines: Any,
) -> None:
    """A retryable error postpones the task — it must NOT call fail_task."""
    executor, mock_tq, _ = _make_executor(sample_pipelines)

    planned = _minimal_planned_stages()
    stage_defs = _minimal_stage_defs()

    with patch(
        "aquarco_supervisor.pipeline.executor.PipelineExecutor._execute_planned_stage",
        new_callable=AsyncMock,
        side_effect=ServerError("500"),
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ):
        await executor._execute_running_phase(
            "task-500", planned, stage_defs, "/repos/test", "branch-x"
        )

    mock_tq.fail_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_phase_stage_error_postpones_for_retry(
    sample_pipelines: Any,
) -> None:
    """A non-retryable StageError on a required stage postpones for retry."""
    executor, mock_tq, _ = _make_executor(sample_pipelines)

    planned = _minimal_planned_stages()
    stage_defs = _minimal_stage_defs()

    with patch(
        "aquarco_supervisor.pipeline.executor.PipelineExecutor._execute_planned_stage",
        new_callable=AsyncMock,
        side_effect=StageError("stage failed"),
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ):
        failed = await executor._execute_running_phase(
            "task-stage-err", planned, stage_defs, "/repos/test", "branch-x"
        )

    assert failed is True
    mock_tq.postpone_task.assert_awaited_once()
    # No checkpoint when the first stage fails — no prior completed stage to reference
    mock_tq.update_checkpoint.assert_not_awaited()
    mock_tq.fail_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_running_phase_retryable_checkpoints_before_postpone(
    sample_pipelines: Any,
) -> None:
    """update_checkpoint is called before postpone_task on retryable errors
    when there is a previously completed stage."""
    executor, mock_tq, _ = _make_executor(sample_pipelines)

    # Two stages: first succeeds, second hits retryable error
    planned = [
        {"category": "analyze", "agents": ["agent"], "parallel": False, "validation": []},
        {"category": "implement", "agents": ["agent"], "parallel": False, "validation": []},
    ]
    stage_defs = [
        {"name": "analyze", "category": "analyze", "required": True, "conditions": []},
        {"name": "implement", "category": "implement", "required": True, "conditions": []},
    ]

    call_count = 0

    async def _stage_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"result": "ok"}, 100  # first stage succeeds, returns (output, stage_id)
        raise OverloadedError("529")

    call_order: list[str] = []
    mock_tq.update_checkpoint.side_effect = lambda *a, **kw: call_order.append("checkpoint") or None
    mock_tq.postpone_task.side_effect = lambda *a, **kw: call_order.append("postpone") or None

    with patch(
        "aquarco_supervisor.pipeline.executor.PipelineExecutor._execute_planned_stage",
        new_callable=AsyncMock,
        side_effect=_stage_side_effect,
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._auto_commit",
        new_callable=AsyncMock,
    ):
        await executor._execute_running_phase(
            "task-order", planned, stage_defs, "/repos/test", "branch-x"
        )

    assert call_order.count("checkpoint") >= 2  # success checkpoint + error checkpoint
    assert "postpone" in call_order
    # The error-path checkpoint must come before postpone
    last_checkpoint_idx = len(call_order) - 1 - call_order[::-1].index("checkpoint")
    assert last_checkpoint_idx < call_order.index("postpone")


@pytest.mark.asyncio
async def test_running_phase_success_does_not_postpone(
    sample_pipelines: Any,
) -> None:
    """A successful stage run never calls postpone_task."""
    executor, mock_tq, _ = _make_executor(sample_pipelines)

    planned = _minimal_planned_stages()
    stage_defs = _minimal_stage_defs()

    with patch(
        "aquarco_supervisor.pipeline.executor.PipelineExecutor._execute_planned_stage",
        new_callable=AsyncMock,
        return_value=({"result": "ok"}, 99),
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._auto_commit",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor.evaluate_conditions",
        new_callable=AsyncMock,
        return_value=MagicMock(jump_to=None),
    ):
        failed = await executor._execute_running_phase(
            "task-ok", planned, stage_defs, "/repos/test", "branch-x"
        )

    assert failed is False
    mock_tq.postpone_task.assert_not_awaited()
    mock_tq.fail_task.assert_not_awaited()

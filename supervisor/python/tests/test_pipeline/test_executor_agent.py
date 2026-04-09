"""Tests for PipelineExecutor._execute_agent and ClaudeOutput integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.claude import ClaudeOutput
from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import PipelineError
from aquarco_supervisor.pipeline.executor import PipelineExecutor
from aquarco_supervisor.task_queue import TaskQueue


@pytest.mark.asyncio
async def test_execute_agent_returns_structured_with_agent_name_and_raw(
    sample_pipelines: Any,
) -> None:
    """_execute_agent returns structured output with _agent_name and _raw_output."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"clone_dir": "/repos/test", "clone_status": "ready", "branch": "main"}
    )
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = MagicMock()
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=["Bash", "Read"])
    mock_registry.get_denied_tools = MagicMock(return_value=["Write"])
    mock_registry.get_agent_environment = MagicMock(return_value={"KEY": "val"})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    raw_text = '{"result": "done"}'
    claude_output = ClaudeOutput(
        structured={"result": "done", "complexity": "low"},
        raw=raw_text,
    )

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=claude_output,
    ), patch("aquarco_supervisor.pipeline.executor.Path"):
        output = await executor._invoker.execute_agent(
            "test-agent", "task-1", {"key": "val"}, 0,
            resolve_clone_dir=executor._resolve_clone_dir,
        )

    assert output["_agent_name"] == "test-agent"
    assert output["result"] == "done"
    assert output["complexity"] == "low"


@pytest.mark.asyncio
async def test_execute_agent_uses_registry(sample_pipelines: Any) -> None:
    """_execute_agent uses the registry for config lookups."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"clone_dir": "/repos/test", "clone_status": "ready", "branch": "main"}
    )
    mock_tq = AsyncMock(spec=TaskQueue)

    mock_registry = MagicMock()
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/agent-1.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_agent_max_turns = MagicMock(return_value=20)
    mock_registry.get_agent_max_cost = MagicMock(return_value=5.0)
    mock_registry.get_agent_model = MagicMock(return_value=None)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    claude_output = ClaudeOutput(structured={"ok": True}, raw="{}")

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=claude_output,
    ) as mock_execute, patch("aquarco_supervisor.pipeline.executor.Path"):
        output = await executor._invoker.execute_agent(
            "agent-1", "task-1", {}, 0,
            resolve_clone_dir=executor._resolve_clone_dir,
        )

    # Registry methods should be called
    mock_registry.get_agent_prompt_file.assert_called_once_with("agent-1")
    mock_registry.get_agent_timeout.assert_called_once_with("agent-1")


@pytest.mark.asyncio
async def test_execute_agent_with_work_dir_override(sample_pipelines: Any) -> None:
    """_execute_agent uses work_dir when provided instead of resolving from DB."""
    mock_db = AsyncMock(spec=Database)
    mock_tq = AsyncMock(spec=TaskQueue)

    mock_registry = MagicMock()
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    claude_output = ClaudeOutput(structured={}, raw="")

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=claude_output,
    ) as mock_execute, patch("aquarco_supervisor.pipeline.executor.Path"):
        await executor._invoker.execute_agent(
            "agent-1", "task-1", {}, 0,
            work_dir="/custom/dir",
        )

    # Should use the provided work_dir, not call _resolve_clone_dir
    call_kwargs = mock_execute.call_args.kwargs
    assert call_kwargs["work_dir"] == "/custom/dir"
    mock_db.fetch_one.assert_not_called()


@pytest.mark.asyncio
async def test_execute_agent_passes_output_schema(sample_pipelines: Any) -> None:
    """_execute_agent passes output_schema from registry to execute_claude."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"clone_dir": "/repos/test", "clone_status": "ready", "branch": "main"}
    )
    mock_tq = AsyncMock(spec=TaskQueue)

    schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
    mock_registry = MagicMock()
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=schema)

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    claude_output = ClaudeOutput(structured={"summary": "ok"}, raw="{}")

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=claude_output,
    ) as mock_execute, patch("aquarco_supervisor.pipeline.executor.Path"):
        await executor._invoker.execute_agent(
            "agent-1", "task-1", {}, 0,
            resolve_clone_dir=executor._resolve_clone_dir,
        )

    call_kwargs = mock_execute.call_args.kwargs
    assert call_kwargs["output_schema"] == schema


@pytest.mark.asyncio
async def test_setup_branch_rejects_unsafe_branch_name(sample_pipelines: Any) -> None:
    """_setup_branch rejects branch names with flag injection patterns."""
    executor = PipelineExecutor(AsyncMock(), AsyncMock(), AsyncMock(), sample_pipelines)

    with pytest.raises(PipelineError, match="Rejected unsafe head_branch"):
        await executor._setup_branch(
            "task-1",
            {"head_branch": "--delete-all"},
            "/repos/test",
        )


@pytest.mark.asyncio
async def test_setup_branch_reuses_existing_branch(sample_pipelines: Any) -> None:
    """When branch creation fails (already exists), checkout and reset."""
    mock_tq = AsyncMock()
    task = MagicMock()
    task.title = "Test feature"
    mock_tq.get_task = AsyncMock(return_value=task)

    mock_db = AsyncMock()
    mock_db.fetch_one = AsyncMock(return_value={"branch": "main"})
    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    call_count = 0

    async def mock_run_git(clone_dir: str, *args: str, **kwargs: Any) -> str:
        nonlocal call_count
        call_count += 1
        if args[0] == "fetch":
            return ""
        if args[0] == "checkout" and args[1] == "-b":
            raise RuntimeError("branch already exists")
        return ""

    with patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        side_effect=mock_run_git,
    ):
        branch = await executor._setup_branch("task-1", {}, "/repos/test")

    assert branch.startswith("aquarco/task-1/")

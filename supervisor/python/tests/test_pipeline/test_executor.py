"""Tests for pipeline executor utilities."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.claude import ClaudeOutput
from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import PipelineError, StageError
from aquarco_supervisor.pipeline.executor import (
    PipelineExecutor,
    _auto_commit,
    _compare_complexity,
    _get_ahead_count,
    _push_if_ahead,
    _resolve_field,
    check_conditions,
)
from aquarco_supervisor.task_queue import TaskQueue
from aquarco_supervisor.utils import run_cmd as _run_cmd
from aquarco_supervisor.utils import run_git as _run_git
from aquarco_supervisor.utils import url_to_slug


def test_https_url_to_slug() -> None:
    assert url_to_slug("https://github.com/owner/repo.git") == "owner/repo"
    assert url_to_slug("https://github.com/owner/repo") == "owner/repo"


def test_ssh_url_to_slug() -> None:
    assert url_to_slug("git@github.com:owner/repo.git") == "owner/repo"
    assert url_to_slug("git@github.com:owner/repo") == "owner/repo"


def test_invalid_url_to_slug() -> None:
    assert url_to_slug("not-a-url") is None


# --- check_conditions ---

def test_check_conditions_empty_conditions() -> None:
    """Empty conditions always pass."""
    assert check_conditions([], {}) is True


def test_check_conditions_eq_passes() -> None:
    output = {"status": "ok"}
    assert check_conditions(["status == ok"], output) is True


def test_check_conditions_eq_fails() -> None:
    output = {"status": "failed"}
    assert check_conditions(["status == ok"], output) is False


def test_check_conditions_neq_passes() -> None:
    output = {"status": "failed"}
    assert check_conditions(["status != ok"], output) is True


def test_check_conditions_neq_fails() -> None:
    output = {"status": "ok"}
    assert check_conditions(["status != ok"], output) is False


def test_check_conditions_missing_field_returns_false() -> None:
    output = {"other": "value"}
    assert check_conditions(["status == ok"], output) is False


def test_check_conditions_dotted_path() -> None:
    output = {"analysis": {"estimated_complexity": "high"}}
    assert check_conditions(["analysis.estimated_complexity == high"], output) is True


def test_check_conditions_complexity_gte() -> None:
    output = {"analysis": {"estimated_complexity": "high"}}
    assert check_conditions(["analysis.estimated_complexity >= medium"], output) is True


def test_check_conditions_complexity_gte_fails() -> None:
    output = {"analysis": {"estimated_complexity": "low"}}
    assert check_conditions(["analysis.estimated_complexity >= medium"], output) is False


def test_check_conditions_complexity_gt() -> None:
    output = {"analysis": {"estimated_complexity": "epic"}}
    assert check_conditions(["analysis.estimated_complexity > high"], output) is True


def test_check_conditions_complexity_lt() -> None:
    output = {"analysis": {"estimated_complexity": "trivial"}}
    assert check_conditions(["analysis.estimated_complexity < medium"], output) is True


def test_check_conditions_complexity_lte() -> None:
    output = {"analysis": {"estimated_complexity": "medium"}}
    assert check_conditions(["analysis.estimated_complexity <= medium"], output) is True


def test_check_conditions_invalid_complexity_returns_false() -> None:
    output = {"analysis": {"estimated_complexity": "banana"}}
    assert check_conditions(["analysis.estimated_complexity >= medium"], output) is False


def test_check_conditions_malformed_condition_skipped() -> None:
    """Conditions with fewer than 3 parts are skipped (treated as passing)."""
    output = {}
    assert check_conditions(["tooshort"], output) is True


def test_check_conditions_multiple_all_pass() -> None:
    output = {"status": "ok", "count": "5"}
    assert check_conditions(["status == ok", "count == 5"], output) is True


def test_check_conditions_multiple_one_fails() -> None:
    output = {"status": "ok", "count": "3"}
    assert check_conditions(["status == ok", "count == 5"], output) is False


# --- _resolve_field ---

def test_resolve_field_simple() -> None:
    assert _resolve_field({"key": "value"}, "key") == "value"


def test_resolve_field_dotted() -> None:
    assert _resolve_field({"a": {"b": {"c": 42}}}, "a.b.c") == 42


def test_resolve_field_missing_returns_none() -> None:
    assert _resolve_field({"a": 1}, "a.b") is None


def test_resolve_field_non_dict_intermediate_returns_none() -> None:
    assert _resolve_field({"a": "string"}, "a.b") is None


# --- _compare_complexity ---

def test_compare_complexity_gte_equal() -> None:
    assert _compare_complexity("medium", ">=", "medium") is True


def test_compare_complexity_gte_greater() -> None:
    assert _compare_complexity("high", ">=", "low") is True


def test_compare_complexity_gte_less() -> None:
    assert _compare_complexity("low", ">=", "high") is False


def test_compare_complexity_gt_greater() -> None:
    assert _compare_complexity("high", ">", "low") is True


def test_compare_complexity_gt_equal() -> None:
    assert _compare_complexity("high", ">", "high") is False


def test_compare_complexity_lt() -> None:
    assert _compare_complexity("trivial", "<", "medium") is True


def test_compare_complexity_lte() -> None:
    assert _compare_complexity("medium", "<=", "medium") is True


def test_compare_complexity_invalid_actual() -> None:
    assert _compare_complexity("banana", ">=", "medium") is False


def test_compare_complexity_invalid_expected() -> None:
    assert _compare_complexity("medium", ">=", "banana") is False


# --- PipelineExecutor._resolve_clone_dir ---

@pytest.mark.asyncio
async def test_resolve_clone_dir_found(sample_pipelines: Any, tmp_path: Any) -> None:
    """Returns clone_dir when the DB row is found and path exists."""
    repo_dir = tmp_path / "my-repo"
    repo_dir.mkdir()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": str(repo_dir), "branch": "main", "clone_status": "ready"})

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = AsyncMock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)
    clone_dir = await executor._resolve_clone_dir("task-001")

    assert clone_dir == str(repo_dir)


@pytest.mark.asyncio
async def test_resolve_clone_dir_not_found_raises(sample_pipelines: Any) -> None:
    """Raises PipelineError when no row is returned."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = AsyncMock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with pytest.raises(PipelineError, match="No clone_dir found for task task-999"):
        await executor._resolve_clone_dir("task-999")


@pytest.mark.asyncio
async def test_resolve_clone_dir_not_ready_raises(sample_pipelines: Any, tmp_path: Any) -> None:
    """Raises PipelineError when clone_status is not 'ready'."""
    repo_dir = tmp_path / "my-repo"
    repo_dir.mkdir()
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"clone_dir": str(repo_dir), "branch": "main", "clone_status": "cloning"}
    )

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = AsyncMock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with pytest.raises(PipelineError, match="Repository not ready.*clone_status=cloning"):
        await executor._resolve_clone_dir("task-001")


@pytest.mark.asyncio
async def test_resolve_clone_dir_missing_path_raises(sample_pipelines: Any, tmp_path: Any) -> None:
    """Raises PipelineError when clone_dir path does not exist on disk."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"clone_dir": str(tmp_path / "nonexistent"), "branch": "main", "clone_status": "ready"}
    )

    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = AsyncMock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with pytest.raises(PipelineError, match="Clone directory missing"):
        await executor._resolve_clone_dir("task-001")


# --- PipelineExecutor._get_repo_slug ---

@pytest.mark.asyncio
async def test_get_repo_slug_returns_slug(sample_pipelines: Any) -> None:
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"url": "https://github.com/owner/repo.git"}
    )

    executor = PipelineExecutor(mock_db, AsyncMock(), AsyncMock(), sample_pipelines)
    slug = await executor._get_repo_slug("task-001")

    assert slug == "owner/repo"


@pytest.mark.asyncio
async def test_get_repo_slug_not_found_returns_none(sample_pipelines: Any) -> None:
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value=None)

    executor = PipelineExecutor(mock_db, AsyncMock(), AsyncMock(), sample_pipelines)
    slug = await executor._get_repo_slug("task-999")

    assert slug is None


# --- PipelineExecutor._setup_branch ---

@pytest.mark.asyncio
async def test_setup_branch_uses_head_branch_if_provided(sample_pipelines: Any) -> None:
    """When context has a head_branch, checkout it directly."""
    executor = PipelineExecutor(AsyncMock(), AsyncMock(), AsyncMock(), sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor._run_git", new_callable=AsyncMock
    ) as mock_run_git:
        branch = await executor._setup_branch(
            "task-001",
            {"head_branch": "feature/existing"},
            "/repos/myrepo",
        )

    assert branch == "feature/existing"
    mock_run_git.assert_awaited_once_with(
        "/repos/myrepo", "checkout", "-B", "feature/existing", "feature/existing",
    )


@pytest.mark.asyncio
async def test_setup_branch_creates_branch_from_task_title(sample_pipelines: Any) -> None:
    """When no head_branch, a new branch is created from the task title."""
    mock_tq = AsyncMock()
    task = MagicMock()
    task.title = "Add New Feature"
    mock_tq.get_task = AsyncMock(return_value=task)

    mock_db = AsyncMock()
    mock_db.fetch_one = AsyncMock(return_value={"branch": "main"})
    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor._run_git", new_callable=AsyncMock
    ):
        branch = await executor._setup_branch("task-abc", {}, "/repos/myrepo")

    assert branch.startswith("aquarco/task-abc/")
    assert "add-new-feature" in branch


@pytest.mark.asyncio
async def test_setup_branch_raises_if_task_not_found(sample_pipelines: Any) -> None:
    mock_tq = AsyncMock()
    mock_tq.get_task = AsyncMock(return_value=None)

    executor = PipelineExecutor(AsyncMock(), mock_tq, AsyncMock(), sample_pipelines)

    with pytest.raises(PipelineError, match="Task task-999 not found"):
        await executor._setup_branch("task-999", {}, "/repos/myrepo")


# --- PipelineExecutor.execute_pipeline ---

@pytest.mark.asyncio
async def test_execute_pipeline_unknown_pipeline_raises(sample_pipelines: Any) -> None:
    """A pipeline name not in config raises PipelineError."""
    mock_db = AsyncMock(spec=Database)
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_stage_number_for_id = AsyncMock(return_value=None)

    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    with pytest.raises(PipelineError, match="Pipeline 'nonexistent-pipeline' not found"):
        await executor.execute_pipeline("nonexistent-pipeline", "task-001", {})


@pytest.mark.asyncio
async def test_execute_pipeline_no_pipeline_name_uses_task_pipeline(
    sample_pipelines: Any,
) -> None:
    """When pipeline_name is empty, reads pipeline from the task record."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_stage_number_for_id = AsyncMock(return_value=None)
    mock_tq.get_task_context = AsyncMock(return_value={})

    mock_task = MagicMock()
    mock_task.pipeline = "feature-pipeline"
    mock_task.title = "Review something"
    mock_task.initial_context = {}
    mock_task.source_ref = None
    mock_task.last_completed_stage = None
    mock_tq.get_task = AsyncMock(return_value=mock_task)

    mock_registry = AsyncMock()
    mock_registry.select_agent = AsyncMock(return_value="review-agent")
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/p.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)
    # Sync methods
    mock_registry.should_skip_planning = MagicMock(return_value=True)
    mock_registry.get_agents_for_category = MagicMock(return_value=["review-agent"])

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=ClaudeOutput(structured={"result": "ok"}, raw='{"result": "ok"}'),
    ), patch("aquarco_supervisor.pipeline.executor.Path"), patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="0",
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._auto_commit",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=0,
    ):
        await executor.execute_pipeline("", "task-001", {})

    # Used task.pipeline = 'pr-review-pipeline' → runs full pipeline
    mock_tq.get_task.assert_awaited()
    mock_tq.complete_task.assert_awaited_once_with("task-001")


# --- git helper module-level functions ---

@pytest.mark.asyncio
async def test_run_cmd_returns_stdout() -> None:
    result = await _run_cmd("echo", "hello world")
    assert result == "hello world"


@pytest.mark.asyncio
async def test_run_cmd_raises_on_failure() -> None:
    """run_cmd raises RuntimeError on non-zero exit code."""
    with pytest.raises(RuntimeError, match="Command failed"):
        await _run_cmd("false")


@pytest.mark.asyncio
async def test_run_cmd_check_false_no_raise() -> None:
    """run_cmd with check=False returns stdout even on failure."""
    result = await _run_cmd("false", check=False)
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_run_git_delegates_to_run_cmd(tmp_path: Any) -> None:
    """_run_git produces correct git invocation (echo test)."""
    with patch(
        "aquarco_supervisor.utils.run_cmd", new_callable=AsyncMock
    ) as mock_run:
        mock_run.return_value = "abc123"
        result = await _run_git("/some/dir", "rev-parse", "HEAD")

    mock_run.assert_awaited_once_with("git", "-C", "/some/dir", "rev-parse", "HEAD", check=True)
    assert result == "abc123"


@pytest.mark.asyncio
async def test_auto_commit_skips_when_clean() -> None:
    """_auto_commit does nothing when status output is empty."""
    with patch(
        "aquarco_supervisor.pipeline.executor._run_git", new_callable=AsyncMock
    ) as mock_git:
        mock_git.return_value = ""  # clean tree
        await _auto_commit("/repo", "task-001", 0, "review")

    # Should only have called status, not add or commit
    calls = [c.args for c in mock_git.await_args_list]
    assert any("status" in c for c in calls)
    assert not any("add" in c for c in calls)
    assert not any("commit" in c for c in calls)


@pytest.mark.asyncio
async def test_auto_commit_commits_when_dirty() -> None:
    """_auto_commit stages and commits when there are changes."""
    call_results = ["M  file.py", "", ""]  # status, add, commit

    with patch(
        "aquarco_supervisor.pipeline.executor._run_git", new_callable=AsyncMock
    ) as mock_git:
        mock_git.side_effect = call_results
        await _auto_commit("/repo", "task-001", 2, "implement")

    assert mock_git.await_count == 3
    commit_call = mock_git.await_args_list[2]
    assert "commit" in commit_call.args
    assert "task-001" in str(commit_call.args)


@pytest.mark.asyncio
async def test_push_if_ahead_pushes_when_ahead() -> None:
    """_push_if_ahead calls git push when ahead count > 0."""
    with patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=3,
    ), patch(
        "aquarco_supervisor.pipeline.executor._run_git", new_callable=AsyncMock
    ) as mock_git:
        await _push_if_ahead("/repo", "my-branch")

    mock_git.assert_awaited_once()
    assert "push" in mock_git.await_args.args


@pytest.mark.asyncio
async def test_push_if_ahead_skips_when_not_ahead() -> None:
    """_push_if_ahead does not push when ahead count == 0."""
    with patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=0,
    ), patch(
        "aquarco_supervisor.pipeline.executor._run_git", new_callable=AsyncMock
    ) as mock_git:
        await _push_if_ahead("/repo", "my-branch")

    mock_git.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_ahead_count_returns_integer() -> None:
    with patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="5",
    ):
        count = await _get_ahead_count("/repo", "feature-branch")

    assert count == 5


@pytest.mark.asyncio
async def test_get_ahead_count_returns_zero_on_empty() -> None:
    with patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="",
    ):
        count = await _get_ahead_count("/repo", "feature-branch")

    assert count == 0


@pytest.mark.asyncio
async def test_get_ahead_count_returns_zero_on_non_numeric() -> None:
    with patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="not-a-number",
    ):
        count = await _get_ahead_count("/repo", "feature-branch")

    assert count == 0


# --- _execute_stage ---


@pytest.mark.asyncio
async def test_execute_stage_success(sample_pipelines: Any) -> None:
    """_execute_stage selects agent, runs it, and returns output."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = AsyncMock()
    mock_registry.select_agent = AsyncMock(return_value="impl-agent")
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/impl.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=ClaudeOutput(structured={"result": "done"}, raw='{"result": "done"}'),
    ), patch(
        "aquarco_supervisor.pipeline.executor.Path",
    ):
        output = await executor._execute_stage("implement", "task-1", {}, 0)

    assert output["_agent_name"] == "impl-agent"
    assert output["result"] == "done"
    mock_registry.increment_agent_instances.assert_awaited_once_with("impl-agent")
    mock_registry.decrement_agent_instances.assert_awaited_once_with("impl-agent")


@pytest.mark.asyncio
async def test_execute_stage_failure_records_and_raises(sample_pipelines: Any) -> None:
    """_execute_stage records failure and raises StageError on agent error."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_registry = AsyncMock()
    mock_registry.select_agent = AsyncMock(return_value="impl-agent")
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/impl.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        side_effect=RuntimeError("agent crashed"),
    ), pytest.raises(StageError, match="Stage 0.*failed"):
        await executor._execute_stage("implement", "task-1", {}, 0)

    mock_tq.record_stage_failed.assert_awaited_once()
    mock_registry.decrement_agent_instances.assert_awaited_once()


# --- execute_pipeline full flow ---


@pytest.mark.asyncio
async def test_execute_pipeline_full_flow(sample_pipelines: Any) -> None:
    """Full pipeline execution with stages, branch setup, and PR creation."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_stage_number_for_id = AsyncMock(return_value=None)
    mock_tq.get_task_context = AsyncMock(return_value={})

    task = MagicMock()
    task.title = "Add widget"
    task.initial_context = {}
    task.source_ref = None
    task.last_completed_stage = None
    mock_tq.get_task = AsyncMock(return_value=task)

    mock_registry = AsyncMock()
    mock_registry.select_agent = AsyncMock(return_value="impl-agent")
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/p.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)
    # Sync methods must use MagicMock, not AsyncMock
    mock_registry.should_skip_planning = MagicMock(return_value=True)
    mock_registry.get_agents_for_category = MagicMock(return_value=["impl-agent"])

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=ClaudeOutput(structured={"result": "ok"}, raw='{"result": "ok"}'),
    ), patch("aquarco_supervisor.pipeline.executor.Path"), patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="0",
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._auto_commit",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=0,
    ):
        await executor.execute_pipeline("feature-pipeline", "task-1", {})

    mock_tq.complete_task.assert_awaited_once_with("task-1")


@pytest.mark.asyncio
async def test_execute_pipeline_with_checkpoint_resume(sample_pipelines: Any) -> None:
    """Pipeline resumes from checkpoint, skipping completed stages."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_stage_number_for_id = AsyncMock(return_value=0)  # stage_number for the last completed stage
    mock_tq.get_task_context = AsyncMock(return_value={})

    task = MagicMock()
    task.title = "Resume task"
    task.initial_context = {}
    task.source_ref = None
    task.last_completed_stage = 99  # stages.id of the last completed stage
    # Resuming requires planned_stages on the task
    task.planned_stages = [
        {"category": "analyze", "agents": ["agent"], "parallel": False, "validation": []},
        {"category": "implement", "agents": ["agent"], "parallel": False, "validation": []},
    ]
    mock_tq.get_task = AsyncMock(return_value=task)

    mock_registry = AsyncMock()
    mock_registry.select_agent = AsyncMock(return_value="agent")
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/p.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=ClaudeOutput(structured={"result": "ok"}, raw='{"result": "ok"}'),
    ), patch("aquarco_supervisor.pipeline.executor.Path"), patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="0",
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._auto_commit",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=0,
    ):
        await executor.execute_pipeline("feature-pipeline", "task-1", {})

    # Should NOT call create_planned_pending_stages since resuming
    mock_tq.create_planned_pending_stages.assert_not_awaited()
    mock_tq.complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_pipeline_required_stage_fails(sample_pipelines: Any) -> None:
    """When a required stage fails, the task is postponed for retry."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_stage_number_for_id = AsyncMock(return_value=None)
    mock_tq.get_task_context = AsyncMock(return_value={})

    task = MagicMock()
    task.title = "Failing task"
    task.initial_context = {}
    task.last_completed_stage = None
    mock_tq.get_task = AsyncMock(return_value=task)

    mock_registry = AsyncMock()
    mock_registry.select_agent = AsyncMock(return_value="agent")
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/p.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)
    # Sync methods
    mock_registry.should_skip_planning = MagicMock(return_value=True)
    mock_registry.get_agents_for_category = MagicMock(return_value=["agent"])

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        side_effect=RuntimeError("agent died"),
    ), patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="",
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ), patch("aquarco_supervisor.pipeline.executor.Path"):
        await executor.execute_pipeline("feature-pipeline", "task-1", {})

    # Required stage failures are now retried via postpone (not permanent fail)
    mock_tq.postpone_task.assert_awaited_once()
    # No checkpoint when the first stage fails — no prior completed stage to reference
    mock_tq.update_checkpoint.assert_not_awaited()
    mock_tq.complete_task.assert_not_called()


@pytest.mark.asyncio
async def test_execute_pipeline_optional_stage_failure(sample_pipelines: Any) -> None:
    """When an optional stage fails, pipeline continues to next stage."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_stage_number_for_id = AsyncMock(return_value=None)
    mock_tq.get_task_context = AsyncMock(return_value={})

    task = MagicMock()
    task.title = "Optional stage task"
    task.initial_context = {}
    task.last_completed_stage = None
    mock_tq.get_task = AsyncMock(return_value=task)

    mock_registry = AsyncMock()
    mock_registry.select_agent = AsyncMock(return_value="agent")
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/p.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)
    # Sync methods
    mock_registry.should_skip_planning = MagicMock(return_value=True)
    mock_registry.get_agents_for_category = MagicMock(return_value=["agent"])

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    # Patch pipeline config to have an optional first stage
    optional_stages = [
        {"category": "analyze", "required": False},
        {"category": "implement", "required": True},
    ]

    with patch(
        "aquarco_supervisor.pipeline.executor.get_pipeline_config",
        return_value=optional_stages,
    ), patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
    ) as mock_claude, patch(
        "aquarco_supervisor.pipeline.executor.Path",
    ), patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="",
    ), patch(
        "aquarco_supervisor.pipeline.executor._git_checkout",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._auto_commit",
        new_callable=AsyncMock,
    ), patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=0,
    ), patch(
        "aquarco_supervisor.pipeline.executor._run_cmd",
        new_callable=AsyncMock,
    ):
        # First call (analyze) fails, second call (implementation) succeeds
        mock_claude.side_effect = [
            RuntimeError("optional stage exploded"),
            ClaudeOutput(structured={"summary": "ok"}, raw='{"summary": "ok"}'),
        ]
        await executor.execute_pipeline("feature-pipeline", "task-1", {})

    # Task should be completed (not failed)
    mock_tq.complete_task.assert_awaited_once()
    mock_tq.fail_task.assert_not_called()
    # Optional stage should be recorded as skipped
    mock_tq.record_stage_skipped.assert_awaited()


@pytest.mark.asyncio
async def test_execute_single_stage_task_not_found(sample_pipelines: Any) -> None:
    """Single-stage execution raises when task is not found."""
    mock_db = AsyncMock(spec=Database)
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_task = AsyncMock(return_value=None)

    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    with pytest.raises(PipelineError, match="not found"):
        await executor.execute_pipeline("", "missing-task", {})


# --- Stage Runs History: _execute_planned_stage run number logic ---


def _make_registry_mock() -> MagicMock:
    """Return a registry mock wired for _execute_planned_stage tests."""
    mock_registry = MagicMock()
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/impl.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)
    mock_registry.increment_agent_instances = AsyncMock()
    mock_registry.decrement_agent_instances = AsyncMock()
    return mock_registry


@pytest.mark.asyncio
async def test_execute_planned_stage_fresh_run(sample_pipelines: Any) -> None:
    """When get_latest_stage_run returns None, the stage executes with run=1."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_latest_stage_run = AsyncMock(return_value=None)
    mock_registry = _make_registry_mock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=ClaudeOutput(structured={"result": "ok"}, raw='{"result": "ok"}'),
    ), patch("aquarco_supervisor.pipeline.executor.Path"):
        result, stage_id = await executor._execute_planned_stage(
            "task-1", 0, "implement", "impl-agent", {}, iteration=1,
        )

    # No retry row should be created
    mock_tq.create_rerun_stage.assert_not_awaited()
    # record_stage_executing called with run=1
    mock_tq.record_stage_executing.assert_awaited_once()
    call_kwargs = mock_tq.record_stage_executing.call_args.kwargs
    assert call_kwargs["run"] == 1


@pytest.mark.asyncio
async def test_execute_planned_stage_retry_after_failure(sample_pipelines: Any) -> None:
    """When latest run has status=failed, creates a retry row with run=latest+1."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_latest_stage_run = AsyncMock(
        return_value={"id": 10, "status": "failed", "run": 1, "error_message": "boom", "session_id": None}
    )
    mock_tq.create_rerun_stage = AsyncMock(return_value=20)
    mock_registry = _make_registry_mock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=ClaudeOutput(structured={"result": "ok"}, raw='{"result": "ok"}'),
    ), patch("aquarco_supervisor.pipeline.executor.Path"):
        result, stage_id = await executor._execute_planned_stage(
            "task-1", 0, "implement", "impl-agent", {}, iteration=1,
        )

    # Must create a retry row with run=2
    mock_tq.create_rerun_stage.assert_awaited_once()
    retry_kwargs = mock_tq.create_rerun_stage.call_args
    assert retry_kwargs.args[6] == 2 or retry_kwargs.kwargs.get("run") == 2 or retry_kwargs.args[-1] == 2

    # record_stage_executing called with run=2 and stage_id from create_rerun_stage
    call_kwargs = mock_tq.record_stage_executing.call_args.kwargs
    assert call_kwargs["run"] == 2
    assert call_kwargs["stage_id"] == 20
    assert stage_id == 20


@pytest.mark.asyncio
async def test_execute_planned_stage_retry_after_rate_limited(sample_pipelines: Any) -> None:
    """When latest run has status=rate_limited, creates a retry row with run=latest+1."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_latest_stage_run = AsyncMock(
        return_value={"id": 15, "status": "rate_limited", "run": 2, "error_message": "429", "session_id": None}
    )
    mock_tq.create_rerun_stage = AsyncMock(return_value=25)
    mock_registry = _make_registry_mock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=ClaudeOutput(structured={"result": "ok"}, raw='{"result": "ok"}'),
    ), patch("aquarco_supervisor.pipeline.executor.Path"):
        result, stage_id = await executor._execute_planned_stage(
            "task-1", 0, "implement", "impl-agent", {}, iteration=1,
        )

    # Must create a retry row with run=3
    mock_tq.create_rerun_stage.assert_awaited_once()

    # record_stage_executing called with run=3
    call_kwargs = mock_tq.record_stage_executing.call_args.kwargs
    assert call_kwargs["run"] == 3
    assert stage_id == 25


@pytest.mark.asyncio
async def test_execute_planned_stage_reuse_pending_run(sample_pipelines: Any) -> None:
    """When latest run has status=pending, reuses that run number without creating a new row."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main", "clone_status": "ready"})
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_tq.get_latest_stage_run = AsyncMock(
        return_value={"id": 30, "status": "pending", "run": 3, "error_message": None, "session_id": None}
    )
    mock_registry = _make_registry_mock()

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=ClaudeOutput(structured={"result": "ok"}, raw='{"result": "ok"}'),
    ), patch("aquarco_supervisor.pipeline.executor.Path"):
        result, stage_id = await executor._execute_planned_stage(
            "task-1", 0, "implement", "impl-agent", {}, iteration=1,
        )

    # No new retry row should be created
    mock_tq.create_rerun_stage.assert_not_awaited()
    # record_stage_executing called with the existing run=3
    call_kwargs = mock_tq.record_stage_executing.call_args.kwargs
    assert call_kwargs["run"] == 3
    assert call_kwargs["stage_id"] == 30
    assert stage_id == 30


# --- close_task_resources ---


@pytest.mark.asyncio
async def test_close_task_resources_removes_worktree(
    sample_pipelines: Any, tmp_path: Any
) -> None:
    """close_task_resources removes the worktree directory."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"clone_dir": str(tmp_path / "clone"), "branch": "main", "clone_status": "ready"}
    )
    mock_tq = AsyncMock(spec=TaskQueue)
    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor._run_git", new_callable=AsyncMock
    ) as mock_git, patch(
        "aquarco_supervisor.pipeline.executor.Path"
    ) as MockPath:
        # Simulate worktree exists
        mock_work_dir = MagicMock()
        mock_work_dir.exists.return_value = True
        mock_work_dir.__str__ = MagicMock(return_value="/var/lib/aquarco/worktrees/task-1")
        MockPath.return_value.__truediv__ = MagicMock(return_value=mock_work_dir)
        # glob returns no parallel worktrees
        MockPath.return_value.glob.return_value = []

        await executor.close_task_resources("task-1")

    mock_git.assert_awaited_once()
    assert "worktree" in mock_git.await_args.args
    assert "remove" in mock_git.await_args.args


@pytest.mark.asyncio
async def test_close_task_resources_no_worktree(
    sample_pipelines: Any, tmp_path: Any
) -> None:
    """close_task_resources is a no-op when worktree doesn't exist."""
    mock_db = AsyncMock(spec=Database)
    mock_tq = AsyncMock(spec=TaskQueue)
    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor._run_git", new_callable=AsyncMock
    ) as mock_git, patch(
        "aquarco_supervisor.pipeline.executor.Path"
    ) as MockPath:
        mock_work_dir = MagicMock()
        mock_work_dir.exists.return_value = False
        MockPath.return_value.__truediv__ = MagicMock(return_value=mock_work_dir)
        MockPath.return_value.glob.return_value = []

        await executor.close_task_resources("task-1")

    mock_git.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_task_resources_fallback_rmtree(
    sample_pipelines: Any, tmp_path: Any
) -> None:
    """close_task_resources falls back to rmtree when git worktree remove fails."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(
        return_value={"clone_dir": str(tmp_path / "clone"), "branch": "main", "clone_status": "ready"}
    )
    mock_tq = AsyncMock(spec=TaskQueue)
    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    with patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        side_effect=RuntimeError("git worktree remove failed"),
    ), patch(
        "aquarco_supervisor.pipeline.executor.shutil.rmtree"
    ) as mock_rmtree, patch(
        "aquarco_supervisor.pipeline.executor.Path"
    ) as MockPath:
        mock_work_dir = MagicMock()
        mock_work_dir.exists.return_value = True
        mock_work_dir.__str__ = MagicMock(return_value="/var/lib/aquarco/worktrees/task-1")
        MockPath.return_value.__truediv__ = MagicMock(return_value=mock_work_dir)
        MockPath.return_value.glob.return_value = []

        await executor.close_task_resources("task-1")

    mock_rmtree.assert_called_once_with(mock_work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Fix: existing-PR guard in _create_pipeline_pr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_pipeline_pr_skips_create_when_pr_exists(
    sample_pipelines: Any,
) -> None:
    """When gh pr view returns a parseable PR number, store it and skip gh pr create."""
    # Arrange
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"url": "https://github.com/owner/repo.git", "branch": "main"})

    mock_tq = AsyncMock(spec=TaskQueue)
    task = MagicMock()
    task.initial_context = {}   # no head_branch → feature-pipeline path
    task.source_ref = None
    task.title = "My feature"
    mock_tq.get_task = AsyncMock(return_value=task)

    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    existing_pr_json = '{"number":42,"url":"https://github.com/owner/repo/pull/42"}'

    with patch(
        "aquarco_supervisor.pipeline.executor._run_cmd",
        new_callable=AsyncMock,
    ) as mock_run_cmd, patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="",
    ), patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=3,
    ):
        # First _run_cmd call is `gh pr view` → return existing PR JSON
        # Any subsequent call would be `gh pr create` — we want that NOT to happen
        mock_run_cmd.return_value = existing_pr_json

        await executor._create_pipeline_pr(
            "task-1", "aquarco/task-1/my-feature", "/repos/test", {},
        )

    # Assert: store_pr_info called with parsed number
    mock_tq.store_pr_info.assert_awaited_once_with(
        "task-1", 42, "aquarco/task-1/my-feature",
    )
    # Assert: gh pr create was NOT called
    create_calls = [
        c for c in mock_run_cmd.await_args_list
        if "pr" in c.args and "create" in c.args
    ]
    assert not create_calls, "gh pr create must not be called when a PR already exists"


@pytest.mark.asyncio
async def test_create_pipeline_pr_falls_through_when_pr_view_unparseable(
    sample_pipelines: Any,
) -> None:
    """When gh pr view returns non-empty but unparseable output, code falls through to
    gh pr create. This test documents the known fallthrough behaviour (silent bug) so
    that any future fix is caught by a test regression."""
    # Arrange
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"url": "https://github.com/owner/repo.git", "branch": "main"})

    mock_tq = AsyncMock(spec=TaskQueue)
    task = MagicMock()
    task.initial_context = {}
    task.source_ref = None
    task.title = "My feature"
    mock_tq.get_task = AsyncMock(return_value=task)

    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    pr_create_output = "https://github.com/owner/repo/pull/99"

    def _cmd_side_effect(*args: str, **kwargs: Any) -> str:
        if "view" in args:
            # gh pr view returns a warning line with no parseable JSON number
            return "warning: no current branch"
        # gh pr create
        return pr_create_output

    with patch(
        "aquarco_supervisor.pipeline.executor._run_cmd",
        new_callable=AsyncMock,
        side_effect=_cmd_side_effect,
    ), patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="",
    ), patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=3,
    ):
        await executor._create_pipeline_pr(
            "task-1", "aquarco/task-1/my-feature", "/repos/test", {},
        )

    # store_pr_info should be called with the number parsed from the create output (99)
    mock_tq.store_pr_info.assert_awaited_once_with(
        "task-1", 99, "aquarco/task-1/my-feature",
    )


@pytest.mark.asyncio
async def test_create_pipeline_pr_no_existing_pr_creates_new(
    sample_pipelines: Any,
) -> None:
    """When gh pr view returns empty string (no existing PR), gh pr create is called."""
    # Arrange
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"url": "https://github.com/owner/repo.git", "branch": "main"})

    mock_tq = AsyncMock(spec=TaskQueue)
    task = MagicMock()
    task.initial_context = {}
    task.source_ref = None
    task.title = "New PR task"
    mock_tq.get_task = AsyncMock(return_value=task)

    executor = PipelineExecutor(mock_db, mock_tq, AsyncMock(), sample_pipelines)

    pr_create_output = "https://github.com/owner/repo/pull/7"

    def _cmd_side_effect(*args: str, **kwargs: Any) -> str:
        if "view" in args:
            return ""   # no existing PR
        return pr_create_output

    with patch(
        "aquarco_supervisor.pipeline.executor._run_cmd",
        new_callable=AsyncMock,
        side_effect=_cmd_side_effect,
    ), patch(
        "aquarco_supervisor.pipeline.executor._run_git",
        new_callable=AsyncMock,
        return_value="",
    ), patch(
        "aquarco_supervisor.pipeline.executor._get_ahead_count",
        new_callable=AsyncMock,
        return_value=2,
    ):
        await executor._create_pipeline_pr(
            "task-1", "aquarco/task-1/new-pr-task", "/repos/test", {},
        )

    # PR was created and stored
    mock_tq.store_pr_info.assert_awaited_once_with(
        "task-1", 7, "aquarco/task-1/new-pr-task",
    )

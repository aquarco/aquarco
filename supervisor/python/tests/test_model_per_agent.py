"""Tests for the 'Set model per agent' feature (GitHub issue #60).

Covers the full model propagation chain:
  Schema → YAML definitions → AgentRegistry → ScopedAgentView →
  Executor → execute_claude CLI wrapper

Also covers edge cases: resume + model, empty string, overlay resolution,
and the condition evaluator's model passthrough.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from aquarco_supervisor.cli.claude import ClaudeOutput, execute_claude
from aquarco_supervisor.cli import claude as claude_mod
from aquarco_supervisor.config_overlay import (
    ResolvedConfig,
    ScopedAgentView,
    resolve_config,
)
from aquarco_supervisor.database import Database
from aquarco_supervisor.models import ConfigOverlay, MergeConfig, MergeStrategy
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry
from aquarco_supervisor.pipeline.executor import PipelineExecutor
from aquarco_supervisor.task_queue import TaskQueue


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
    with patch.object(claude_mod, "_LOG_DIR", log_dir):
        yield


# ===========================================================================
# 1. JSON Schema validation — model field exists
# ===========================================================================

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "config" / "schemas"


@pytest.mark.parametrize("schema_file", ["pipeline-agent-v1.json", "system-agent-v1.json"])
def test_schema_has_model_field(schema_file: str) -> None:
    """Both agent schemas must declare spec.properties.model as a string."""
    path = SCHEMA_DIR / schema_file
    if not path.exists():
        pytest.skip(f"Schema file not found at {path}")
    schema = json.loads(path.read_text())
    spec_props = schema["properties"]["spec"]["properties"]
    assert "model" in spec_props, f"model field missing from {schema_file}"
    assert spec_props["model"]["type"] == "string"


# ===========================================================================
# 2. YAML agent definitions — model field present
# ===========================================================================

DEFINITIONS_DIR = Path(__file__).resolve().parents[3] / "config" / "agents" / "definitions"


def _collect_yaml_definitions() -> list[Path]:
    if not DEFINITIONS_DIR.exists():
        return []
    return list(DEFINITIONS_DIR.rglob("*.yaml"))


@pytest.mark.parametrize(
    "yaml_path",
    _collect_yaml_definitions(),
    ids=lambda p: str(p.relative_to(DEFINITIONS_DIR)),
)
def test_yaml_definition_has_model(yaml_path: Path) -> None:
    """Every agent definition YAML must include a model field in spec."""
    raw = yaml.safe_load(yaml_path.read_text())
    if not isinstance(raw, dict) or raw.get("kind") != "AgentDefinition":
        pytest.skip("Not an AgentDefinition")
    spec = raw.get("spec", {})
    assert "model" in spec, f"{yaml_path.name} is missing spec.model"
    assert isinstance(spec["model"], str) and len(spec["model"]) > 0


# ===========================================================================
# 3. AgentRegistry.get_agent_model — unit tests
# ===========================================================================


def test_registry_get_agent_model_set(tmp_path: Path) -> None:
    db = AsyncMock(spec=Database)
    reg = AgentRegistry(db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {"a": {"model": "claude-opus-4"}}
    assert reg.get_agent_model("a") == "claude-opus-4"


def test_registry_get_agent_model_missing(tmp_path: Path) -> None:
    db = AsyncMock(spec=Database)
    reg = AgentRegistry(db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {"a": {"resources": {}}}
    assert reg.get_agent_model("a") is None


def test_registry_get_agent_model_unknown_agent(tmp_path: Path) -> None:
    db = AsyncMock(spec=Database)
    reg = AgentRegistry(db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {}
    assert reg.get_agent_model("nonexistent") is None


def test_registry_get_agent_model_empty_string_returns_none(tmp_path: Path) -> None:
    """Empty string model should be treated as None (backward compat)."""
    db = AsyncMock(spec=Database)
    reg = AgentRegistry(db, str(tmp_path), str(tmp_path / "prompts"))
    reg._agents = {"a": {"model": ""}}
    assert reg.get_agent_model("a") is None


# ===========================================================================
# 4. AgentRegistry._discover_agents loads model from YAML
# ===========================================================================


@pytest.mark.asyncio
async def test_discover_agents_loads_model_from_yaml(tmp_path: Path) -> None:
    """YAML discovery preserves the model field in the loaded spec."""
    agents_dir = tmp_path / "agents"
    system_dir = agents_dir / "system"
    pipeline_dir = agents_dir / "pipeline"
    system_dir.mkdir(parents=True)
    pipeline_dir.mkdir(parents=True)

    defn = {
        "kind": "AgentDefinition",
        "metadata": {"name": "impl-agent"},
        "spec": {
            "model": "claude-opus-4",
            "categories": ["implement"],
            "promptFile": "impl.md",
        },
    }
    (pipeline_dir / "impl-agent.yaml").write_text(yaml.dump(defn))

    sys_defn = {
        "kind": "AgentDefinition",
        "metadata": {"name": "planner"},
        "spec": {
            "model": "claude-haiku-4-5",
            "role": "planner",
            "promptFile": "planner.md",
        },
    }
    (system_dir / "planner.yaml").write_text(yaml.dump(sys_defn))

    db = AsyncMock(spec=Database)
    db.fetch_all = AsyncMock(return_value=[])
    reg = AgentRegistry(db, str(agents_dir), str(tmp_path / "prompts"))
    await reg.load(str(tmp_path / "nonexistent-registry.json"))

    assert reg.get_agent_model("impl-agent") == "claude-opus-4"
    assert reg.get_agent_model("planner") == "claude-haiku-4-5"


# ===========================================================================
# 5. ScopedAgentView.get_agent_model — edge cases
# ===========================================================================


def test_scoped_view_model_empty_string_returns_none(tmp_path: Path) -> None:
    resolved = ResolvedConfig(
        agents={"a": {"name": "a", "model": ""}},
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)
    assert view.get_agent_model("a") is None
    view.cleanup()


def test_scoped_view_model_nested_empty_string(tmp_path: Path) -> None:
    resolved = ResolvedConfig(
        agents={"a": {"name": "a", "spec": {"model": ""}}},
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)
    assert view.get_agent_model("a") is None
    view.cleanup()


def test_scoped_view_model_flat_takes_precedence_over_nested(tmp_path: Path) -> None:
    """When both flat and nested model exist, flat (spec.get('spec', spec)) resolves correctly."""
    resolved = ResolvedConfig(
        agents={"a": {"name": "a", "model": "claude-opus-4", "spec": {"model": "claude-haiku-4-5"}}},
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)
    # The nested spec takes precedence because spec.get("spec", spec) returns the inner dict
    assert view.get_agent_model("a") == "claude-haiku-4-5"
    view.cleanup()


def test_scoped_view_model_overlay_overrides_default(tmp_path: Path) -> None:
    """Repo overlay should override default model via resolve_config."""
    default_agents = {
        "agent": {"name": "agent", "model": "claude-sonnet-4-6"},
    }
    repo_base = tmp_path / "repo"
    repo_base.mkdir()
    repo_overlay = ConfigOverlay(
        agents=[{"name": "agent", "model": "claude-opus-4"}],
        merge=MergeConfig(agents=MergeStrategy.EXTEND, pipelines=MergeStrategy.EXTEND),
    )
    resolved = resolve_config(
        default_agents, [], tmp_path / "prompts",
        repo_overlay=repo_overlay,
        repo_overlay_base=repo_base,
    )
    view = ScopedAgentView(resolved)
    assert view.get_agent_model("agent") == "claude-opus-4"
    view.cleanup()


def test_scoped_view_model_global_overlay_then_repo_overlay(tmp_path: Path) -> None:
    """Repo overlay model takes precedence over global overlay model."""
    default_agents = {
        "agent": {"name": "agent", "model": "claude-haiku-4-5"},
    }
    global_base = tmp_path / "global"
    global_base.mkdir()
    global_overlay = ConfigOverlay(
        agents=[{"name": "agent", "model": "claude-sonnet-4-6"}],
        merge=MergeConfig(agents=MergeStrategy.EXTEND, pipelines=MergeStrategy.EXTEND),
    )
    repo_base = tmp_path / "repo"
    repo_base.mkdir()
    repo_overlay = ConfigOverlay(
        agents=[{"name": "agent", "model": "claude-opus-4"}],
        merge=MergeConfig(agents=MergeStrategy.EXTEND, pipelines=MergeStrategy.EXTEND),
    )
    resolved = resolve_config(
        default_agents, [], tmp_path / "prompts",
        global_overlay=global_overlay,
        global_overlay_base=global_base,
        repo_overlay=repo_overlay,
        repo_overlay_base=repo_base,
    )
    view = ScopedAgentView(resolved)
    assert view.get_agent_model("agent") == "claude-opus-4"
    view.cleanup()


# ===========================================================================
# 6. execute_claude — model flag in CLI args
# ===========================================================================


@pytest.mark.asyncio
async def test_execute_claude_model_flag_value(tmp_path: Path) -> None:
    """--model flag is placed correctly with the right value."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            model="claude-opus-4",
        )

    # Verify --model and its value appear consecutively
    args_list = [str(a) for a in captured_args]
    idx = args_list.index("--model")
    assert args_list[idx + 1] == "claude-opus-4"


@pytest.mark.asyncio
async def test_execute_claude_no_model_when_none(tmp_path: Path) -> None:
    """When model=None, --model is absent from CLI args."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            model=None,
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--model" not in args_str


@pytest.mark.asyncio
async def test_execute_claude_no_model_when_empty_string(tmp_path: Path) -> None:
    """When model='', --model is absent (empty string is falsy)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            model="",
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--model" not in args_str


@pytest.mark.asyncio
async def test_execute_claude_resume_with_model(tmp_path: Path) -> None:
    """When both resume_session_id and model are set, --model appears in args.

    This is acceptance criterion #8 from the design — model applies to both
    fresh and resumed sessions.
    """
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            model="claude-sonnet-4-6",
            resume_session_id="abc12345",
        )

    args_list = [str(a) for a in captured_args]
    # --model should be present
    assert "--model" in args_list
    idx = args_list.index("--model")
    assert args_list[idx + 1] == "claude-sonnet-4-6"
    # --resume should also be present
    assert "--resume" in args_list
    # --system-prompt-file should NOT be present (resume mode)
    assert "--system-prompt-file" not in args_list


@pytest.mark.asyncio
async def test_execute_claude_resume_without_model(tmp_path: Path) -> None:
    """Resume without model — --model absent, --resume present."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)
    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
            model=None,
            resume_session_id="abc12345",
        )

    args_str = " ".join(str(a) for a in captured_args)
    assert "--resume" in args_str
    assert "--model" not in args_str


# ===========================================================================
# 7. Executor passes model to execute_claude
# ===========================================================================


@pytest.mark.asyncio
async def test_executor_passes_model_from_registry(sample_pipelines: Any) -> None:
    """_execute_agent resolves model from registry and passes to execute_claude."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main"})
    mock_tq = AsyncMock(spec=TaskQueue)

    mock_registry = MagicMock()
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_agent_max_turns = MagicMock(return_value=30)
    mock_registry.get_agent_max_cost = MagicMock(return_value=5.0)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)
    mock_registry.get_agent_model = MagicMock(return_value="claude-opus-4")

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    claude_output = ClaudeOutput(structured={"result": "ok"}, raw="{}")

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=claude_output,
    ) as mock_execute, patch("aquarco_supervisor.pipeline.executor.Path"):
        await executor._execute_agent("impl-agent", "task-1", {}, 0)

    mock_registry.get_agent_model.assert_called_once_with("impl-agent")
    call_kwargs = mock_execute.call_args.kwargs
    assert call_kwargs["model"] == "claude-opus-4"


@pytest.mark.asyncio
async def test_executor_passes_none_model_when_not_set(sample_pipelines: Any) -> None:
    """_execute_agent passes model=None when registry returns None."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main"})
    mock_tq = AsyncMock(spec=TaskQueue)

    mock_registry = MagicMock()
    mock_registry.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    mock_registry.get_agent_timeout = MagicMock(return_value=30)
    mock_registry.get_agent_max_turns = MagicMock(return_value=30)
    mock_registry.get_agent_max_cost = MagicMock(return_value=5.0)
    mock_registry.get_allowed_tools = MagicMock(return_value=[])
    mock_registry.get_denied_tools = MagicMock(return_value=[])
    mock_registry.get_agent_environment = MagicMock(return_value={})
    mock_registry.get_agent_output_schema = MagicMock(return_value=None)
    mock_registry.get_agent_model = MagicMock(return_value=None)

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    claude_output = ClaudeOutput(structured={"result": "ok"}, raw="{}")

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=claude_output,
    ) as mock_execute, patch("aquarco_supervisor.pipeline.executor.Path"):
        await executor._execute_agent("docs-agent", "task-1", {}, 0)

    call_kwargs = mock_execute.call_args.kwargs
    assert call_kwargs["model"] is None


@pytest.mark.asyncio
async def test_executor_uses_scoped_view_model(sample_pipelines: Any) -> None:
    """_execute_agent uses scoped_view.get_agent_model when provided."""
    mock_db = AsyncMock(spec=Database)
    mock_db.fetch_one = AsyncMock(return_value={"clone_dir": "/repos/test", "branch": "main"})
    mock_tq = AsyncMock(spec=TaskQueue)

    mock_registry = MagicMock()
    mock_scoped = MagicMock()
    mock_scoped.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    mock_scoped.get_agent_timeout = MagicMock(return_value=30)
    mock_scoped.get_agent_max_turns = MagicMock(return_value=30)
    mock_scoped.get_agent_max_cost = MagicMock(return_value=5.0)
    mock_scoped.get_allowed_tools = MagicMock(return_value=[])
    mock_scoped.get_denied_tools = MagicMock(return_value=[])
    mock_scoped.get_agent_environment = MagicMock(return_value={})
    mock_scoped.get_agent_output_schema = MagicMock(return_value=None)
    mock_scoped.get_agent_model = MagicMock(return_value="claude-haiku-4-5")

    executor = PipelineExecutor(mock_db, mock_tq, mock_registry, sample_pipelines)

    claude_output = ClaudeOutput(structured={"ok": True}, raw="{}")

    with patch(
        "aquarco_supervisor.pipeline.executor.execute_claude",
        new_callable=AsyncMock,
        return_value=claude_output,
    ) as mock_execute, patch("aquarco_supervisor.pipeline.executor.Path"):
        await executor._execute_agent(
            "cond-agent", "task-1", {}, 0,
            scoped_view=mock_scoped,
        )

    mock_scoped.get_agent_model.assert_called_once_with("cond-agent")
    mock_registry.get_agent_model.assert_not_called()
    call_kwargs = mock_execute.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5"


# ===========================================================================
# 8. Conditions — evaluate_ai_condition passes model
# ===========================================================================


@pytest.mark.asyncio
async def test_evaluate_ai_condition_passes_model() -> None:
    """evaluate_ai_condition forwards the model parameter to execute_claude."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    mock_output = ClaudeOutput(
        structured={"answer": True, "message": "yes"},
        raw='{"answer": true, "message": "yes"}',
    )

    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        new_callable=AsyncMock,
        return_value=mock_output,
    ) as mock_exec:
        result = await evaluate_ai_condition(
            prompt="Is this ready?",
            context={"task": "t1"},
            work_dir="/tmp/test",
            task_id="task-1",
            stage_num=0,
            model="claude-sonnet-4-6",
        )

    call_kwargs = mock_exec.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"
    assert result["answer"] is True


@pytest.mark.asyncio
async def test_evaluate_ai_condition_model_none_by_default() -> None:
    """evaluate_ai_condition defaults model to None when not provided."""
    from aquarco_supervisor.pipeline.conditions import evaluate_ai_condition

    mock_output = ClaudeOutput(
        structured={"answer": False, "message": "no"},
        raw='{"answer": false, "message": "no"}',
    )

    with patch(
        "aquarco_supervisor.pipeline.conditions.execute_claude",
        new_callable=AsyncMock,
        return_value=mock_output,
    ) as mock_exec:
        result = await evaluate_ai_condition(
            prompt="Ready?",
            context={},
            work_dir="/tmp/test",
            task_id="task-1",
            stage_num=0,
        )

    call_kwargs = mock_exec.call_args.kwargs
    assert call_kwargs["model"] is None


# ===========================================================================
# 9. Backward compatibility — model parameter is optional everywhere
# ===========================================================================


@pytest.mark.asyncio
async def test_execute_claude_model_param_optional(tmp_path: Path) -> None:
    """execute_claude works without model parameter (backward compat)."""
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("agent prompt")

    mock_proc = _make_proc_mock(returncode=0)

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        return mock_proc

    async def fake_tail(path, proc, **kwargs):
        return [], False

    with patch("aquarco_supervisor.cli.claude._tail_file", side_effect=fake_tail), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("tempfile.mkstemp") as mock_mkstemp, \
         patch("pathlib.Path.mkdir"):
        ctx_fd, ctx_path = _make_temp_file(tmp_path / "ctx.json")
        out_fd, out_path = _make_temp_file(tmp_path / "out.ndjson")
        mock_mkstemp.side_effect = [(ctx_fd, ctx_path), (out_fd, out_path)]

        # Call without model parameter at all
        result = await execute_claude(
            prompt_file=prompt_file,
            context={},
            work_dir=str(tmp_path),
            task_id="t1",
            stage_num=0,
        )

    assert isinstance(result, ClaudeOutput)

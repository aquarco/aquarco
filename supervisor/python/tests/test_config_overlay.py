"""Tests for config overlay loading, merging, and ScopedAgentView."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from aifishtank_supervisor.config_overlay import (
    ResolvedConfig,
    ScopedAgentView,
    load_overlay,
    merge_agents,
    merge_pipelines,
    resolve_config,
)
from aifishtank_supervisor.models import MergeStrategy


# --- load_overlay ---


def test_load_overlay_valid(tmp_path: Path) -> None:
    """Parses a valid .aifishtank.yaml."""
    overlay = {
        "apiVersion": "aifishtank.config/v1",
        "kind": "ConfigOverlay",
        "merge": {"agents": "extend", "pipelines": "replace"},
        "agents": [
            {"name": "custom-agent", "spec": {"categories": ["custom"], "priority": 5}},
        ],
        "pipelines": [
            {"name": "custom-pipeline", "trigger": {"labels": ["custom"]}, "stages": []},
        ],
        "promptsDir": "./my-prompts",
    }
    (tmp_path / ".aifishtank.yaml").write_text(yaml.dump(overlay))

    result = load_overlay(tmp_path)
    assert result is not None
    assert result.api_version == "aifishtank.config/v1"
    assert result.merge.agents == MergeStrategy.EXTEND
    assert result.merge.pipelines == MergeStrategy.REPLACE
    assert len(result.agents) == 1
    assert result.agents[0]["name"] == "custom-agent"
    assert len(result.pipelines) == 1
    assert result.prompts_dir == "./my-prompts"


def test_load_overlay_missing_file(tmp_path: Path) -> None:
    """Returns None when .aifishtank.yaml doesn't exist."""
    result = load_overlay(tmp_path)
    assert result is None


def test_load_overlay_invalid_yaml(tmp_path: Path) -> None:
    """Returns None for invalid YAML."""
    (tmp_path / ".aifishtank.yaml").write_text("{{bad yaml::")
    result = load_overlay(tmp_path)
    assert result is None


def test_load_overlay_non_dict(tmp_path: Path) -> None:
    """Returns None when YAML is not a dict."""
    (tmp_path / ".aifishtank.yaml").write_text("- just a list")
    result = load_overlay(tmp_path)
    assert result is None


def test_load_overlay_defaults(tmp_path: Path) -> None:
    """Defaults are applied when minimal YAML is provided."""
    (tmp_path / ".aifishtank.yaml").write_text(yaml.dump({}))
    result = load_overlay(tmp_path)
    assert result is not None
    assert result.merge.agents == MergeStrategy.EXTEND
    assert result.merge.pipelines == MergeStrategy.EXTEND
    assert result.agents == []
    assert result.pipelines == []
    assert result.prompts_dir == "./prompts"


# --- merge_agents ---


def test_merge_agents_extend() -> None:
    """Extend mode: adds custom agents, overrides by name."""
    base = {
        "agent-a": {"name": "agent-a", "categories": ["cat1"]},
        "agent-b": {"name": "agent-b", "categories": ["cat2"]},
    }
    overlay = [
        {"name": "agent-b", "categories": ["cat2-custom"]},
        {"name": "agent-c", "categories": ["cat3"]},
    ]
    result = merge_agents(base, overlay, MergeStrategy.EXTEND)
    assert "agent-a" in result
    assert result["agent-b"]["categories"] == ["cat2-custom"]
    assert "agent-c" in result
    assert len(result) == 3


def test_merge_agents_replace() -> None:
    """Replace mode: swaps all defaults."""
    base = {
        "agent-a": {"name": "agent-a", "categories": ["cat1"]},
    }
    overlay = [
        {"name": "agent-x", "categories": ["catX"]},
    ]
    result = merge_agents(base, overlay, MergeStrategy.REPLACE)
    assert "agent-a" not in result
    assert "agent-x" in result
    assert len(result) == 1


def test_merge_agents_extend_skips_nameless() -> None:
    """Agents without a name are skipped."""
    base = {"agent-a": {"name": "agent-a"}}
    overlay = [{"categories": ["no-name"]}]
    result = merge_agents(base, overlay, MergeStrategy.EXTEND)
    assert len(result) == 1


# --- merge_pipelines ---


def test_merge_pipelines_extend() -> None:
    """Extend mode: adds/overrides pipelines by name."""
    base = [
        {"name": "p1", "stages": [{"category": "a"}]},
        {"name": "p2", "stages": [{"category": "b"}]},
    ]
    overlay = [
        {"name": "p2", "stages": [{"category": "b-custom"}]},
        {"name": "p3", "stages": [{"category": "c"}]},
    ]
    result = merge_pipelines(base, overlay, MergeStrategy.EXTEND)
    names = {p["name"] for p in result}
    assert names == {"p1", "p2", "p3"}
    p2 = next(p for p in result if p["name"] == "p2")
    assert p2["stages"] == [{"category": "b-custom"}]


def test_merge_pipelines_replace() -> None:
    """Replace mode: swaps all pipelines."""
    base = [{"name": "p1", "stages": []}]
    overlay = [{"name": "p-new", "stages": []}]
    result = merge_pipelines(base, overlay, MergeStrategy.REPLACE)
    assert len(result) == 1
    assert result[0]["name"] == "p-new"


# --- resolve_config ---


def test_resolve_config_no_overlays(tmp_path: Path) -> None:
    """Defaults only, no overlays."""
    agents = {"a": {"name": "a", "categories": ["cat1"]}}
    pipelines = [{"name": "p1", "stages": []}]
    prompts_dir = tmp_path / "prompts"

    resolved = resolve_config(agents, pipelines, prompts_dir)
    assert resolved.agents == agents
    assert resolved.pipelines == pipelines
    assert resolved.prompt_dirs == [prompts_dir]


def test_resolve_config_all_layers(tmp_path: Path) -> None:
    """Full 3-layer resolution."""
    from aifishtank_supervisor.models import ConfigOverlay, MergeConfig

    agents = {"a": {"name": "a", "categories": ["cat1"]}}
    pipelines = [{"name": "p1", "stages": []}]
    prompts_dir = tmp_path / "default-prompts"

    global_base = tmp_path / "global"
    global_base.mkdir()
    global_overlay = ConfigOverlay(
        agents=[{"name": "b", "categories": ["cat2"]}],
        pipelines=[{"name": "p2", "stages": []}],
        merge=MergeConfig(agents=MergeStrategy.EXTEND, pipelines=MergeStrategy.EXTEND),
    )

    repo_base = tmp_path / "repo"
    repo_base.mkdir()
    repo_overlay = ConfigOverlay(
        agents=[{"name": "c", "categories": ["cat3"]}],
        pipelines=[{"name": "p1", "stages": [{"category": "overridden"}]}],
        merge=MergeConfig(agents=MergeStrategy.EXTEND, pipelines=MergeStrategy.EXTEND),
    )

    resolved = resolve_config(
        agents, pipelines, prompts_dir,
        global_overlay, global_base,
        repo_overlay, repo_base,
    )
    assert "a" in resolved.agents
    assert "b" in resolved.agents
    assert "c" in resolved.agents
    assert len(resolved.prompt_dirs) == 3
    # p1 should be overridden by repo overlay
    p1 = next(p for p in resolved.pipelines if p["name"] == "p1")
    assert p1["stages"] == [{"category": "overridden"}]


# --- ScopedAgentView ---


def test_scoped_view_prompt_file_resolution(tmp_path: Path) -> None:
    """Searches prompt_dirs in reverse order (later overrides earlier)."""
    default_dir = tmp_path / "default"
    default_dir.mkdir()
    (default_dir / "agent.md").write_text("default prompt")

    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    (overlay_dir / "agent.md").write_text("overlay prompt")

    resolved = ResolvedConfig(
        agents={"agent": {"name": "agent"}},
        pipelines=[],
        prompt_dirs=[default_dir, overlay_dir],
    )
    view = ScopedAgentView(resolved)
    prompt = view.get_agent_prompt_file("agent")
    assert prompt == overlay_dir / "agent.md"
    view.cleanup()


def test_scoped_view_prompt_falls_back_to_default(tmp_path: Path) -> None:
    """Falls back to default dir when overlay doesn't have the file."""
    default_dir = tmp_path / "default"
    default_dir.mkdir()
    (default_dir / "agent.md").write_text("default prompt")

    overlay_dir = tmp_path / "overlay"
    overlay_dir.mkdir()
    # No agent.md in overlay

    resolved = ResolvedConfig(
        agents={"agent": {"name": "agent"}},
        pipelines=[],
        prompt_dirs=[default_dir, overlay_dir],
    )
    view = ScopedAgentView(resolved)
    prompt = view.get_agent_prompt_file("agent")
    assert prompt == default_dir / "agent.md"
    view.cleanup()


def test_scoped_view_inline_prompt(tmp_path: Path) -> None:
    """Writes tempfile for promptInline."""
    resolved = ResolvedConfig(
        agents={"agent": {"name": "agent", "promptInline": "You are a test agent."}},
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)
    prompt = view.get_agent_prompt_file("agent")
    assert prompt.exists()
    assert prompt.read_text() == "You are a test agent."
    view.cleanup()
    assert not prompt.exists()


def test_scoped_view_inline_prompt_nested_spec(tmp_path: Path) -> None:
    """promptInline nested under spec is also found."""
    resolved = ResolvedConfig(
        agents={"agent": {"name": "agent", "spec": {"promptInline": "Nested inline."}}},
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)
    prompt = view.get_agent_prompt_file("agent")
    assert prompt.read_text() == "Nested inline."
    view.cleanup()


def test_scoped_view_accessors(tmp_path: Path) -> None:
    """Test timeout, tools, env, schema accessors."""
    resolved = ResolvedConfig(
        agents={
            "agent": {
                "name": "agent",
                "spec": {
                    "resources": {"timeoutMinutes": 60},
                    "tools": {"allowed": ["Read", "Write"], "denied": ["Bash"]},
                    "environment": {"FOO": "bar"},
                    "outputSchema": {"type": "object"},
                },
            }
        },
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)
    assert view.get_agent_timeout("agent") == 60
    assert view.get_allowed_tools("agent") == ["Read", "Write"]
    assert view.get_denied_tools("agent") == ["Bash"]
    assert view.get_agent_environment("agent") == {"FOO": "bar"}
    assert view.get_agent_output_schema("agent") == {"type": "object"}
    view.cleanup()


def test_scoped_view_accessors_defaults(tmp_path: Path) -> None:
    """Unknown agents get defaults."""
    resolved = ResolvedConfig(agents={}, pipelines=[], prompt_dirs=[tmp_path])
    view = ScopedAgentView(resolved)
    assert view.get_agent_timeout("unknown") == 30
    assert view.get_allowed_tools("unknown") == []
    assert view.get_denied_tools("unknown") == []
    assert view.get_agent_environment("unknown") == {}
    assert view.get_agent_output_schema("unknown") is None
    view.cleanup()


def test_scoped_view_path_traversal(tmp_path: Path) -> None:
    """Path traversal in promptFile is rejected."""
    from aifishtank_supervisor.exceptions import AgentRegistryError

    resolved = ResolvedConfig(
        agents={"agent": {"name": "agent", "promptFile": "../../etc/passwd"}},
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)
    with pytest.raises(AgentRegistryError, match="escapes"):
        view.get_agent_prompt_file("agent")
    view.cleanup()

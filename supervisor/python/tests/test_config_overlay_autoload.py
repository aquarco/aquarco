"""Tests for config overlay autoloaded agents (4th layer).

Tests that autoloaded agents are correctly merged as a 4th config layer
in resolve_config() and accessible via ScopedAgentView.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aquarco_supervisor.config_overlay import (
    ResolvedConfig,
    ScopedAgentView,
    resolve_config,
)
from aquarco_supervisor.models import (
    ConfigOverlay,
    MergeConfig,
    MergeStrategy,
)


# ---------------------------------------------------------------------------
# resolve_config with autoloaded_agents
# ---------------------------------------------------------------------------


def test_resolve_config_autoloaded_agents_added(tmp_path: Path) -> None:
    """AC: Autoloaded agents appear in resolved config."""
    agents: dict[str, dict[str, Any]] = {
        "default-agent": {"name": "default-agent", "categories": ["test"]},
    }
    pipelines: list[dict[str, Any]] = []
    prompts_dir = tmp_path / "prompts"

    autoloaded = [
        {"name": "repo-custom", "categories": ["review"], "spec": {"tools": {"allowed": ["Read"]}}},
    ]

    resolved = resolve_config(agents, pipelines, prompts_dir, autoloaded_agents=autoloaded)

    assert "default-agent" in resolved.agents
    assert "repo-custom" in resolved.agents
    assert len(resolved.agents) == 2


def test_resolve_config_autoloaded_extends_not_replaces(tmp_path: Path) -> None:
    """Autoloaded agents use EXTEND strategy, preserving existing agents."""
    agents: dict[str, dict[str, Any]] = {
        "a": {"name": "a"},
        "b": {"name": "b"},
    }

    autoloaded = [{"name": "c"}]

    resolved = resolve_config(agents, [], tmp_path, autoloaded_agents=autoloaded)

    assert "a" in resolved.agents
    assert "b" in resolved.agents
    assert "c" in resolved.agents


def test_resolve_config_autoloaded_overrides_by_name(tmp_path: Path) -> None:
    """Autoloaded agents override existing agents with the same name."""
    agents: dict[str, dict[str, Any]] = {
        "shared": {"name": "shared", "categories": ["original"]},
    }

    autoloaded = [{"name": "shared", "categories": ["autoloaded"]}]

    resolved = resolve_config(agents, [], tmp_path, autoloaded_agents=autoloaded)

    assert resolved.agents["shared"]["categories"] == ["autoloaded"]


def test_resolve_config_autoloaded_after_repo_overlay(tmp_path: Path) -> None:
    """Autoloaded agents are merged AFTER repo overlay (4th layer)."""
    agents: dict[str, dict[str, Any]] = {
        "default": {"name": "default", "priority": 1},
    }

    repo_base = tmp_path / "repo"
    repo_base.mkdir()
    repo_overlay = ConfigOverlay(
        agents=[{"name": "repo-agent", "priority": 2}],
        merge=MergeConfig(agents=MergeStrategy.EXTEND, pipelines=MergeStrategy.EXTEND),
    )

    autoloaded = [{"name": "autoloaded-agent", "priority": 3}]

    resolved = resolve_config(
        agents, [], tmp_path,
        repo_overlay=repo_overlay,
        repo_overlay_base=repo_base,
        autoloaded_agents=autoloaded,
    )

    assert "default" in resolved.agents
    assert "repo-agent" in resolved.agents
    assert "autoloaded-agent" in resolved.agents


def test_resolve_config_autoloaded_none_is_noop(tmp_path: Path) -> None:
    """When autoloaded_agents is None, no extra merging occurs."""
    agents: dict[str, dict[str, Any]] = {"a": {"name": "a"}}

    resolved = resolve_config(agents, [], tmp_path, autoloaded_agents=None)

    assert resolved.agents == agents


def test_resolve_config_autoloaded_empty_list_is_noop(tmp_path: Path) -> None:
    """When autoloaded_agents is empty list, no extra merging occurs."""
    agents: dict[str, dict[str, Any]] = {"a": {"name": "a"}}

    resolved = resolve_config(agents, [], tmp_path, autoloaded_agents=[])

    assert resolved.agents == agents


def test_resolve_config_autoloaded_does_not_affect_pipelines(tmp_path: Path) -> None:
    """Autoloaded agents don't affect pipeline resolution."""
    pipelines = [{"name": "p1", "stages": [{"category": "test"}]}]
    autoloaded = [{"name": "agent"}]

    resolved = resolve_config({}, pipelines, tmp_path, autoloaded_agents=autoloaded)

    assert resolved.pipelines == pipelines


# ---------------------------------------------------------------------------
# ScopedAgentView with autoloaded agents
# ---------------------------------------------------------------------------


def test_scoped_view_autoloaded_inline_prompt(tmp_path: Path) -> None:
    """AC: Autoloaded agents with promptInline are accessible via ScopedAgentView."""
    resolved = ResolvedConfig(
        agents={
            "autoloaded": {
                "name": "autoloaded",
                "spec": {
                    "promptInline": "You are an autoloaded agent.",
                    "tools": {"allowed": ["Read", "Grep", "Glob"]},
                    "resources": {"timeoutMinutes": 30, "maxTurns": 30},
                },
            }
        },
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)

    prompt = view.get_agent_prompt_file("autoloaded")
    assert prompt.exists()
    assert prompt.read_text() == "You are an autoloaded agent."
    assert view.get_allowed_tools("autoloaded") == ["Read", "Grep", "Glob"]
    assert view.get_agent_timeout("autoloaded") == 30
    assert view.get_agent_max_turns("autoloaded") == 30

    view.cleanup()


def test_scoped_view_autoloaded_default_tools(tmp_path: Path) -> None:
    """AC: Conservative defaults when Claude analysis didn't suggest tools."""
    resolved = ResolvedConfig(
        agents={
            "auto": {
                "name": "auto",
                "spec": {
                    "tools": {"allowed": ["Read", "Grep", "Glob"], "denied": []},
                    "resources": {"maxCost": 5.0},
                },
            }
        },
        pipelines=[],
        prompt_dirs=[tmp_path],
    )
    view = ScopedAgentView(resolved)

    tools = view.get_allowed_tools("auto")
    assert tools == ["Read", "Grep", "Glob"]
    assert view.get_agent_max_cost("auto") == 5.0
    assert view.get_denied_tools("auto") == []

    view.cleanup()


def test_scoped_view_autoloaded_participates_in_category_lookup(tmp_path: Path) -> None:
    """AC: Autoloaded agents participate in pipeline execution when categories match."""
    agents: dict[str, dict[str, Any]] = {
        "default-test": {"name": "default-test", "categories": ["test"]},
    }
    autoloaded = [
        {"name": "repo-test", "categories": ["test"], "spec": {"priority": 50}},
    ]

    resolved = resolve_config(agents, [], tmp_path, autoloaded_agents=autoloaded)

    # Both agents with 'test' category are present
    test_agents = [
        name for name, spec in resolved.agents.items()
        if "test" in (spec.get("categories") or [])
    ]
    assert "default-test" in test_agents
    assert "repo-test" in test_agents

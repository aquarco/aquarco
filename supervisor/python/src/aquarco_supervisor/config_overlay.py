"""3-layer config overlay: default -> global -> per-repo."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from .exceptions import AgentRegistryError
from .logging import get_logger
from .models import ConfigOverlay, MergeStrategy

log = get_logger("config-overlay")


def load_overlay(config_path: Path) -> ConfigOverlay | None:
    """Parse .aquarco.yaml at the given path. Returns None if missing."""
    yaml_file = config_path / ".aquarco.yaml"
    if not yaml_file.exists():
        return None
    try:
        raw = yaml.safe_load(yaml_file.read_text())
        if not isinstance(raw, dict):
            log.warning("overlay_invalid_format", path=str(yaml_file))
            return None
        return ConfigOverlay.model_validate(raw)
    except Exception as e:
        log.warning("overlay_parse_error", path=str(yaml_file), error=str(e))
        return None


def merge_agents(
    base: dict[str, dict[str, Any]],
    overlay_agents: list[dict[str, Any]],
    strategy: MergeStrategy,
    config_base: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Merge overlay agents into base agents dict.

    When *config_base* is provided, each overlay agent is tagged with
    ``_config_base`` so that ``promptFile`` can be resolved relative to it.
    """
    if strategy == MergeStrategy.REPLACE:
        merged = {a["name"]: a for a in overlay_agents}
    else:
        # EXTEND: add/override by name
        merged = dict(base)
        for agent in overlay_agents:
            name = agent.get("name")
            if not name:
                continue
            merged[name] = agent

    if config_base is not None:
        base_str = str(config_base)
        for agent in overlay_agents:
            name = agent.get("name")
            if name and name in merged:
                merged[name]["_config_base"] = base_str

    return merged


def merge_pipelines(
    base: list[dict[str, Any]],
    overlay_pipelines: list[dict[str, Any]],
    strategy: MergeStrategy,
) -> list[dict[str, Any]]:
    """Merge overlay pipelines into base pipelines list."""
    if strategy == MergeStrategy.REPLACE:
        return list(overlay_pipelines)
    # EXTEND: add/override by name
    result_map = {p["name"]: p for p in base}
    for pipeline in overlay_pipelines:
        name = pipeline.get("name")
        if not name:
            continue
        result_map[name] = pipeline
    return list(result_map.values())


class ResolvedConfig:
    """Holds merged agents and pipelines."""

    def __init__(
        self,
        agents: dict[str, dict[str, Any]],
        pipelines: list[dict[str, Any]],
    ) -> None:
        self.agents = agents
        self.pipelines = pipelines


def resolve_config(
    default_agents: dict[str, dict[str, Any]],
    default_pipelines: list[dict[str, Any]],
    global_overlay: ConfigOverlay | None = None,
    global_overlay_base: Path | None = None,
    repo_overlay: ConfigOverlay | None = None,
    repo_overlay_base: Path | None = None,
    autoloaded_agents: list[dict[str, Any]] | None = None,
) -> ResolvedConfig:
    """Apply config layers: default -> global -> per-repo -> autoloaded.

    The optional *autoloaded_agents* list is merged as a 4th layer after
    repo_overlay using EXTEND strategy (add/override by name).

    Overlay agents are tagged with ``_config_base`` so that ``promptFile``
    can be resolved relative to their source directory.
    """
    agents = dict(default_agents)
    pipelines = list(default_pipelines)

    if global_overlay and global_overlay_base:
        agents = merge_agents(
            agents, global_overlay.agents, global_overlay.merge.agents,
            config_base=global_overlay_base,
        )
        pipelines = merge_pipelines(
            pipelines, global_overlay.pipelines, global_overlay.merge.pipelines,
        )

    if repo_overlay and repo_overlay_base:
        agents = merge_agents(
            agents, repo_overlay.agents, repo_overlay.merge.agents,
            config_base=repo_overlay_base,
        )
        pipelines = merge_pipelines(
            pipelines, repo_overlay.pipelines, repo_overlay.merge.pipelines,
        )

    # Layer 4: autoloaded agents (always EXTEND strategy)
    if autoloaded_agents:
        agents = merge_agents(agents, autoloaded_agents, MergeStrategy.EXTEND)

    return ResolvedConfig(agents, pipelines)


class ScopedAgentView:
    """Provides agent config accessors over a ResolvedConfig.

    Same interface as AgentRegistry's config methods but reads from
    resolved (merged) config. Does NOT handle capacity management.
    """

    def __init__(self, resolved: ResolvedConfig) -> None:
        self._resolved = resolved
        self._temp_files: list[Path] = []

    def get_agent_prompt_file(self, agent_name: str) -> Path:
        """Get the prompt file path for an agent.

        Resolution order:
        1. ``promptInline`` → written to a tempfile.
        2. ``promptFile`` resolved relative to the agent's ``_definition_file``
           parent directory (for YAML-discovered agents) or ``_config_base``
           (for overlay agents).

        If the agent spec has promptInline, writes it to a tempfile.
        """
        spec = self._resolved.agents.get(agent_name, {})

        # Inline prompt
        inline = spec.get("promptInline") or spec.get("spec", {}).get("promptInline")
        if inline:
            tf = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", prefix=f"prompt-{agent_name}-",
                delete=False,
            )
            tf.write(inline)
            tf.close()
            path = Path(tf.name)
            self._temp_files.append(path)
            return path

        # File-based prompt: resolve relative to definition file or config base
        prompt_file_name = (
            spec.get("promptFile")
            or spec.get("spec", {}).get("promptFile")
            or f"{agent_name}.md"
        )

        # Determine base directory for resolution
        definition_file = spec.get("_definition_file")
        config_base = spec.get("_config_base")
        if definition_file:
            base_dir = Path(definition_file).parent
        elif config_base:
            base_dir = Path(config_base)
        else:
            raise AgentRegistryError(
                f"Cannot resolve prompt file for '{agent_name}': "
                f"no _definition_file or _config_base"
            )

        return (base_dir / prompt_file_name).resolve()

    def get_agent_model(self, agent_name: str) -> str | None:
        """Get the model for an agent from resolved config, or None if not set."""
        spec = self._resolved.agents.get(agent_name, {})
        s = spec.get("spec", spec)
        model: str | None = s.get("model")
        return model or None

    def get_agent_timeout(self, agent_name: str) -> int:
        spec = self._resolved.agents.get(agent_name, {})
        s = spec.get("spec", spec)
        resources: dict[str, Any] = s.get("resources", {})
        return resources.get("timeoutMinutes", 30)

    def get_agent_max_turns(self, agent_name: str) -> int:
        spec = self._resolved.agents.get(agent_name, {})
        s = spec.get("spec", spec)
        resources: dict[str, Any] = s.get("resources", {})
        return resources.get("maxTurns", 30)

    def get_agent_max_cost(self, agent_name: str) -> float:
        spec = self._resolved.agents.get(agent_name, {})
        s = spec.get("spec", spec)
        resources: dict[str, Any] = s.get("resources", {})
        return resources.get("maxCost", 5.0)

    def get_allowed_tools(self, agent_name: str) -> list[str]:
        spec = self._resolved.agents.get(agent_name, {})
        s = spec.get("spec", spec)
        tools: dict[str, Any] = s.get("tools", {})
        return tools.get("allowed", [])

    def get_denied_tools(self, agent_name: str) -> list[str]:
        spec = self._resolved.agents.get(agent_name, {})
        s = spec.get("spec", spec)
        tools: dict[str, Any] = s.get("tools", {})
        return tools.get("denied", [])

    def get_agent_environment(self, agent_name: str) -> dict[str, str]:
        spec = self._resolved.agents.get(agent_name, {})
        s = spec.get("spec", spec)
        return s.get("environment", {})

    def get_agent_output_schema(self, agent_name: str) -> dict[str, Any] | None:
        spec = self._resolved.agents.get(agent_name, {})
        s = spec.get("spec", spec)
        schema = s.get("outputSchema")
        return schema if schema else None

    def get_pipeline_categories(self, pipeline_name: str) -> dict[str, dict[str, Any]]:
        """Get category -> outputSchema map for a named pipeline from resolved config."""
        for p in self._resolved.pipelines:
            if p.get("name") == pipeline_name:
                return p.get("categories", {})
        return {}

    def cleanup(self) -> None:
        """Remove any tempfiles created for inline prompts."""
        for path in self._temp_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._temp_files.clear()

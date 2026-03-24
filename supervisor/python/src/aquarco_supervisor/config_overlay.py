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
) -> dict[str, dict[str, Any]]:
    """Merge overlay agents into base agents dict."""
    if strategy == MergeStrategy.REPLACE:
        return {a["name"]: a for a in overlay_agents}
    # EXTEND: add/override by name
    result = dict(base)
    for agent in overlay_agents:
        name = agent.get("name")
        if not name:
            continue
        result[name] = agent
    return result


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
    """Holds merged agents, pipelines, and ordered prompt directories."""

    def __init__(
        self,
        agents: dict[str, dict[str, Any]],
        pipelines: list[dict[str, Any]],
        prompt_dirs: list[Path],
    ) -> None:
        self.agents = agents
        self.pipelines = pipelines
        self.prompt_dirs = prompt_dirs


def resolve_config(
    default_agents: dict[str, dict[str, Any]],
    default_pipelines: list[dict[str, Any]],
    default_prompts_dir: Path,
    global_overlay: ConfigOverlay | None = None,
    global_overlay_base: Path | None = None,
    repo_overlay: ConfigOverlay | None = None,
    repo_overlay_base: Path | None = None,
) -> ResolvedConfig:
    """Apply config layers: default -> global -> per-repo."""
    agents = dict(default_agents)
    pipelines = list(default_pipelines)
    prompt_dirs = [default_prompts_dir]

    if global_overlay and global_overlay_base:
        agents = merge_agents(agents, global_overlay.agents, global_overlay.merge.agents)
        pipelines = merge_pipelines(
            pipelines, global_overlay.pipelines, global_overlay.merge.pipelines,
        )
        overlay_prompts = (global_overlay_base / global_overlay.prompts_dir).resolve()
        prompt_dirs.append(overlay_prompts)

    if repo_overlay and repo_overlay_base:
        agents = merge_agents(agents, repo_overlay.agents, repo_overlay.merge.agents)
        pipelines = merge_pipelines(
            pipelines, repo_overlay.pipelines, repo_overlay.merge.pipelines,
        )
        overlay_prompts = (repo_overlay_base / repo_overlay.prompts_dir).resolve()
        prompt_dirs.append(overlay_prompts)

    return ResolvedConfig(agents, pipelines, prompt_dirs)


class ScopedAgentView:
    """Provides agent config accessors over a ResolvedConfig.

    Same interface as AgentRegistry's config methods but reads from
    resolved (merged) config. Does NOT handle capacity management.
    """

    def __init__(self, resolved: ResolvedConfig) -> None:
        self._resolved = resolved
        self._temp_files: list[Path] = []

    def get_agent_prompt_file(self, agent_name: str) -> Path:
        """Get the prompt file path, searching prompt_dirs in reverse order.

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

        # File-based prompt: search prompt_dirs in reverse (later layers override)
        prompt_file_name = (
            spec.get("promptFile")
            or spec.get("spec", {}).get("promptFile")
            or f"{agent_name}.md"
        )

        for prompt_dir in reversed(self._resolved.prompt_dirs):
            resolved = (prompt_dir / prompt_file_name).resolve()
            if not resolved.is_relative_to(prompt_dir.resolve()):
                raise AgentRegistryError(
                    f"Prompt file path escapes prompts directory: {prompt_file_name}"
                )
            if resolved.exists():
                return resolved

        # Fall back to first (default) prompts dir even if file doesn't exist
        return (self._resolved.prompt_dirs[0] / prompt_file_name).resolve()

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

    def cleanup(self) -> None:
        """Remove any tempfiles created for inline prompts."""
        for path in self._temp_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._temp_files.clear()

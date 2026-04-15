"""Agent discovery, capacity management, and instance tracking."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ..database import Database
from ..exceptions import AgentRegistryError, NoAvailableAgentError
from ..logging import get_logger

log = get_logger("agent-registry")


def _parse_md_agent_file(path: Path) -> tuple[dict, str]:
    """Split a hybrid .md agent file into (frontmatter_dict, prompt_body).

    The file format is::

        ---
        <YAML frontmatter>
        ---
        <Markdown prompt body>

    Raises :class:`ValueError` on missing delimiters or non-dict YAML.
    """
    content = path.read_text()

    if not content.startswith("---"):
        raise ValueError(f"Missing opening '---' delimiter in {path}")

    # Find the closing delimiter (second '---' line)
    after_open = content[3:]
    # Skip past the newline after opening ---
    if after_open.startswith("\n"):
        after_open = after_open[1:]

    close_idx = after_open.find("\n---")
    if close_idx == -1:
        raise ValueError(f"Missing closing '---' delimiter in {path}")

    frontmatter_text = after_open[:close_idx]
    # Prompt body starts after the closing --- and its newline
    body_start = close_idx + 4  # len("\n---")
    prompt_body = after_open[body_start:]
    if prompt_body.startswith("\n"):
        prompt_body = prompt_body[1:]

    parsed = yaml.safe_load(frontmatter_text)
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Frontmatter in {path} did not parse as a dict (got {type(parsed).__name__})"
        )

    return parsed, prompt_body


class AgentRegistry:
    """Manages agent definitions, capacity, and instance tracking."""

    def __init__(self, db: Database, agents_dir: str) -> None:
        self._db = db
        self._agents_dir = Path(agents_dir)
        self._agents: dict[str, dict[str, Any]] = {}

    async def load(self, registry_file: str | None = None) -> None:
        """Load agent registry from JSON file or discover from YAML definitions."""
        if registry_file:
            path = Path(registry_file)
        else:
            path = self._agents_dir.parent / "schemas" / "agent-registry.json"

        if path.exists():
            try:
                data = json.loads(path.read_text())
                agents_list = data.get("agents", data) if isinstance(data, dict) else data
                if isinstance(agents_list, list):
                    self._agents = {a["name"]: a for a in agents_list}
                else:
                    self._agents = agents_list
            except (json.JSONDecodeError, KeyError) as e:
                raise AgentRegistryError(f"Failed to parse registry: {e}") from e
        else:
            await self._discover_agents()

        await self._sync_agent_instances()
        log.info("registry_loaded", agent_count=len(self._agents))

    async def _discover_agents(self) -> None:
        """Discover agents from hybrid .md definition files.

        When ``agents_dir/system/`` and ``agents_dir/pipeline/`` subdirectories
        exist, scans each with the appropriate group tag.  Falls back to a flat
        scan of ``agents_dir`` for backward compatibility; agents whose name
        appears in the known system-agent list are tagged 'system'.
        """
        if not self._agents_dir.exists():
            log.warning("agents_dir_not_found", path=str(self._agents_dir))
            return

        system_dir = self._agents_dir / "system"
        pipeline_dir = self._agents_dir / "pipeline"

        if system_dir.is_dir() and pipeline_dir.is_dir():
            self._discover_agents_from_dir(system_dir, group="system")
            self._discover_agents_from_dir(pipeline_dir, group="pipeline")
        else:
            # Flat scan — infer group from name
            from ..constants import SYSTEM_AGENT_NAMES as _SYSTEM_AGENT_NAMES  # noqa: PLC0415

            for md_file in sorted(self._agents_dir.glob("*.md")):
                try:
                    frontmatter, _prompt = _parse_md_agent_file(md_file)
                    name = frontmatter.get("name", md_file.stem)
                    spec = dict(frontmatter)  # shallow copy
                    spec["name"] = name
                    group = "system" if name in _SYSTEM_AGENT_NAMES else "pipeline"
                    spec["_group"] = group
                    spec["_definition_file"] = str(md_file)
                    self._agents[name] = spec
                except (ValueError, yaml.YAMLError) as exc:
                    log.warning("agent_md_parse_error", file=str(md_file), error=str(exc))

    def _discover_agents_from_dir(self, directory: Path, group: str) -> None:
        """Scan a single directory and add agents tagged with the given group."""
        for md_file in sorted(directory.glob("*.md")):
            try:
                frontmatter, _prompt = _parse_md_agent_file(md_file)
                name = frontmatter.get("name", md_file.stem)
                spec = dict(frontmatter)  # shallow copy
                spec["name"] = name
                spec["_group"] = group
                spec["_definition_file"] = str(md_file)
                self._agents[name] = spec
            except (ValueError, yaml.YAMLError) as exc:
                log.warning("agent_md_parse_error", file=str(md_file), error=str(exc))

    async def _sync_agent_instances(self) -> None:
        """Ensure all agents have rows in agent_instances table.

        On startup, reset active_count to 0 for all known agents since any
        previously active agents are dead after a supervisor restart.
        Preserves total_executions and last_execution_at.
        """
        for name in self._agents:
            await self._db.execute(
                """
                INSERT INTO agent_instances (agent_name, active_count, total_executions)
                VALUES (%(name)s, 0, 0)
                ON CONFLICT (agent_name) DO UPDATE
                SET active_count = 0
                """,
                {"name": name},
            )

    def get_agents_for_category(self, category: str) -> list[str]:
        """Get pipeline agent names that handle a category, sorted by priority.

        System agents (group='system') are always excluded — they are invoked
        directly by the executor and never compete for pipeline stage slots.
        """
        matching = []
        for name, spec in self._agents.items():
            if spec.get("_group") == "system":
                continue
            categories = spec.get("categories", [])
            if category in categories:
                priority = spec.get("priority", 50)
                matching.append((priority, name))
        matching.sort()
        return [name for _, name in matching]

    def get_agent_group(self, agent_name: str) -> str:
        """Return the group ('system' or 'pipeline') for an agent.

        Returns 'pipeline' for unknown agents (safe default).
        """
        spec = self._agents.get(agent_name, {})
        return spec.get("_group", "pipeline")

    def get_system_agent_by_role(self, role: str) -> str | None:
        """Return the name of the system agent with the given role, or None."""
        for name, spec in self._agents.items():
            if spec.get("_group") == "system" and spec.get("role") == role:
                return name
        return None

    async def select_agent(self, category: str) -> str:
        """Select the first available agent for a category."""
        if not self._agents:
            raise NoAvailableAgentError(
                "Agent registry is empty — no agents loaded"
            )
        candidates = self.get_agents_for_category(category)
        if not candidates:
            raise NoAvailableAgentError(
                f"No agents registered for category '{category}'"
            )
        for agent_name in candidates:
            if await self.agent_is_available(agent_name):
                return agent_name
        raise NoAvailableAgentError(
            f"All agents for category '{category}' are at capacity"
        )

    async def agent_is_available(self, agent_name: str) -> bool:
        """Check if an agent has capacity for more work."""
        max_concurrent = self._get_max_concurrent(agent_name)
        active = await self._db.fetch_val(
            """
            SELECT COALESCE(active_count, 0) FROM agent_instances
            WHERE agent_name = %(name)s
            """,
            {"name": agent_name},
        )
        return (active or 0) < max_concurrent

    async def increment_agent_instances(self, agent_name: str) -> None:
        """Increment active count and total executions for an agent."""
        await self._db.execute(
            """
            INSERT INTO agent_instances
                (agent_name, active_count, total_executions, last_execution_at)
            VALUES (%(name)s, 1, 1, NOW())
            ON CONFLICT (agent_name) DO UPDATE
            SET active_count = agent_instances.active_count + 1,
                total_executions = agent_instances.total_executions + 1,
                last_execution_at = NOW()
            """,
            {"name": agent_name},
        )

    async def decrement_agent_instances(self, agent_name: str) -> None:
        """Decrement active count for an agent (floor at 0)."""
        await self._db.execute(
            """
            UPDATE agent_instances
            SET active_count = GREATEST(active_count - 1, 0)
            WHERE agent_name = %(name)s
            """,
            {"name": agent_name},
        )

    def get_agent_prompt_file(self, agent_name: str) -> Path:
        """Get the prompt file path for an agent.

        For hybrid ``.md`` definition files (no ``promptFile`` key), returns
        the definition file path itself.  For legacy agents with an explicit
        ``promptFile``, resolves it relative to the definition file's parent
        directory.  Validates the resolved path stays within the agents config
        tree (``agents_dir.parent``) to prevent path traversal.
        """
        spec = self._agents.get(agent_name, {})
        definition_file = spec.get("_definition_file")

        # Hybrid .md file without an explicit promptFile → the definition IS the prompt
        if definition_file and definition_file.endswith(".md") and "promptFile" not in spec:
            return Path(definition_file).resolve()

        prompt_file: str = spec.get("promptFile", f"{agent_name}.md")

        # Determine the base directory for resolution
        if definition_file:
            base_dir = Path(definition_file).parent
        else:
            # Fallback for agents without a definition file (e.g. from DB)
            base_dir = self._agents_dir

        resolved = (base_dir / prompt_file).resolve()
        config_root = self._agents_dir.parent.resolve()
        if not resolved.is_relative_to(config_root):
            raise AgentRegistryError(
                f"Prompt file path escapes config directory: {prompt_file}"
            )
        return resolved

    def get_agent_prompt_content(self, agent_name: str) -> str | None:
        """Return the embedded Markdown prompt body from a hybrid .md file.

        Returns ``None`` for agents loaded from JSON registry or database
        (no embedded prompt available).
        """
        spec = self._agents.get(agent_name, {})
        definition_file = spec.get("_definition_file")
        if not definition_file or not definition_file.endswith(".md"):
            return None
        try:
            _frontmatter, prompt_body = _parse_md_agent_file(Path(definition_file))
            return prompt_body.strip() or None
        except (ValueError, yaml.YAMLError, OSError):
            return None

    def get_agent_model(self, agent_name: str) -> str | None:
        """Get the model for an agent, or None if not set (CLI default)."""
        spec = self._agents.get(agent_name, {})
        model: str | None = spec.get("model")
        return model or None

    def get_agent_timeout(self, agent_name: str) -> int:
        """Get timeout in minutes for an agent."""
        spec = self._agents.get(agent_name, {})
        resources: dict[str, Any] = spec.get("resources", {})
        timeout: int = resources.get("timeoutMinutes", 60)
        return timeout

    def get_agent_max_turns(self, agent_name: str) -> int:
        """Get max conversation turns for an agent."""
        spec = self._agents.get(agent_name, {})
        resources: dict[str, Any] = spec.get("resources", {})
        max_turns: int = resources.get("maxTurns", 30)
        return max_turns

    def get_agent_max_cost(self, agent_name: str) -> float:
        """Get max cost in USD for a single stage invocation (including continuations)."""
        spec = self._agents.get(agent_name, {})
        resources: dict[str, Any] = spec.get("resources", {})
        max_cost: float = resources.get("maxCost", 5.0)
        return max_cost

    def get_allowed_tools(self, agent_name: str) -> list[str]:
        """Get the list of allowed tools for an agent."""
        spec = self._agents.get(agent_name, {})
        tools_section: dict[str, Any] = spec.get("tools", {})
        tools: list[str] = tools_section.get("allowed", [])
        return tools

    def get_denied_tools(self, agent_name: str) -> list[str]:
        """Get the list of denied tools for an agent."""
        spec = self._agents.get(agent_name, {})
        tools_section: dict[str, Any] = spec.get("tools", {})
        tools: list[str] = tools_section.get("denied", [])
        return tools

    def get_agent_output_schema(self, agent_name: str) -> dict[str, Any] | None:
        """Get the outputSchema for an agent, if defined."""
        spec = self._agents.get(agent_name, {})
        schema: dict[str, Any] | None = spec.get("outputSchema")
        return schema if schema else None

    # Default environment for agent task execution.
    # Agents get their own virtualenv so pip installs don't corrupt the supervisor.
    _AGENT_VENV = "/home/agent/.agent-venv"
    _AGENT_DEFAULT_ENV: dict[str, str] = {
        "VIRTUAL_ENV": _AGENT_VENV,
        "PATH": f"{_AGENT_VENV}/bin:/usr/local/bin:/usr/bin:/bin:/home/agent/.npm-global/bin",
    }

    def get_agent_environment(self, agent_name: str) -> dict[str, str]:
        """Get environment variables for an agent.

        Merges per-agent env from the definition on top of the default agent
        venv environment, so agents run in an isolated virtualenv by default.
        """
        spec = self._agents.get(agent_name, {})
        env: dict[str, str] = {**self._AGENT_DEFAULT_ENV, **spec.get("environment", {})}
        return env

    def _get_max_concurrent(self, agent_name: str) -> int:
        """Get max concurrent instances for an agent."""
        spec = self._agents.get(agent_name, {})
        resources: dict[str, Any] = spec.get("resources", {})
        max_conc: int = resources.get("maxConcurrent", 1)
        return max_conc

    async def get_all_agent_definitions_json(self) -> list[dict[str, Any]]:
        """Return all active agent definitions as serializable dicts.

        Tries the database first (agent_definitions table), falls back to
        in-memory registry loaded from hybrid .md definition files.
        """
        try:
            rows = await self._db.fetch_all(
                """
                SELECT name, version, description, spec,
                       COALESCE(agent_group, 'pipeline') AS agent_group
                FROM agent_definitions
                WHERE is_active = TRUE
                ORDER BY name
                """
            )
            if rows:
                result: list[dict[str, Any]] = []
                for row in rows:
                    spec = row["spec"] if isinstance(row["spec"], dict) else json.loads(row["spec"])
                    db_group = row.get("agent_group", "pipeline")
                    result.append({
                        "name": row["name"],
                        "version": row["version"],
                        "description": row["description"],
                        "group": db_group.upper(),
                        "categories": spec.get("categories", []),
                        "conditions": spec.get("conditions", {}),
                        "resources": spec.get("resources", {}),
                        "outputSchema": spec.get("outputSchema", {}),
                        "priority": spec.get("priority", 50),
                    })
                return result
        except Exception:
            log.debug("agent_definitions_db_fallback", reason="DB query failed, using in-memory")

        # Fallback: in-memory registry
        return [
            {
                "name": name,
                "version": spec.get("version", "0.0.0"),
                "description": spec.get("description", ""),
                "group": spec.get("_group", "pipeline").upper(),
                "categories": spec.get("categories", []),
                "conditions": spec.get("conditions", {}),
                "resources": spec.get("resources", {}),
                "outputSchema": spec.get("outputSchema", {}),
                "priority": spec.get("priority", 50),
            }
            for name, spec in self._agents.items()
        ]

    def should_skip_planning(self, categories: list[str]) -> bool:
        """Return True if every category has exactly one registered agent.

        Fast path: no decision needed when there's only one option per category.
        """
        for category in categories:
            agents = self.get_agents_for_category(category)
            if len(agents) != 1:
                return False
        return True

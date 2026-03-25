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


class AgentRegistry:
    """Manages agent definitions, capacity, and instance tracking."""

    def __init__(self, db: Database, agents_dir: str, prompts_dir: str) -> None:
        self._db = db
        self._agents_dir = Path(agents_dir)
        self._prompts_dir = Path(prompts_dir)
        self._agents: dict[str, dict[str, Any]] = {}

    async def load(self, registry_file: str | None = None) -> None:
        """Load agent registry from JSON file or discover from YAML definitions.

        Also loads autoloaded agents from the database for all registered
        repositories.
        """
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

        # Load autoloaded agents from DB
        await self._load_autoloaded_agents()

        await self._sync_agent_instances()
        log.info("registry_loaded", agent_count=len(self._agents))

    async def _discover_agents(self) -> None:
        """Discover agents from YAML definition files."""
        if not self._agents_dir.exists():
            log.warning("agents_dir_not_found", path=str(self._agents_dir))
            return

        for yaml_file in sorted(self._agents_dir.glob("*.yaml")):
            try:
                raw = yaml.safe_load(yaml_file.read_text())
                if not isinstance(raw, dict) or raw.get("kind") != "AgentDefinition":
                    continue
                name = raw.get("metadata", {}).get("name", yaml_file.stem)
                self._agents[name] = raw.get("spec", raw)
                self._agents[name]["name"] = name
            except yaml.YAMLError:
                log.warning("agent_yaml_parse_error", file=str(yaml_file))

    async def _load_autoloaded_agents(self) -> None:
        """Load autoloaded agents from the database for all registered repositories."""
        try:
            rows = await self._db.fetch_all(
                """SELECT ad.name, ad.spec, ad.source
                   FROM agent_definitions ad
                   WHERE ad.is_active = true
                     AND ad.source LIKE 'autoload:%%'"""
            )
            for row in rows:
                name = row["name"]
                spec = row["spec"] if isinstance(row["spec"], dict) else json.loads(row["spec"])
                spec["name"] = name
                self._agents[name] = spec
            if rows:
                log.info("autoloaded_agents_loaded", count=len(rows))
        except Exception:
            log.debug("autoloaded_agents_load_skipped", reason="DB query failed or table missing")

    async def _sync_agent_instances(self) -> None:
        """Ensure all agents have rows in agent_instances table."""
        for name in self._agents:
            await self._db.execute(
                """
                INSERT INTO agent_instances (agent_name, active_count, total_executions)
                VALUES (%(name)s, 0, 0)
                ON CONFLICT (agent_name) DO NOTHING
                """,
                {"name": name},
            )

    def get_agents_for_category(self, category: str) -> list[str]:
        """Get agent names that handle a category, sorted by priority."""
        matching = []
        for name, spec in self._agents.items():
            categories = spec.get("categories", [])
            if category in categories:
                priority = spec.get("priority", 50)
                matching.append((priority, name))
        matching.sort()
        return [name for _, name in matching]

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

        Validates the resolved path stays within prompts_dir to prevent
        path traversal via malicious promptFile values.
        """
        spec = self._agents.get(agent_name, {})
        prompt_file: str = spec.get("promptFile", f"{agent_name}.md")
        resolved = (self._prompts_dir / prompt_file).resolve()
        if not resolved.is_relative_to(self._prompts_dir.resolve()):
            raise AgentRegistryError(
                f"Prompt file path escapes prompts directory: {prompt_file}"
            )
        return resolved

    def get_agent_timeout(self, agent_name: str) -> int:
        """Get timeout in minutes for an agent."""
        spec = self._agents.get(agent_name, {})
        resources: dict[str, Any] = spec.get("resources", {})
        timeout: int = resources.get("timeoutMinutes", 30)
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

    def get_agent_environment(self, agent_name: str) -> dict[str, str]:
        """Get environment variables for an agent."""
        spec = self._agents.get(agent_name, {})
        env: dict[str, str] = spec.get("environment", {})
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
        in-memory registry loaded from YAML files.
        """
        try:
            rows = await self._db.fetch_all(
                """
                SELECT name, version, description, spec
                FROM agent_definitions
                WHERE is_active = TRUE
                ORDER BY name
                """
            )
            if rows:
                result: list[dict[str, Any]] = []
                for row in rows:
                    spec = row["spec"] if isinstance(row["spec"], dict) else json.loads(row["spec"])
                    result.append({
                        "name": row["name"],
                        "version": row["version"],
                        "description": row["description"],
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
                "categories": spec.get("categories", []),
                "conditions": spec.get("conditions", {}),
                "resources": spec.get("resources", {}),
                "outputSchema": spec.get("outputSchema", {}),
                "priority": spec.get("priority", 50),
            }
            for name, spec in self._agents.items()
        ]

    def get_default_agents(self) -> dict[str, dict[str, Any]]:
        """Return a copy of all loaded agent definitions."""
        return dict(self._agents)

    def get_default_prompts_dir(self) -> Path:
        """Return the default prompts directory path."""
        return self._prompts_dir

    def should_skip_planning(self, categories: list[str]) -> bool:
        """Return True if every category has exactly one registered agent.

        Fast path: no decision needed when there's only one option per category.
        """
        for category in categories:
            agents = self.get_agents_for_category(category)
            if len(agents) != 1:
                return False
        return True

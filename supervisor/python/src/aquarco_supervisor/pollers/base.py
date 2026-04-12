"""Abstract poller interface."""

from __future__ import annotations

import abc
from typing import Any

from ..database import Database
from ..logging import get_logger
from ..models import SupervisorConfig
from ..task_queue import TaskQueue

log = get_logger("poller")


class BasePoller(abc.ABC):
    """Base class for all pollers."""

    name: str = ""

    def __init__(
        self, config: SupervisorConfig, task_queue: TaskQueue, db: Database,
    ) -> None:
        self._config = config
        self._tq = task_queue
        self._db = db

    @abc.abstractmethod
    async def poll(self) -> int:
        """Execute one poll cycle. Returns number of tasks created."""
        ...

    async def _get_repositories(self, poller_name: str | None = None) -> list[dict[str, Any]]:
        """Fetch repositories from DB, optionally filtered by poller name."""
        if poller_name:
            rows = await self._db.fetch_all(
                """
                SELECT name, url, branch, clone_dir, pollers, git_flow_config
                FROM repositories
                WHERE clone_status = 'ready'
                  AND %(poller)s = ANY(pollers)
                """,
                {"poller": poller_name},
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT name, url, branch, clone_dir, pollers, git_flow_config
                FROM repositories
                WHERE clone_status = 'ready'
                """
            )
        return [dict(r) for r in rows]

    def _get_poller_config(self) -> dict[str, Any]:
        """Get this poller's config block from supervisor config."""
        for poller in self._config.spec.pollers:
            if poller.name == self.name:
                return poller.config
        return {}

    def is_enabled(self) -> bool:
        """Check if this poller is enabled."""
        for poller in self._config.spec.pollers:
            if poller.name == self.name:
                return poller.enabled
        return False

    def get_interval(self) -> int:
        """Get poll interval in seconds."""
        for poller in self._config.spec.pollers:
            if poller.name == self.name:
                return poller.interval_seconds
        return 60

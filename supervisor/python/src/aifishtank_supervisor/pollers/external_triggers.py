"""External trigger poller - watches for trigger files."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..database import Database
from ..logging import get_logger
from ..models import SupervisorConfig
from ..task_queue import TaskQueue
from .base import BasePoller

log = get_logger("external-triggers")


class ExternalTriggersPoller(BasePoller):
    """Watches a directory for trigger files (YAML/JSON)."""

    name = "external-triggers"

    def __init__(
        self, config: SupervisorConfig, task_queue: TaskQueue, db: Database,
    ) -> None:
        super().__init__(config, task_queue, db)
        poller_cfg = self._get_poller_config()
        self._watch_dir = Path(poller_cfg.get("watchDir", "/var/lib/aifishtank/triggers"))
        self._processed_dir = Path(
            poller_cfg.get("processedDir", str(self._watch_dir / "processed"))
        )

    async def poll(self) -> int:
        """Scan watch directory for trigger files."""
        if not self._watch_dir.exists():
            return 0

        self._processed_dir.mkdir(parents=True, exist_ok=True)
        total_created = 0

        for pattern in ("*.yaml", "*.yml", "*.json"):
            for trigger_file in sorted(self._watch_dir.glob(pattern)):
                if trigger_file.is_file():
                    created = await self._process_trigger_file(trigger_file)
                    if created:
                        total_created += 1

        if total_created > 0:
            await self._tq.update_poll_state(
                self.name,
                datetime.now(timezone.utc).isoformat(),
                {"tasks_created": total_created},
            )

        return total_created

    async def _process_trigger_file(self, trigger_file: Path) -> bool:
        """Process a single trigger file and create a task."""
        # Parse file
        content = trigger_file.read_text()
        data: dict[str, Any] | None = None

        if trigger_file.suffix in (".yaml", ".yml"):
            try:
                data = yaml.safe_load(content)
            except yaml.YAMLError:
                self._move_to_failed(trigger_file, "yaml-parse-error")
                return False
        else:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                self._move_to_failed(trigger_file, "json-parse-error")
                return False

        if not isinstance(data, dict):
            self._move_to_failed(trigger_file, "invalid-format")
            return False

        # Validate required fields
        title = data.get("title")
        repository = data.get("repository")
        pipeline = data.get("pipeline", "feature-pipeline")

        if not title:
            self._move_to_failed(trigger_file, "missing-title")
            return False
        if not repository:
            self._move_to_failed(trigger_file, "missing-repository")
            return False

        # Validate repository exists in DB
        repo_exists = await self._db.fetch_val(
            "SELECT COUNT(*) FROM repositories WHERE name = %(name)s",
            {"name": repository},
        )
        if not repo_exists:
            self._move_to_failed(trigger_file, f"unknown-repository-{repository}")
            return False

        # Generate task ID
        file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        task_id = f"external-{repository}-{file_hash}"

        # Check idempotency
        if await self._tq.task_exists(task_id):
            self._move_to_processed(trigger_file)
            return False

        # Build context
        context = data.get("context", {})
        context["_trigger_file"] = trigger_file.name
        context["_triggered_at"] = datetime.now(timezone.utc).isoformat()
        if "labels" in data:
            context["_labels"] = data["labels"]

        source_ref = data.get("source_ref", "")

        created = await self._tq.create_task(
            task_id=task_id,
            title=title,
            source="external-trigger",
            source_ref=source_ref,
            repository=repository,
            pipeline=pipeline,
            context=context,
        )

        self._move_to_processed(trigger_file)
        return created

    def _move_to_processed(self, trigger_file: Path) -> None:
        """Move trigger file to processed directory."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%Sz")
        dest = self._processed_dir / f"{ts}-{trigger_file.name}"
        try:
            shutil.move(str(trigger_file), str(dest))
        except OSError as e:
            log.warning(
                "trigger_move_failed", file=trigger_file.name, error=str(e)
            )

    def _move_to_failed(self, trigger_file: Path, reason: str) -> None:
        """Move trigger file to failed subdirectory."""
        failed_dir = self._processed_dir / "failed"
        failed_dir.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%Sz")
        dest = failed_dir / f"{ts}-{reason}-{trigger_file.name}"
        try:
            shutil.move(str(trigger_file), str(dest))
        except OSError as e:
            log.warning(
                "trigger_move_failed", file=trigger_file.name, error=str(e)
            )
        log.warning("trigger_failed", file=trigger_file.name, reason=reason)

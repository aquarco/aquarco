"""GitHub issue poller - polls for issues labelled agent-task."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..logging import get_logger
from ..models import SupervisorConfig
from ..task_queue import TaskQueue
from ..utils import url_to_slug as _url_to_slug
from .base import BasePoller

log = get_logger("github-tasks")


class GitHubTasksPoller(BasePoller):
    """Polls GitHub issues with the agent-task label."""

    name = "github-tasks"

    def __init__(self, config: SupervisorConfig, task_queue: TaskQueue) -> None:
        super().__init__(config, task_queue)
        poller_cfg = self._get_poller_config()
        categorization = poller_cfg.get("categorization", {})
        self._label_mapping: dict[str, str] = categorization.get("labelMapping", {})
        self._default_category: str = categorization.get("defaultCategory", "analyze")

    async def poll(self) -> int:
        """Poll GitHub issues and create tasks."""
        cursor = await self._tq.get_poll_cursor(self.name)
        if not cursor:
            cursor = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        total_created = 0

        for repo in self._config.spec.repositories:
            if self.name not in repo.pollers:
                continue

            slug = _url_to_slug(repo.url)
            if not slug:
                continue

            try:
                issues = await _gh_list_issues(slug, cursor)
            except Exception as e:
                log.error("gh_issue_list_failed", repo=slug, error=str(e))
                continue

            for issue in issues:
                created = await self._process_issue(issue, repo.name, slug)
                if created:
                    total_created += 1

        new_cursor = datetime.now(timezone.utc).isoformat()
        await self._tq.update_poll_state(
            self.name,
            new_cursor,
            {"tasks_created": total_created},
        )

        if total_created > 0:
            log.info("poll_complete", tasks_created=total_created)
        return total_created

    async def _process_issue(
        self, issue: dict[str, Any], repo_name: str, repo_slug: str
    ) -> bool:
        """Process a single GitHub issue into a task."""
        number = issue.get("number")
        task_id = f"github-issue-{repo_name}-{number}"

        if await self._tq.task_exists(task_id):
            return False

        labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]
        category = self._categorize(labels)
        pipeline = self._select_pipeline(labels)

        context = {
            "github_issue_number": number,
            "title": issue.get("title", ""),
            "body": issue.get("body", ""),
            "url": issue.get("url", ""),
            "repository_slug": repo_slug,
            "labels": labels,
        }

        return await self._tq.create_task(
            task_id=task_id,
            title=issue.get("title", f"Issue #{number}"),
            category=category,
            source="github-issues",
            source_ref=str(number),
            repository=repo_name,
            pipeline=pipeline,
            context=context,
        )

    def _categorize(self, labels: list[str]) -> str:
        """Map issue labels to a task category."""
        for label in labels:
            if label in self._label_mapping:
                return self._label_mapping[label]
        return self._default_category

    def _select_pipeline(self, labels: list[str]) -> str:
        """Select a pipeline based on issue labels."""
        label_set = set(labels)
        for pipeline in self._config.spec.pipelines:
            trigger_labels = set(pipeline.trigger.labels)
            if trigger_labels and trigger_labels & label_set:
                return pipeline.name
        return "feature-pipeline"


async def _gh_list_issues(
    repo_slug: str, since: str, timeout: int = 60
) -> list[dict[str, Any]]:
    """Call gh issue list and return parsed JSON."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "issue", "list",
        "--repo", repo_slug,
        "--label", "agent-task",
        "--state", "open",
        "--search", f"updated:>{since}",
        "--json", "number,title,labels,body,createdAt,updatedAt,url",
        "--limit", "100",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"gh issue list timed out after {timeout}s for {repo_slug}")
    if proc.returncode != 0:
        raise RuntimeError(f"gh issue list failed: {stderr.decode('utf-8', errors='replace')}")
    issues: list[dict[str, Any]] = json.loads(stdout.decode())
    return issues

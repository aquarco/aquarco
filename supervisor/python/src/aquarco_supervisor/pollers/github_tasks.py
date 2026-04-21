"""GitHub issue poller - polls for issues labelled agent-task."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..database import Database
from ..exceptions import GitHubAuthenticationError
from ..logging import get_logger
from ..models import PipelineConfig, SupervisorConfig
from ..task_queue import TaskQueue
from ..utils import url_to_slug as _url_to_slug
from .auth_utils import is_github_auth_error as _is_github_auth_error
from .base import BasePoller

# Map branch type → default pipeline name when rule has no explicit pipeline.
_BRANCH_TYPE_PIPELINE: dict[str, str] = {
    "feature": "feature-pipeline",
    "bugfix": "bugfix-pipeline",
    "hotfix": "bugfix-pipeline",
}

log = get_logger("github-tasks")


class GitHubTasksPoller(BasePoller):
    """Polls GitHub issues with the agent-task label."""

    name = "github-tasks"

    def __init__(
        self,
        config: SupervisorConfig,
        task_queue: TaskQueue,
        db: Database,
        pipelines: list[PipelineConfig] | None = None,
    ) -> None:
        super().__init__(config, task_queue, db)
        self._pipelines = pipelines or []

    async def poll(self) -> int:
        """Poll GitHub issues and create tasks."""
        cursor = await self._tq.get_poll_cursor(self.name)
        if not cursor:
            cursor = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        total_created = 0

        for repo in await self._get_repositories(self.name):
            slug = _url_to_slug(repo["url"])
            if not slug:
                continue

            try:
                issues = await _gh_list_issues(slug, cursor)
            except GitHubAuthenticationError:
                raise
            except Exception as e:
                log.error("gh_issue_list_failed", repo=slug, error=str(e))
                continue

            for issue in issues:
                created = await self._process_issue(issue, repo["name"], slug, repo)
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
        self,
        issue: dict[str, Any],
        repo_name: str,
        repo_slug: str,
        repo: dict[str, Any] | None = None,
    ) -> bool:
        """Process a single GitHub issue into a task."""
        number = issue.get("number")
        task_id = f"github-issue-{repo_name}-{number}"

        if await self._tq.task_exists(task_id):
            return False

        labels = [lbl.get("name", "") for lbl in issue.get("labels", [])]
        repo_rules = self._extract_repo_rules(repo)
        pipeline = self._select_pipeline(labels, repo_rules)

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
            source="github-issues",
            source_ref=str(number),
            repository=repo_name,
            pipeline=pipeline,
            context=context,
        )

    def _extract_repo_rules(self, repo: dict[str, Any] | None) -> dict[str, Any] | None:
        """Parse git_flow_config.rules from a repository row, or None."""
        if not repo:
            return None
        raw_gfc = repo.get("git_flow_config")
        if not raw_gfc:
            return None
        if isinstance(raw_gfc, str):
            try:
                raw_gfc = json.loads(raw_gfc)
            except (ValueError, TypeError):
                return None
        if not isinstance(raw_gfc, dict):
            return None
        # Note: rules are used for pipeline selection regardless of git-flow mode.
        return raw_gfc.get("rules") or None

    def _select_pipeline(
        self,
        labels: list[str],
        repo_rules: dict[str, Any] | None = None,
    ) -> str:
        """Select a pipeline based on issue labels.

        Repo-level rules (from git_flow_config.rules) take precedence over the
        global pipelines.yaml trigger labels.
        """
        label_set = {lbl.lower().strip() for lbl in labels}

        # --- Repo-level rules (highest priority) ---
        if repo_rules:
            # hotfix > bugfix > feature priority
            for branch_type in ("hotfix", "bugfix", "feature"):
                rule = repo_rules.get(branch_type)
                if not isinstance(rule, dict):
                    continue
                rule_labels = {
                    lbl.lower().strip()
                    for lbl in rule.get("issueLabels", [])
                }
                if rule_labels and rule_labels & label_set:
                    explicit = rule.get("pipeline")
                    if explicit:
                        return explicit
                    return _BRANCH_TYPE_PIPELINE.get(branch_type, "feature-pipeline")

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
        err_text = stderr.decode("utf-8", errors="replace")
        if _is_github_auth_error(err_text):
            raise GitHubAuthenticationError(f"GitHub authentication failed for {repo_slug}: {err_text[:200]}")
        raise RuntimeError(f"gh issue list failed: {err_text}")
    issues: list[dict[str, Any]] = json.loads(stdout.decode())
    return issues

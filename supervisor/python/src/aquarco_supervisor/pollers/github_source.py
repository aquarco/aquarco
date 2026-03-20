"""GitHub source poller - polls PRs and recent commits."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..database import Database
from ..logging import get_logger
from ..models import SupervisorConfig
from ..task_queue import TaskQueue
from ..utils import run_git as _run_git
from ..utils import url_to_slug as _url_to_slug
from .base import BasePoller

log = get_logger("github-source")


class GitHubSourcePoller(BasePoller):
    """Polls GitHub for open PRs and recent commits."""

    name = "github-source"

    def __init__(
        self, config: SupervisorConfig, task_queue: TaskQueue, db: Database,
    ) -> None:
        super().__init__(config, task_queue, db)
        poller_cfg = self._get_poller_config()
        self._triggers: dict[str, list[str]] = poller_cfg.get("triggers", {})

    async def poll(self) -> int:
        """Poll PRs and commits across repositories."""
        cursor = await self._tq.get_poll_cursor(self.name)
        if not cursor:
            cursor = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

        total_created = 0

        for repo in await self._get_repositories(self.name):
            slug = _url_to_slug(repo["url"])
            if not slug:
                continue

            try:
                created = await self._poll_prs(repo["name"], slug, cursor)
                total_created += created
            except Exception as e:
                log.error("pr_poll_failed", repo=slug, error=str(e))

            try:
                created = await self._poll_commits(repo["name"], repo["clone_dir"], cursor)
                total_created += created
            except Exception as e:
                log.error("commit_poll_failed", repo=repo["name"], error=str(e))

        new_cursor = datetime.now(timezone.utc).isoformat()
        await self._tq.update_poll_state(
            self.name,
            new_cursor,
            {"tasks_created": total_created},
        )

        if total_created > 0:
            log.info("poll_complete", tasks_created=total_created)
        return total_created

    async def _poll_prs(self, repo_name: str, repo_slug: str, cursor: str) -> int:
        """Poll open PRs for a repository."""
        prs = await _gh_list_prs(repo_slug)
        created = 0

        for pr in prs:
            updated_at = pr.get("updatedAt", "")
            created_at = pr.get("createdAt", "")

            if updated_at <= cursor and created_at <= cursor:
                continue

            event_type = "pr_opened" if created_at > cursor else "pr_updated"
            result = await self._process_pr(pr, repo_name, repo_slug, event_type)
            created += result

        return created

    async def _poll_commits(self, repo_name: str, clone_dir: str, cursor: str) -> int:
        """Poll recent commits for a repository."""
        if not Path(clone_dir).joinpath(".git").exists():
            return 0

        # Fetch latest
        await _run_git(clone_dir, "fetch", "--quiet", "origin")

        # Get default branch
        try:
            head_ref = await _run_git(clone_dir, "rev-parse", "--abbrev-ref", "origin/HEAD")
            default_branch = head_ref.replace("origin/", "")
        except Exception:
            default_branch = "main"

        # Get recent commits
        output = await _run_git(
            clone_dir, "log", f"origin/{default_branch}",
            f"--since={cursor}",
            "--pretty=format:%H\t%s\t%an\t%aI",
        )

        if not output.strip():
            return 0

        created = 0
        for line in output.strip().split("\n"):
            parts = line.split("\t", 3)
            if len(parts) < 4:
                continue

            sha, subject, author, date = parts
            task_id = f"github-commit-{repo_name}-{sha[:12]}"

            if await self._tq.task_exists(task_id):
                continue

            context = {
                "commit_sha": sha,
                "commit_subject": subject,
                "commit_author": author,
                "commit_date": date,
            }

            if await self._tq.create_task(
                task_id=task_id,
                title=f"Review commit: {subject[:80]}",
                source="github-commits",
                source_ref=sha,
                repository=repo_name,
                pipeline="pr-review-pipeline",
                context=context,
            ):
                created += 1

        return created

    async def _process_pr(
        self,
        pr: dict[str, Any],
        repo_name: str,
        repo_slug: str,
        event_type: str,
    ) -> int:
        """Process a PR and create tasks for trigger categories."""
        head_branch = pr.get("headRefName", "")

        # Skip system-created PRs
        if head_branch.startswith("aquarco/"):
            return 0

        pipelines = self._triggers.get(event_type, [])
        if not pipelines:
            return 0

        number = pr.get("number")
        created = 0

        for pipeline in pipelines:
            if event_type == "pr_opened":
                task_id = f"github-pr-{repo_name}-{number}-{pipeline}"
            else:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
                task_id = f"github-pr-{repo_name}-{number}-{pipeline}-{ts}"

            if await self._tq.task_exists(task_id):
                continue

            context = {
                "github_pr_number": number,
                "title": pr.get("title", ""),
                "head_branch": head_branch,
                "base_branch": pr.get("baseRefName", "main"),
                "url": pr.get("url", ""),
                "additions": pr.get("additions", 0),
                "deletions": pr.get("deletions", 0),
                "changed_files": pr.get("changedFiles", 0),
                "repository_slug": repo_slug,
            }

            if await self._tq.create_task(
                task_id=task_id,
                title=f"PR #{number}: {pr.get('title', '')}",
                source="github-prs",
                source_ref=str(number),
                repository=repo_name,
                pipeline=pipeline,
                context=context,
            ):
                created += 1

        return created


async def _gh_list_prs(repo_slug: str, timeout: int = 60) -> list[dict[str, Any]]:
    """Call gh pr list and return parsed JSON."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "list",
        "--repo", repo_slug,
        "--state", "open",
        "--json",
        "number,title,headRefName,baseRefName,url,labels,"
        "createdAt,updatedAt,additions,deletions,changedFiles",
        "--limit", "50",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"gh pr list timed out after {timeout}s for {repo_slug}")
    if proc.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {stderr.decode('utf-8', errors='replace')}")
    prs: list[dict[str, Any]] = json.loads(stdout.decode())
    return prs


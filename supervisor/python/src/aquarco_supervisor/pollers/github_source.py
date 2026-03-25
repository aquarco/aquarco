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
        head_sha_updates: dict[str, str] = {}

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
                created, new_sha = await self._poll_commits(
                    repo["name"], repo["clone_dir"], cursor,
                )
                total_created += created
                if new_sha:
                    head_sha_updates[f"head_sha:{repo['name']}"] = new_sha
            except Exception as e:
                log.error("commit_poll_failed", repo=repo["name"], error=str(e))

        new_cursor = datetime.now(timezone.utc).isoformat()
        state = {"tasks_created": total_created, **head_sha_updates}
        await self._tq.update_poll_state(self.name, new_cursor, state)

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

    async def _poll_commits(
        self, repo_name: str, clone_dir: str, cursor: str,
    ) -> tuple[int, str | None]:
        """Poll recent commits for a repository.

        Returns (tasks_created, new_head_sha).
        """
        if not Path(clone_dir).joinpath(".git").exists():
            return 0, None

        # Get the last processed sha from poll state (not repositories.head_sha
        # which the pull worker updates independently).
        state_row = await self._db.fetch_one(
            "SELECT state_data FROM poll_state WHERE poller_name = %(name)s",
            {"name": self.name},
        )
        state_data = state_row["state_data"] if state_row else {}
        if isinstance(state_data, str):
            import json as _json
            state_data = _json.loads(state_data)
        old_head_sha = state_data.get(f"head_sha:{repo_name}")

        # Use the local repo state (pull_worker keeps it up to date via git fetch).
        # No git-fetch here — avoids auth issues and duplicate fetches.

        # Get default branch
        try:
            head_ref = await _run_git(clone_dir, "rev-parse", "--abbrev-ref", "origin/HEAD")
            default_branch = head_ref.replace("origin/", "")
        except Exception:
            default_branch = "main"

        # Get current head sha from local refs (updated by pull_worker)
        new_head_sha = await _run_git(
            clone_dir, "rev-parse", f"origin/{default_branch}",
        )

        # No new commits if head hasn't changed
        if old_head_sha and old_head_sha == new_head_sha:
            return 0, None

        log.info(
            "commit_poll_head_changed",
            repo=repo_name,
            old_sha=old_head_sha[:12] if old_head_sha else None,
            new_sha=new_head_sha[:12],
        )

        # Get commits between old head and new head (sha-based, not time-based)
        if old_head_sha:
            rev_range = f"{old_head_sha}..origin/{default_branch}"
        else:
            rev_range = f"origin/{default_branch}"

        args = [
            clone_dir, "log", rev_range,
            "--no-merges",
            "--pretty=format:%H\t%s\t%an\t%aI",
        ]
        if not old_head_sha:
            args.extend([f"--since={cursor}"])

        output = await _run_git(*args)

        if not output.strip():
            return 0, new_head_sha

        # Collect commits, filtering out pipeline merges
        commits: list[dict[str, str]] = []
        for line in output.strip().split("\n"):
            parts = line.split("\t", 3)
            if len(parts) < 4:
                continue
            sha, subject, author, date = parts
            if "aquarco/" in subject:
                log.debug("skip_pipeline_merge", sha=sha[:12], subject=subject)
                continue
            commits.append({
                "sha": sha, "subject": subject,
                "author": author, "date": date,
            })

        if not commits:
            return 0, new_head_sha

        # Create a single task per push (batch of commits)
        task_id = f"github-push-{repo_name}-{new_head_sha[:12]}"
        if await self._tq.task_exists(task_id):
            return 0, new_head_sha

        # Build a summary for the task title
        if len(commits) == 1:
            title = f"Review push: {commits[0]['subject'][:80]}"
        else:
            title = f"Review push: {len(commits)} commits ({old_head_sha[:8] if old_head_sha else '?'}..{new_head_sha[:8]})"

        context: dict[str, Any] = {
            "push_old_sha": old_head_sha,
            "push_new_sha": new_head_sha,
            "commit_count": len(commits),
            "commits": commits,
        }

        log.info(
            "commit_poll_creating_push_task",
            repo=repo_name,
            commit_count=len(commits),
            task_id=task_id,
        )

        created = 0
        if await self._tq.create_task(
            task_id=task_id,
            title=title,
            source="github-commits",
            source_ref=new_head_sha,
            repository=repo_name,
            pipeline="pr-review-pipeline",
            context=context,
        ):
            created = 1

        return created, new_head_sha

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


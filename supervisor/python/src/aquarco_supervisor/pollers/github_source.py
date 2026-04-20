"""GitHub source poller - polls PRs and recent commits."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..database import Database
from ..exceptions import GitHubAuthenticationError
from ..logging import get_logger
from ..models import GitFlowConfig, SupervisorConfig
from ..pipeline.git_workflow import (
    find_active_release_branch,
    perform_back_merge,
    resolve_back_merge_target,
)
from ..task_queue import TaskQueue
from ..utils import run_git as _run_git
from ..utils import url_to_slug as _url_to_slug
from .base import BasePoller

log = get_logger("github-source")

# Git SHAs are exactly 40 lowercase hex characters.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Branch names must not contain shell metacharacters or start with a dash.
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]*$")


def _validate_sha(value: str, context: str) -> str:
    """Return value unchanged if it is a valid 40-hex SHA, else raise."""
    if not _SHA_RE.match(value.strip()):
        raise ValueError(f"Invalid git SHA in {context!r}: {value!r}")
    return value.strip()


def _validate_branch(value: str, context: str) -> str:
    """Return value unchanged if it is a safe branch name, else raise."""
    if not _SAFE_BRANCH_RE.match(value):
        raise ValueError(f"Unsafe branch name in {context!r}: {value!r}")
    return value


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
            except GitHubAuthenticationError:
                raise
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

            # Poll merged PRs for back-merge in Git Flow repos
            try:
                await self._poll_merged_prs(repo["name"], slug, cursor)
            except GitHubAuthenticationError:
                raise
            except Exception as e:
                log.error("merged_pr_poll_failed", repo=slug, error=str(e))

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
            state_data = json.loads(state_data)
        raw_old_sha = state_data.get(f"head_sha:{repo_name}")
        # Validate the stored SHA before using it in a git argument.  A
        # corrupted or tampered state_data value must not reach the git CLI.
        old_head_sha: str | None = None
        if raw_old_sha is not None:
            try:
                old_head_sha = _validate_sha(str(raw_old_sha), "poll_state head_sha")
            except ValueError:
                log.warning(
                    "commit_poll_invalid_stored_sha",
                    repo=repo_name,
                    raw=str(raw_old_sha)[:16],
                )
                old_head_sha = None

        # Use the local repo state (pull_worker keeps it up to date via git fetch).
        # No git-fetch here — avoids auth issues and duplicate fetches.

        # Get default branch and validate it before using in git arguments.
        try:
            head_ref = await _run_git(clone_dir, "rev-parse", "--abbrev-ref", "origin/HEAD")
            raw_branch = head_ref.replace("origin/", "", 1).strip()
            default_branch = _validate_branch(raw_branch, "origin/HEAD")
        except Exception:
            default_branch = "main"

        # Get current head sha from local refs (updated by pull_worker).
        # Validate the git output — never trust subprocess results blindly.
        raw_new_sha = await _run_git(
            clone_dir, "rev-parse", f"origin/{default_branch}",
        )
        new_head_sha = _validate_sha(raw_new_sha, "rev-parse origin/default_branch")

        # No new commits if head hasn't changed
        if old_head_sha and old_head_sha == new_head_sha:
            return 0, None

        log.info(
            "commit_poll_head_changed",
            repo=repo_name,
            old_sha=old_head_sha[:12] if old_head_sha else None,
            new_sha=new_head_sha[:12],
        )

        # Build the rev-range from validated SHAs and branch names only.
        if old_head_sha:
            # old_head_sha is already validated as 40-hex; using -- separator
            # prevents git from misinterpreting the SHA as a flag.
            rev_range = f"{old_head_sha}..origin/{default_branch}"
        else:
            rev_range = f"origin/{default_branch}"

        args = [
            clone_dir, "log", rev_range,
            "--no-merges",
            "--pretty=format:%H\t%s\t%an\t%aI",
        ]
        if not old_head_sha:
            args.append(f"--since={cursor}")

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
                "sha": sha,
                "subject": subject,
                "author": author,
                "date": date,
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
            old_part = old_head_sha[:8] if old_head_sha else "?"
            title = f"Review push: {len(commits)} commits ({old_part}..{new_head_sha[:8]})"

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

        task_created = await self._tq.create_task(
            task_id=task_id,
            title=title,
            source="github-commits",
            source_ref=new_head_sha,
            repository=repo_name,
            pipeline="pr-review-pipeline",
            context=context,
        )
        return (1 if task_created else 0), new_head_sha

    async def _get_repo_git_flow_config(
        self, repo_name: str,
    ) -> GitFlowConfig | None:
        """Read git_flow_config from DB for a repository by name."""
        row = await self._db.fetch_one(
            "SELECT git_flow_config FROM repositories WHERE name = %(name)s",
            {"name": repo_name},
        )
        if not row or not row["git_flow_config"]:
            return None
        raw = row["git_flow_config"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        cfg = GitFlowConfig(**raw)
        if not cfg.enabled:
            return None
        return cfg

    async def _get_repo_clone_dir(self, repo_name: str) -> str | None:
        """Get the clone directory for a repository by name."""
        row = await self._db.fetch_one(
            "SELECT clone_dir FROM repositories WHERE name = %(name)s AND clone_status = 'ready'",
            {"name": repo_name},
        )
        return row["clone_dir"] if row else None

    async def _poll_merged_prs(
        self, repo_name: str, repo_slug: str, cursor: str,
    ) -> None:
        """Poll recently merged PRs and trigger back-merges for Git Flow repos.

        This method only acts on repositories with git_flow_config enabled.
        It tracks processed PR numbers in poll_state.state_data to ensure
        idempotency.
        """
        git_flow_cfg = await self._get_repo_git_flow_config(repo_name)
        if git_flow_cfg is None:
            return

        clone_dir = await self._get_repo_clone_dir(repo_name)
        if not clone_dir or not Path(clone_dir).exists():
            return

        # Fetch recently merged PRs
        merged_prs = await _gh_list_merged_prs(repo_slug)
        if not merged_prs:
            return

        # Load already-processed PR numbers from poll_state
        state_row = await self._db.fetch_one(
            "SELECT state_data FROM poll_state WHERE poller_name = %(name)s",
            {"name": self.name},
        )
        state_data = state_row["state_data"] if state_row else {}
        if isinstance(state_data, str):
            state_data = json.loads(state_data)
        processed_key = f"back_merged_prs:{repo_name}"
        processed_prs: list[int] = state_data.get(processed_key, [])

        # Parse cursor to datetime for robust timestamp comparison
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except (ValueError, TypeError):
            cursor_dt = datetime.now(timezone.utc) - timedelta(hours=1)

        # Pre-compute active release branch once per poll cycle (not per PR)
        # to avoid O(N*M) git subprocess calls.
        active_release = await find_active_release_branch(
            clone_dir,
            git_flow_cfg.branches.stable,
            git_flow_cfg.branches.release,
        )

        for pr in merged_prs:
            pr_number = pr.get("number")
            if not pr_number or pr_number in processed_prs:
                continue

            merged_at = pr.get("mergedAt", "")
            if merged_at:
                try:
                    merged_dt = datetime.fromisoformat(merged_at)
                    if merged_dt <= cursor_dt:
                        continue
                except (ValueError, TypeError):
                    # If we can't parse the timestamp, process the PR anyway
                    log.warning(
                        "merged_pr_bad_timestamp",
                        pr=pr_number,
                        merged_at=merged_at,
                    )

            base_ref = pr.get("baseRefName", "")
            head_ref = pr.get("headRefName", "")

            # Skip system-created back-merge PRs to avoid infinite loops
            if head_ref.startswith("aquarco/back-merge/"):
                processed_prs.append(pr_number)
                continue

            # Determine back-merge target
            target = resolve_back_merge_target(
                git_flow_cfg, base_ref, active_release_branch=active_release,
            )

            if target:
                log.info(
                    "back_merge_triggered",
                    repo=repo_slug,
                    pr=pr_number,
                    source=base_ref,
                    target=target,
                )
                await perform_back_merge(
                    clone_dir, repo_slug, base_ref, target,
                    task_description=f"Back-merge after PR #{pr_number} merged into {base_ref}",
                )

            processed_prs.append(pr_number)

        # Persist the updated list of processed PRs (keep last 200 to avoid unbounded growth).
        # Use jsonb_set for targeted key update to avoid clobbering concurrent writes.
        processed_prs = processed_prs[-200:]
        await self._db.execute(
            """
            INSERT INTO poll_state (poller_name, state_data)
            VALUES (%(name)s, jsonb_build_object(%(key)s, %(prs)s::jsonb))
            ON CONFLICT (poller_name)
            DO UPDATE SET state_data = jsonb_set(
                COALESCE(poll_state.state_data, '{}'::jsonb),
                ARRAY[%(key)s],
                %(prs)s::jsonb
            )
            """,
            {
                "name": self.name,
                "key": processed_key,
                "prs": json.dumps(processed_prs),
            },
        )

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


def _is_github_auth_error(err_text: str) -> bool:
    """Return True if the gh CLI stderr indicates an authentication failure."""
    lower = err_text.lower()
    return any(kw in lower for kw in ("401", "403", "authentication", "unauthorized", "bad credentials", "not logged in", "token"))


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
        # Redact any embedded credentials (e.g., https://token@host/…) before
        # propagating stderr text into logs or error messages.
        err_text = stderr.decode("utf-8", errors="replace")
        err_text = re.sub(r"https?://[^@\s]+@", "https://<redacted>@", err_text)
        if _is_github_auth_error(err_text):
            raise GitHubAuthenticationError(f"GitHub authentication failed for {repo_slug}: {err_text[:200]}")
        raise RuntimeError(f"gh pr list failed: {err_text}")
    prs: list[dict[str, Any]] = json.loads(stdout.decode())
    return prs


async def _gh_list_merged_prs(
    repo_slug: str, timeout: int = 60,
) -> list[dict[str, Any]]:
    """Call gh pr list --state merged and return parsed JSON.

    Returns recently merged PRs (last 50) for back-merge processing.
    """
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "list",
        "--repo", repo_slug,
        "--state", "merged",
        "--json",
        "number,title,headRefName,baseRefName,mergedAt",
        "--limit", "50",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(
            f"gh pr list (merged) timed out after {timeout}s for {repo_slug}"
        )
    if proc.returncode != 0:
        err_text = stderr.decode("utf-8", errors="replace")
        err_text = re.sub(r"https?://[^@\s]+@", "https://<redacted>@", err_text)
        if _is_github_auth_error(err_text):
            raise GitHubAuthenticationError(f"GitHub authentication failed for {repo_slug}: {err_text[:200]}")
        raise RuntimeError(f"gh pr list (merged) failed: {err_text}")
    prs: list[dict[str, Any]] = json.loads(stdout.decode())
    return prs


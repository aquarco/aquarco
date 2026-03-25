"""Git pull worker - keeps ready repositories up to date."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from ..database import Database
from ..logging import get_logger
from ..utils import run_git as _run_git

log = get_logger("pull-worker")

_FETCH_TIMEOUT = 30  # seconds


async def _fetch_with_timeout(clone_dir: str, branch: str) -> None:
    """Run git fetch with a hard timeout, killing the process if it hangs."""
    proc = await asyncio.create_subprocess_exec(
        "git", "-C", clone_dir, "fetch", "origin", branch, "--quiet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_FETCH_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"git fetch timed out after {_FETCH_TIMEOUT}s")
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git fetch failed ({proc.returncode}): {err}")


# Branch names must be safe git ref components: no spaces, no shell metacharacters,
# no leading dashes (which git would interpret as flags).
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]*$")


class PullWorker:
    """Pulls latest changes for repositories with clone_status='ready'."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def pull_ready_repos(self) -> None:
        """Fetch and reset all ready repositories."""
        rows = await self._db.fetch_all(
            """
            SELECT name, clone_dir, branch FROM repositories
            WHERE clone_status = 'ready'
            ORDER BY last_pulled_at ASC NULLS FIRST
            """
        )

        for row in rows:
            name = row["name"]
            clone_dir = row["clone_dir"]
            branch = row["branch"]

            if not Path(clone_dir, ".git").exists():
                continue

            # Reject branch names that could inject git flags or shell metacharacters.
            if not _SAFE_BRANCH_RE.match(branch):
                log.error(
                    "pull_skipped_invalid_branch",
                    name=name,
                    branch=branch,
                )
                continue

            active_count = await self._db.fetch_val(
                """
                SELECT COUNT(*) FROM tasks
                WHERE repository = %(name)s
                  AND status IN ('queued', 'executing')
                """,
                {"name": name},
            )
            if active_count:
                log.warning(
                    "pull_skipped_active_pipeline",
                    name=name,
                    active_tasks=active_count,
                )
                continue

            try:
                old_sha = await _run_git(clone_dir, "rev-parse", "HEAD")
                await _fetch_with_timeout(clone_dir, branch)
                await _run_git(clone_dir, "reset", "--hard", "--quiet", f"origin/{branch}")
                new_sha = await _run_git(clone_dir, "rev-parse", "HEAD")

                await self._db.execute(
                    """
                    UPDATE repositories
                    SET last_pulled_at = NOW(), head_sha = %(sha)s
                    WHERE name = %(name)s
                    """,
                    {"name": name, "sha": new_sha},
                )

                if old_sha != new_sha:
                    log.info("repo_updated", name=name, old=old_sha[:12], new=new_sha[:12])

            except Exception as e:
                log.warning("pull_failed", name=name, error=str(e))

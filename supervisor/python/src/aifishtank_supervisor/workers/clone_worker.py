"""Git clone worker - clones pending repositories."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from pathlib import Path

from ..database import Database
from ..exceptions import CloneError
from ..logging import get_logger
from ..utils import run_git as _run_git

log = get_logger("clone-worker")


class CloneWorker:
    """Clones repositories that are in pending status."""

    def __init__(self, db: Database, github_token: str | None = None) -> None:
        self._db = db
        self._github_token = github_token

    async def clone_pending_repos(self) -> None:
        """Claim and clone one pending repository."""
        row = await self._db.fetch_one(
            """
            UPDATE repositories SET clone_status = 'cloning'
            WHERE name = (
                SELECT name FROM repositories
                WHERE clone_status = 'pending'
                ORDER BY name LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            RETURNING name, url, branch, clone_dir
            """
        )
        if row is None:
            return

        name = row["name"]
        url = row["url"]
        branch = row["branch"]
        clone_dir = row["clone_dir"]

        log.info("cloning_repo", name=name, url=url)

        # Already cloned?
        if Path(clone_dir, ".git").exists():
            head_sha = await _run_git(clone_dir, "rev-parse", "HEAD")
            await self._mark_ready(name, head_sha)
            return

        try:
            clone_url = url
            ssh_command = self._get_ssh_command(url)
            env_extras = self._get_auth_env(url)
            await self._do_clone(clone_url, branch, clone_dir, ssh_command, env_extras)

            head_sha = await _run_git(clone_dir, "rev-parse", "HEAD")
            await self._mark_ready(name, head_sha)
            log.info("clone_success", name=name, sha=head_sha)

        except Exception as e:
            log.error("clone_failed", name=name, error=str(e))

            # Generate deploy key if needed
            deploy_key = await self._ensure_deploy_key(url)

            await self._db.execute(
                """
                UPDATE repositories
                SET clone_status = 'error',
                    error_message = %(error)s, deploy_public_key = %(key)s
                WHERE name = %(name)s
                """,
                {
                    "name": name,
                    "error": str(e),
                    "key": deploy_key,
                },
            )

    def _get_auth_env(self, url: str) -> dict[str, str]:
        """Get extra environment variables for git authentication.

        GitHub token auth is configured process-wide via GIT_ASKPASS and
        GITHUB_TOKEN env vars set in main.py. This method returns an empty
        dict for HTTPS URLs since the process env already handles auth.
        """
        return {}

    def _get_ssh_command(self, url: str) -> str | None:
        """Get SSH command with deploy key if available."""
        if not url.startswith("git@"):
            return None

        key_name = _url_to_key_name(url)
        key_path = Path.home() / ".ssh" / "deploy-keys" / key_name / "id_ed25519"
        if not key_path.exists():
            return None

        return (
            f"ssh -i {shlex.quote(str(key_path))} -o IdentitiesOnly=yes"
            " -o StrictHostKeyChecking=accept-new"
        )

    async def _do_clone(
        self,
        url: str,
        branch: str,
        clone_dir: str,
        ssh_command: str | None,
        env_extras: dict[str, str] | None = None,
    ) -> None:
        """Execute git clone."""
        Path(clone_dir).parent.mkdir(parents=True, exist_ok=True)

        env_args: dict[str, str] = dict(env_extras or {})
        if ssh_command:
            env_args["GIT_SSH_COMMAND"] = ssh_command

        cmd = ["git", "clone", "--branch", branch, "--single-branch", "--", url, clone_dir]

        env = {**os.environ, **env_args} if env_args else None
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise CloneError(f"git clone failed: {stderr.decode()}")

    async def _ensure_deploy_key(self, url: str) -> str | None:
        """Generate a deploy key if one doesn't exist. Returns public key."""
        key_name = _url_to_key_name(url)
        key_dir = Path.home() / ".ssh" / "deploy-keys" / key_name
        key_path = key_dir / "id_ed25519"
        pub_path = key_dir / "id_ed25519.pub"

        if pub_path.exists():
            return pub_path.read_text().strip()

        key_dir.mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            log.warning(
                "deploy_key_generation_failed",
                key_path=str(key_path),
                error=stderr.decode("utf-8", errors="replace").strip(),
            )
            return None

        if pub_path.exists():
            return pub_path.read_text().strip()
        return None

    async def _mark_ready(self, name: str, head_sha: str) -> None:
        """Mark repository as ready."""
        await self._db.execute(
            """
            UPDATE repositories
            SET clone_status = 'ready', last_cloned_at = NOW(), head_sha = %(sha)s
            WHERE name = %(name)s
            """,
            {"name": name, "sha": head_sha},
        )


def _url_to_key_name(url: str) -> str:
    """Derive filesystem-safe key name from URL."""
    name = url
    name = re.sub(r"^(https?://|git@)", "", name)
    name = re.sub(r"\.git$", "", name)
    name = re.sub(r"[/:]", "-", name)
    return name



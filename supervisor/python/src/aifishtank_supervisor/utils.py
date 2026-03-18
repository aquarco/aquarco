"""Shared utility functions."""

from __future__ import annotations

import asyncio
import re


async def run_cmd(
    *args: str, cwd: str | None = None, check: bool = True
) -> str:
    """Run a command and return stdout.  Raises on non-zero exit when check=True."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    if check and proc.returncode != 0:
        cmd_str = " ".join(args)
        err_text = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Command failed ({proc.returncode}): {cmd_str}\n{err_text}")
    return stdout.decode("utf-8", errors="replace").strip()


async def run_git(clone_dir: str, *args: str, check: bool = True) -> str:
    """Run a git command in the clone directory."""
    return await run_cmd("git", "-C", clone_dir, *args, check=check)


def url_to_slug(url: str) -> str | None:
    """Convert a GitHub repo URL (HTTPS or SSH) to owner/repo slug."""
    m = re.match(r"https?://[^/]+/([^/]+/[^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    m = re.match(r"git@[^:]+:([^/]+/[^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    return None

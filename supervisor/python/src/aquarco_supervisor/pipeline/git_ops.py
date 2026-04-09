"""Git helper functions for pipeline execution.

These are module-level async functions for git operations (checkout, commit,
push, ahead count) used by the pipeline executor and stage runner.
"""

from __future__ import annotations

from ..utils import run_git as _run_git


async def _git_checkout(clone_dir: str, branch: str) -> None:
    """Checkout a branch in the clone directory."""
    await _run_git(clone_dir, "checkout", branch)


async def _auto_commit(
    clone_dir: str, task_id: str, stage_num: int, category: str
) -> None:
    """Commit any uncommitted changes."""
    status = await _run_git(clone_dir, "status", "--porcelain")
    if not status.strip():
        return
    await _run_git(clone_dir, "add", "-A")
    await _run_git(
        clone_dir, "commit", "-m",
        f"chore(aquarco): {category} stage {stage_num} for {task_id}",
    )


async def _push_if_ahead(clone_dir: str, branch: str) -> None:
    """Push if local branch is ahead of remote."""
    ahead = await _get_ahead_count(clone_dir, branch)
    if ahead > 0:
        await _run_git(clone_dir, "push", "origin", branch)


async def _get_ahead_count(clone_dir: str, branch: str, base: str = "main") -> int:
    """Get number of commits ahead of the remote base branch."""
    result = await _run_git(
        clone_dir, "rev-list", "--count", f"origin/{base}..{branch}", check=False
    )
    try:
        return int(result) if result.strip() else 0
    except ValueError:
        return 0

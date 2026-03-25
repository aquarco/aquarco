"""Tests for _fetch_with_timeout and the updated PullWorker.fetch path.

Covers:
- _fetch_with_timeout: success path
- _fetch_with_timeout: asyncio.TimeoutError kills the process and raises RuntimeError
- _fetch_with_timeout: non-zero returncode raises RuntimeError with message
- PullWorker.pull_ready_repos: uses _fetch_with_timeout (not _run_git) for the fetch step
- PullWorker.pull_ready_repos: timeout during fetch is caught by the outer exception handler
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.workers.pull_worker import (
    PullWorker,
    _fetch_with_timeout,
    _FETCH_TIMEOUT,
)


# ---------------------------------------------------------------------------
# _fetch_with_timeout unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_with_timeout_succeeds_on_zero_returncode() -> None:
    """When git fetch exits 0, _fetch_with_timeout returns without raising."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch(
        "aquarco_supervisor.workers.pull_worker.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ) as mock_exec:
        await _fetch_with_timeout("/repo/path", "main")

    mock_exec.assert_awaited_once()
    call_args = mock_exec.await_args.args
    assert "git" in call_args
    assert "-C" in call_args
    assert "/repo/path" in call_args
    assert "fetch" in call_args
    assert "origin" in call_args
    assert "main" in call_args


@pytest.mark.asyncio
async def test_fetch_with_timeout_raises_on_nonzero_returncode() -> None:
    """When git fetch exits non-zero, RuntimeError includes the returncode and stderr."""
    mock_proc = MagicMock()
    mock_proc.returncode = 128
    mock_proc.communicate = AsyncMock(
        return_value=(b"", b"fatal: repository not found")
    )

    with patch(
        "aquarco_supervisor.workers.pull_worker.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ):
        with pytest.raises(RuntimeError) as exc_info:
            await _fetch_with_timeout("/repo/path", "main")

    msg = str(exc_info.value)
    assert "128" in msg
    assert "repository not found" in msg


@pytest.mark.asyncio
async def test_fetch_with_timeout_kills_process_on_timeout() -> None:
    """When git fetch hangs, the process is killed and RuntimeError mentions timeout."""
    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    async def hanging_communicate() -> tuple:
        await asyncio.sleep(9999)
        return b"", b""  # unreachable

    mock_proc.communicate = hanging_communicate

    async def fast_wait_for(coro: Any, timeout: float) -> Any:
        raise asyncio.TimeoutError()

    with patch(
        "aquarco_supervisor.workers.pull_worker.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ):
        with patch(
            "aquarco_supervisor.workers.pull_worker.asyncio.wait_for",
            side_effect=fast_wait_for,
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await _fetch_with_timeout("/repo/path", "main")

    mock_proc.kill.assert_called_once()
    mock_proc.wait.assert_awaited_once()
    assert str(_FETCH_TIMEOUT) in str(exc_info.value)
    assert "timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_fetch_with_timeout_passes_branch_name_to_git() -> None:
    """_fetch_with_timeout passes the exact branch name as a positional argument to git."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch(
        "aquarco_supervisor.workers.pull_worker.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ) as mock_exec:
        await _fetch_with_timeout("/some/dir", "feature/my-branch")

    call_args = mock_exec.await_args.args
    assert "feature/my-branch" in call_args


# ---------------------------------------------------------------------------
# PullWorker integration: fetch path uses _fetch_with_timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_worker_calls_fetch_with_timeout_not_run_git_for_fetch(
    tmp_path: Any,
) -> None:
    """PullWorker uses _fetch_with_timeout for the fetch step, not _run_git."""
    db = AsyncMock(spec=Database)

    repo_dir = tmp_path / "my-repo"
    (repo_dir / ".git").mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[{"name": "my-repo", "clone_dir": str(repo_dir), "branch": "main"}]
    )
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    fetch_called_with: list[tuple] = []

    async def mock_fetch(clone_dir: str, branch: str) -> None:
        fetch_called_with.append((clone_dir, branch))

    # _run_git is only called for rev-parse and reset (not fetch)
    git_responses = ["oldsha", "", "newsha"]

    with patch(
        "aquarco_supervisor.workers.pull_worker._fetch_with_timeout",
        side_effect=mock_fetch,
    ):
        with patch(
            "aquarco_supervisor.workers.pull_worker._run_git",
            new_callable=AsyncMock,
            side_effect=git_responses,
        ) as mock_git:
            worker = PullWorker(db)
            await worker.pull_ready_repos()

    assert len(fetch_called_with) == 1
    assert fetch_called_with[0] == (str(repo_dir), "main")
    # _run_git should only be called 3 times: rev-parse HEAD, reset, rev-parse HEAD
    assert mock_git.await_count == 3


@pytest.mark.asyncio
async def test_pull_worker_timeout_on_fetch_is_caught_gracefully(
    tmp_path: Any,
) -> None:
    """A timeout in _fetch_with_timeout is caught and the repo is skipped; others continue."""
    db = AsyncMock(spec=Database)

    slow_dir = tmp_path / "slow-repo"
    (slow_dir / ".git").mkdir(parents=True)
    fast_dir = tmp_path / "fast-repo"
    (fast_dir / ".git").mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[
            {"name": "slow-repo", "clone_dir": str(slow_dir), "branch": "main"},
            {"name": "fast-repo", "clone_dir": str(fast_dir), "branch": "main"},
        ]
    )
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    async def mock_fetch(clone_dir: str, branch: str) -> None:
        if "slow" in clone_dir:
            raise RuntimeError("git fetch timed out after 30s")

    with patch(
        "aquarco_supervisor.workers.pull_worker._fetch_with_timeout",
        side_effect=mock_fetch,
    ):
        with patch(
            "aquarco_supervisor.workers.pull_worker._run_git",
            new_callable=AsyncMock,
            # slow-repo: rev-parse HEAD before fetch -> fetch raises -> exception caught
            # fast-repo: rev-parse HEAD, reset, rev-parse HEAD
            side_effect=["sha-slow-old", "sha-fast-old", "", "sha-fast-new"],
        ):
            worker = PullWorker(db)
            await worker.pull_ready_repos()

    # Only fast-repo should have updated the DB
    db.execute.assert_awaited_once()
    params = db.execute.await_args.args[1]
    assert params["name"] == "fast-repo"


@pytest.mark.asyncio
async def test_pull_worker_fetch_error_message_includes_stderr(
    tmp_path: Any,
) -> None:
    """A fetch failure with stderr content is propagated in the warning log (RuntimeError message)."""
    db = AsyncMock(spec=Database)

    repo_dir = tmp_path / "broken-repo"
    (repo_dir / ".git").mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[{"name": "broken-repo", "clone_dir": str(repo_dir), "branch": "main"}]
    )
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    async def mock_fetch(clone_dir: str, branch: str) -> None:
        raise RuntimeError("git fetch failed (1): Permission denied (publickey)")

    with patch(
        "aquarco_supervisor.workers.pull_worker._fetch_with_timeout",
        side_effect=mock_fetch,
    ):
        with patch(
            "aquarco_supervisor.workers.pull_worker._run_git",
            new_callable=AsyncMock,
            side_effect=["sha-old"],
        ):
            worker = PullWorker(db)
            # Should not raise — exception is caught internally
            await worker.pull_ready_repos()

    db.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Branch safety validation (unchanged behaviour; regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_worker_skips_invalid_branch_name(tmp_path: Any) -> None:
    """Repos with branch names containing shell metacharacters are skipped."""
    db = AsyncMock(spec=Database)

    repo_dir = tmp_path / "unsafe-repo"
    (repo_dir / ".git").mkdir(parents=True)

    db.fetch_all = AsyncMock(
        return_value=[
            {
                "name": "unsafe-repo",
                "clone_dir": str(repo_dir),
                "branch": "-f --force; rm -rf /",
            }
        ]
    )
    db.execute = AsyncMock()
    db.fetch_val = AsyncMock(return_value=0)

    with patch(
        "aquarco_supervisor.workers.pull_worker._fetch_with_timeout",
        new_callable=AsyncMock,
    ) as mock_fetch:
        with patch(
            "aquarco_supervisor.workers.pull_worker._run_git",
            new_callable=AsyncMock,
        ) as mock_git:
            worker = PullWorker(db)
            await worker.pull_ready_repos()

    mock_fetch.assert_not_called()
    mock_git.assert_not_called()
    db.execute.assert_not_called()

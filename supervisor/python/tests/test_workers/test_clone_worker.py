"""Tests for clone worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aifishtank_supervisor.database import Database
from aifishtank_supervisor.exceptions import CloneError
from aifishtank_supervisor.workers.clone_worker import (
    CloneWorker,
    _url_to_key_name,
    _url_to_ssh,
)


def test_url_to_ssh_https() -> None:
    assert _url_to_ssh("https://github.com/owner/repo.git") == "git@github.com:owner/repo.git"
    assert _url_to_ssh("https://github.com/owner/repo") == "git@github.com:owner/repo"


def test_url_to_ssh_already_ssh() -> None:
    url = "git@github.com:owner/repo.git"
    assert _url_to_ssh(url) == url


def test_url_to_key_name() -> None:
    assert _url_to_key_name("git@github.com:owner/repo.git") == "github.com-owner-repo"
    assert _url_to_key_name("https://github.com/owner/repo.git") == "github.com-owner-repo"


def test_get_auth_env_with_token() -> None:
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db, github_token="ghp_test123")
    env = worker._get_auth_env("https://github.com/owner/repo.git")
    assert "GIT_CONFIG_VALUE_0" in env
    assert "ghp_test123" in env["GIT_CONFIG_VALUE_0"]


def test_get_auth_env_no_token() -> None:
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db, github_token=None)
    env = worker._get_auth_env("https://github.com/owner/repo.git")
    assert env == {}


def test_get_auth_env_ssh_url() -> None:
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db, github_token="ghp_test123")
    env = worker._get_auth_env("git@github.com:owner/repo.git")
    assert env == {}


def test_get_auth_env_contains_all_keys() -> None:
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db, github_token="my-token")
    env = worker._get_auth_env("https://github.com/org/repo.git")
    assert env["GIT_ASKPASS"] == "/bin/echo"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_CONFIG_COUNT" in env
    assert "GIT_CONFIG_KEY_0" in env


def test_get_ssh_command_no_key_returns_none(tmp_path: Any) -> None:
    """Returns None when no deploy key file exists."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    with patch.object(Path, "exists", return_value=False):
        result = worker._get_ssh_command("git@github.com:owner/repo.git")

    assert result is None


def test_get_ssh_command_https_returns_none() -> None:
    """Returns None for HTTPS URLs (no SSH key needed)."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)
    result = worker._get_ssh_command("https://github.com/owner/repo.git")
    assert result is None


def test_get_ssh_command_returns_ssh_string_when_key_exists(tmp_path: Any) -> None:
    """Returns ssh command string when the deploy key file exists."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    with patch.object(Path, "exists", return_value=True):
        result = worker._get_ssh_command("git@github.com:owner/repo.git")

    assert result is not None
    assert "ssh -i" in result
    assert "IdentitiesOnly=yes" in result


# --- clone_pending_repos ---

@pytest.mark.asyncio
async def test_clone_pending_repos_no_pending_rows() -> None:
    """Does nothing when there are no pending repositories."""
    db = AsyncMock(spec=Database)
    db.fetch_one = AsyncMock(return_value=None)

    worker = CloneWorker(db)
    await worker.clone_pending_repos()

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_clone_pending_repos_already_cloned(tmp_path: Any) -> None:
    """If .git dir already exists, mark repo as ready without cloning."""
    db = AsyncMock(spec=Database)
    db.fetch_one = AsyncMock(
        return_value={
            "name": "my-repo",
            "url": "https://github.com/org/repo.git",
            "branch": "main",
            "clone_dir": str(tmp_path / "repo"),
        }
    )
    db.execute = AsyncMock()

    # Create the .git directory
    (tmp_path / "repo" / ".git").mkdir(parents=True)

    worker = CloneWorker(db, github_token=None)

    with patch(
        "aifishtank_supervisor.workers.clone_worker._run_git",
        new_callable=AsyncMock,
        return_value="abc123",
    ):
        await worker.clone_pending_repos()

    db.execute.assert_awaited_once()
    call_sql = db.execute.await_args.args[0]
    assert "clone_status = 'ready'" in call_sql


@pytest.mark.asyncio
async def test_clone_pending_repos_clones_successfully(tmp_path: Any) -> None:
    """Successfully clones a repo and marks it ready."""
    db = AsyncMock(spec=Database)
    clone_dir = str(tmp_path / "new-repo")
    db.fetch_one = AsyncMock(
        return_value={
            "name": "new-repo",
            "url": "https://github.com/org/repo.git",
            "branch": "main",
            "clone_dir": clone_dir,
        }
    )
    db.execute = AsyncMock()

    worker = CloneWorker(db, github_token="ghp_token")

    with patch.object(worker, "_do_clone", new_callable=AsyncMock) as mock_clone, \
         patch(
             "aifishtank_supervisor.workers.clone_worker._run_git",
             new_callable=AsyncMock,
             return_value="deadbeef",
         ):
        await worker.clone_pending_repos()

    mock_clone.assert_awaited_once()
    db.execute.assert_awaited_once()
    call_sql = db.execute.await_args.args[0]
    assert "clone_status = 'ready'" in call_sql


@pytest.mark.asyncio
async def test_clone_pending_repos_clone_failure_sets_error(tmp_path: Any) -> None:
    """When _do_clone fails, repo is marked as error with the message."""
    db = AsyncMock(spec=Database)
    clone_dir = str(tmp_path / "fail-repo")
    db.fetch_one = AsyncMock(
        return_value={
            "name": "fail-repo",
            "url": "https://github.com/org/repo.git",
            "branch": "main",
            "clone_dir": clone_dir,
        }
    )
    db.execute = AsyncMock()

    worker = CloneWorker(db, github_token=None)

    with patch.object(
        worker, "_do_clone", new_callable=AsyncMock,
        side_effect=CloneError("authentication failed"),
    ), patch.object(
        worker, "_ensure_deploy_key", new_callable=AsyncMock, return_value="ssh-ed25519 AAAA..."
    ):
        await worker.clone_pending_repos()

    db.execute.assert_awaited_once()
    call_sql = db.execute.await_args.args[0]
    assert "clone_status = 'error'" in call_sql

    call_params = db.execute.await_args.args[1]
    assert "authentication failed" in call_params["error"]


# --- _do_clone ---

@pytest.mark.asyncio
async def test_do_clone_raises_clone_error_on_nonzero_exit(tmp_path: Any) -> None:
    """_do_clone raises CloneError when git exits with non-zero code."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 128
    mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: repo not found"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        with pytest.raises(CloneError, match="git clone failed"):
            await worker._do_clone(
                "https://github.com/org/nope.git",
                "main",
                str(tmp_path / "dest"),
                ssh_command=None,
            )


@pytest.mark.asyncio
async def test_do_clone_succeeds_on_zero_exit(tmp_path: Any) -> None:
    """_do_clone completes without error when git exits 0."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        # Should not raise
        await worker._do_clone(
            "https://github.com/org/repo.git",
            "main",
            str(tmp_path / "dest"),
            ssh_command=None,
        )


@pytest.mark.asyncio
async def test_do_clone_with_ssh_command_sets_env(tmp_path: Any) -> None:
    """When ssh_command is provided, it's added to the process environment."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    captured_kwargs: dict = {}

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_kwargs.update(kwargs)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await worker._do_clone(
            "git@github.com:org/repo.git",
            "main",
            str(tmp_path / "dest"),
            ssh_command='ssh -i "/key/path"',
        )

    env = captured_kwargs.get("env", {})
    assert "GIT_SSH_COMMAND" in env
    assert "/key/path" in env["GIT_SSH_COMMAND"]


# --- _mark_ready ---

@pytest.mark.asyncio
async def test_mark_ready_updates_db() -> None:
    db = AsyncMock(spec=Database)
    db.execute = AsyncMock()

    worker = CloneWorker(db)
    await worker._mark_ready("my-repo", "sha123")

    db.execute.assert_awaited_once()
    call_params = db.execute.await_args.args[1]
    assert call_params["name"] == "my-repo"
    assert call_params["sha"] == "sha123"


# --- _ensure_deploy_key ---


@pytest.mark.asyncio
async def test_ensure_deploy_key_existing(tmp_path: Any) -> None:
    """Returns existing public key without generating a new one."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    key_name = "github.com-owner-repo"
    key_dir = tmp_path / ".ssh" / "deploy-keys" / key_name
    key_dir.mkdir(parents=True)
    pub = key_dir / "id_ed25519.pub"
    pub.write_text("ssh-ed25519 AAAA existing-key")

    with patch.object(Path, "home", return_value=tmp_path):
        result = await worker._ensure_deploy_key("git@github.com:owner/repo.git")

    assert result == "ssh-ed25519 AAAA existing-key"


@pytest.mark.asyncio
async def test_ensure_deploy_key_generates_new(tmp_path: Any) -> None:
    """Generates a new key via ssh-keygen when none exists."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    key_name = "github.com-owner-repo"
    key_dir = tmp_path / ".ssh" / "deploy-keys" / key_name

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        # Simulate ssh-keygen creating the pub file
        key_dir.mkdir(parents=True, exist_ok=True)
        (key_dir / "id_ed25519.pub").write_text("ssh-ed25519 AAAA new-key")
        return mock_proc

    with patch.object(Path, "home", return_value=tmp_path), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        result = await worker._ensure_deploy_key("git@github.com:owner/repo.git")

    assert result == "ssh-ed25519 AAAA new-key"


@pytest.mark.asyncio
async def test_ensure_deploy_key_generation_fails(tmp_path: Any) -> None:
    """Returns None and logs warning when ssh-keygen fails."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"permission denied"))

    with patch.object(Path, "home", return_value=tmp_path), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await worker._ensure_deploy_key("git@github.com:owner/repo.git")

    assert result is None

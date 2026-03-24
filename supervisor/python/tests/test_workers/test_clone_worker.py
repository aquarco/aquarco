"""Tests for clone worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import CloneError
from aquarco_supervisor.workers.clone_worker import (
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


def test_url_to_ssh_non_matching_returns_unchanged() -> None:
    """URLs that don't match https?:// or git@ are returned unchanged."""
    url = "ssh://git@github.com/owner/repo.git"
    assert _url_to_ssh(url) == url


def test_url_to_ssh_http_non_https() -> None:
    """Plain http:// URLs are also converted to SSH format."""
    assert _url_to_ssh("http://github.com/owner/repo.git") == "git@github.com:owner/repo.git"


def test_url_to_key_name() -> None:
    assert _url_to_key_name("git@github.com:owner/repo.git") == "github.com-owner-repo"
    assert _url_to_key_name("https://github.com/owner/repo.git") == "github.com-owner-repo"


def test_url_to_key_name_ssh_scheme() -> None:
    """ssh:// URLs produce a filesystem-safe key name."""
    result = _url_to_key_name("ssh://git@github.com/owner/repo.git")
    assert "/" not in result
    assert ":" not in result


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


def test_get_auth_env_ssh_scheme_url() -> None:
    """ssh:// URLs should also skip token auth (review finding #2 fix)."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db, github_token="ghp_test123")
    env = worker._get_auth_env("ssh://git@github.com/owner/repo.git")
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


def test_get_ssh_command_ssh_scheme_url_with_key(tmp_path: Any) -> None:
    """ssh:// URLs should also look up deploy keys (review finding #2 fix)."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    with patch.object(Path, "exists", return_value=True):
        result = worker._get_ssh_command("ssh://git@github.com/owner/repo.git")

    assert result is not None
    assert "ssh -i" in result
    assert "IdentitiesOnly=yes" in result


def test_get_ssh_command_ssh_scheme_url_no_key() -> None:
    """ssh:// URLs without deploy keys return None."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    with patch.object(Path, "exists", return_value=False):
        result = worker._get_ssh_command("ssh://git@github.com/owner/repo.git")

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
        "aquarco_supervisor.workers.clone_worker._run_git",
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
             "aquarco_supervisor.workers.clone_worker._run_git",
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


@pytest.mark.asyncio
async def test_do_clone_with_env_extras_sets_auth_env(tmp_path: Any) -> None:
    """When env_extras is provided (e.g. token auth), vars are passed to git."""
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
            "https://github.com/org/repo.git",
            "main",
            str(tmp_path / "dest"),
            ssh_command=None,
            env_extras={
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "http.extraheader",
                "GIT_CONFIG_VALUE_0": "Authorization: Bearer ghp_test",
            },
        )

    env = captured_kwargs.get("env", {})
    assert env["GIT_CONFIG_VALUE_0"] == "Authorization: Bearer ghp_test"
    assert env["GIT_CONFIG_COUNT"] == "1"


@pytest.mark.asyncio
async def test_do_clone_with_ssh_command_and_env_extras(tmp_path: Any) -> None:
    """Both ssh_command and env_extras can coexist in the process environment."""
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
            env_extras={"CUSTOM_VAR": "hello"},
        )

    env = captured_kwargs.get("env", {})
    assert "GIT_SSH_COMMAND" in env
    assert env["CUSTOM_VAR"] == "hello"


@pytest.mark.asyncio
async def test_do_clone_no_branch_omits_branch_flags(tmp_path: Any) -> None:
    """When branch is None/empty, clone command should not include --branch."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await worker._do_clone(
            "https://github.com/org/repo.git",
            None,
            str(tmp_path / "dest"),
            ssh_command=None,
        )

    assert "--branch" not in captured_args
    assert "--single-branch" not in captured_args


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
async def test_clone_pending_repos_no_branch_detects_default(tmp_path: Any) -> None:
    """When branch is None/empty, detects actual default branch and updates DB."""
    db = AsyncMock(spec=Database)
    clone_dir = str(tmp_path / "unset-branch-repo")
    db.fetch_one = AsyncMock(
        return_value={
            "name": "unset-branch-repo",
            "url": "https://github.com/org/repo.git",
            "branch": None,
            "clone_dir": clone_dir,
        }
    )
    db.execute = AsyncMock()

    worker = CloneWorker(db, github_token="ghp_token")

    run_git_responses = iter(["deadbeef", "main"])

    async def fake_run_git(*args: Any, **kwargs: Any) -> str:
        return next(run_git_responses)

    with patch.object(worker, "_do_clone", new_callable=AsyncMock), \
         patch(
             "aquarco_supervisor.workers.clone_worker._run_git",
             side_effect=fake_run_git,
         ):
        await worker.clone_pending_repos()

    assert db.execute.await_count == 2
    # First call: branch update
    first_call_sql = db.execute.await_args_list[0].args[0]
    first_call_params = db.execute.await_args_list[0].args[1]
    assert "SET branch" in first_call_sql
    assert first_call_params["branch"] == "main"
    # Second call: mark ready
    second_call_sql = db.execute.await_args_list[1].args[0]
    assert "clone_status = 'ready'" in second_call_sql


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


@pytest.mark.asyncio
async def test_ensure_deploy_key_keygen_succeeds_but_no_pub_file(tmp_path: Any) -> None:
    """Returns None when ssh-keygen exits 0 but pub file is not created."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    # Don't create the pub file — simulates a keygen that succeeds but produces nothing
    with patch.object(Path, "home", return_value=tmp_path), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await worker._ensure_deploy_key("git@github.com:owner/repo.git")

    assert result is None


def test_url_to_ssh_ssh_scheme_passthrough() -> None:
    """ssh:// URLs are passed through unchanged (not git@ format)."""
    url = "ssh://git@github.com/owner/repo.git"
    assert _url_to_ssh(url) == url


def test_url_to_key_name_strips_git_suffix() -> None:
    """The .git suffix is removed from key names."""
    assert _url_to_key_name("https://github.com/org/my-project.git") == "github.com-org-my-project"
    assert _url_to_key_name("git@github.com:org/my-project.git") == "github.com-org-my-project"


def test_url_to_key_name_no_git_suffix() -> None:
    """URLs without .git suffix also work correctly."""
    result = _url_to_key_name("https://github.com/org/my-project")
    assert result == "github.com-org-my-project"


@pytest.mark.asyncio
async def test_do_clone_with_branch_includes_branch_flags(tmp_path: Any) -> None:
    """When branch is provided, clone command includes --branch and --single-branch."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    captured_args: list = []

    async def fake_exec(*args: Any, **kwargs: Any) -> Any:
        captured_args.extend(args)
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await worker._do_clone(
            "https://github.com/org/repo.git",
            "develop",
            str(tmp_path / "dest"),
            ssh_command=None,
        )

    assert "--branch" in captured_args
    assert "develop" in captured_args
    assert "--single-branch" in captured_args


@pytest.mark.asyncio
async def test_do_clone_creates_parent_directory(tmp_path: Any) -> None:
    """_do_clone creates the parent directory of clone_dir if it doesn't exist."""
    db = AsyncMock(spec=Database)
    worker = CloneWorker(db)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    nested_dir = str(tmp_path / "deep" / "nested" / "repo")

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        await worker._do_clone(
            "https://github.com/org/repo.git",
            "main",
            nested_dir,
            ssh_command=None,
        )

    # Parent directory should have been created
    assert Path(nested_dir).parent.exists()


@pytest.mark.asyncio
async def test_do_clone_no_env_when_no_extras_and_no_ssh(tmp_path: Any) -> None:
    """When neither ssh_command nor env_extras are provided, env should be None."""
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
            "https://github.com/org/repo.git",
            "main",
            str(tmp_path / "dest"),
            ssh_command=None,
            env_extras=None,
        )

    assert captured_kwargs.get("env") is None

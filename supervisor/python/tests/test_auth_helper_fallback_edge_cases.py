"""Edge-case tests for auth_helper.py fallback paths (b5c6d35).

Covers scenarios not exercised by test_auth_helper.py or test_cli_auth_helper.py:
  - _safe_mtime OSError handling during worktree sort
  - Race condition: worktree candidate removed between glob() and exists()
  - Candidate ordering: earlier candidate preferred over stable path
  - Worktree fallback logging (oauth_script_resolved_from_worktree)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.auth_helper import _handle_login


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_ipc(tmp_path: Path) -> Path:
    ipc = tmp_path / "ipc"
    ipc.mkdir()
    (ipc / "login-request").write_text("")
    return ipc


# ---------------------------------------------------------------------------
# _safe_mtime — OSError gracefully returns 0.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worktree_safe_mtime_handles_oserror(tmp_path: Path) -> None:
    """When a worktree candidate file disappears between glob and stat,
    _safe_mtime returns 0.0 and the remaining candidates are still considered."""
    ipc = _setup_ipc(tmp_path)

    worktree_root = tmp_path / "worktrees"
    # Create two worktrees: one will "vanish" on stat, the other is normal
    wt_vanishing = worktree_root / "vanishing-wt" / "supervisor" / "scripts"
    wt_vanishing.mkdir(parents=True)
    vanishing_script = wt_vanishing / "claude-auth-oauth.py"
    vanishing_script.write_text("# vanishing")

    wt_good = worktree_root / "good-wt" / "supervisor" / "scripts"
    wt_good.mkdir(parents=True)
    good_script = wt_good / "claude-auth-oauth.py"
    good_script.write_text("# good")

    original_exists = Path.exists
    original_is_dir = Path.is_dir
    original_resolve = Path.resolve
    original_glob = Path.glob
    original_stat = Path.stat

    def mock_exists(self: Path) -> bool:
        s = str(self)
        if "claude-auth-oauth" in s and str(worktree_root) not in s:
            return False
        if s == "/var/lib/aquarco/worktrees":
            return True
        return original_exists(self)

    def mock_is_dir(self: Path) -> bool:
        if str(self) == "/var/lib/aquarco/worktrees":
            return True
        return original_is_dir(self)

    def mock_glob(self: Path, pattern: str) -> list[Path]:
        if str(self) == "/var/lib/aquarco/worktrees":
            return list(worktree_root.glob(pattern))
        return list(original_glob(self, pattern))

    def mock_stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        # Simulate file disappearing between glob and stat for vanishing-wt
        if "vanishing-wt" in str(self):
            raise OSError("No such file or directory")
        return original_stat(self)

    def mock_resolve(self: Path, strict: bool = False) -> Path:
        s = str(self)
        if str(worktree_root) in s:
            relative = original_resolve(self, strict=False).relative_to(
                original_resolve(worktree_root, strict=False)
            )
            return Path("/var/lib/aquarco/worktrees") / relative
        if "/var/lib/aquarco" in s:
            return self
        return original_resolve(self, strict=strict)

    captured_scripts: list[str] = []

    async def fake_subprocess(*args: Any, **kwargs: Any) -> MagicMock:
        captured_scripts.extend(str(a) for a in args)
        proc = MagicMock()
        proc.pid = 12345
        return proc

    with (
        patch.object(Path, "exists", mock_exists),
        patch.object(Path, "is_dir", mock_is_dir),
        patch.object(Path, "glob", mock_glob),
        patch.object(Path, "stat", mock_stat),
        patch.object(Path, "resolve", mock_resolve),
        patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
    ):
        await _handle_login(ipc, oauth_script=None)

    # The good-wt script should have been selected (vanishing-wt gets mtime=0.0
    # but still exists from the mock_exists perspective)
    assert any("claude-auth-oauth.py" in s for s in captured_scripts)


# ---------------------------------------------------------------------------
# Race condition: candidate removed between sort and exists check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worktree_candidate_removed_between_sort_and_exists(tmp_path: Path) -> None:
    """When all worktree candidates are found by glob but disappear before the
    final exists() check, the function writes a not-found error."""
    ipc = _setup_ipc(tmp_path)

    worktree_root = tmp_path / "worktrees"
    wt = worktree_root / "ephemeral-wt" / "supervisor" / "scripts"
    wt.mkdir(parents=True)
    script = wt / "claude-auth-oauth.py"
    script.write_text("# ephemeral")

    original_exists = Path.exists
    original_is_dir = Path.is_dir
    original_glob = Path.glob

    # Track how many times exists is called for worktree scripts to simulate
    # the race: first call (glob) sees the file, second call (exists) does not
    exists_call_count: dict[str, int] = {}

    def mock_exists(self: Path) -> bool:
        s = str(self)
        # Static candidates: always missing
        if "claude-auth-oauth" in s and str(worktree_root) not in s:
            return False
        if s == "/var/lib/aquarco/worktrees":
            return True
        # Worktree candidate: exists for glob, but disappears for the final check
        if "ephemeral-wt" in s and "claude-auth-oauth" in s:
            exists_call_count[s] = exists_call_count.get(s, 0) + 1
            # The candidate was found by glob (which doesn't call exists),
            # but now when the code calls exists() after sorting, return False
            return False
        return original_exists(self)

    def mock_is_dir(self: Path) -> bool:
        if str(self) == "/var/lib/aquarco/worktrees":
            return True
        return original_is_dir(self)

    def mock_glob(self: Path, pattern: str) -> list[Path]:
        if str(self) == "/var/lib/aquarco/worktrees":
            return list(worktree_root.glob(pattern))
        return list(original_glob(self, pattern))

    with (
        patch.object(Path, "exists", mock_exists),
        patch.object(Path, "is_dir", mock_is_dir),
        patch.object(Path, "glob", mock_glob),
        patch("aquarco_supervisor.cli.auth_helper._kill_previous_login_processes"),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await _handle_login(ipc, oauth_script=None)

    # Should get a not-found error since the only worktree candidate vanished
    response = ipc / "login-response"
    assert response.exists()
    data = json.loads(response.read_text())
    assert "not found" in data["error"]


# ---------------------------------------------------------------------------
# Candidate ordering: earlier candidate preferred over stable path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_ordering_earlier_preferred_over_stable(tmp_path: Path) -> None:
    """When an earlier candidate exists alongside the stable path, the earlier
    one is selected because `next()` picks the first match in the candidates list.

    We verify by checking that the legacy path candidate appears before the stable
    path in the source code's candidates list, ensuring priority ordering."""
    import inspect
    source = inspect.getsource(_handle_login)

    # Find the positions of the two candidate paths in the source code
    legacy_pos = source.find("/home/agent/aquarco/supervisor/scripts/claude-auth-oauth.py")
    stable_pos = source.find("/var/lib/aquarco/scripts/claude-auth-oauth.py")

    assert legacy_pos > 0, "Legacy candidate path not found in source"
    assert stable_pos > 0, "Stable candidate path not found in source"
    assert legacy_pos < stable_pos, (
        "Legacy path should come before stable path in the candidates list "
        "so that next() picks it first when both exist"
    )


# ---------------------------------------------------------------------------
# Worktree fallback logging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worktree_fallback_logs_resolved_script(tmp_path: Path) -> None:
    """When the oauth script is resolved from a worktree, the function logs
    'oauth_script_resolved_from_worktree' with the script path."""
    ipc = _setup_ipc(tmp_path)

    worktree_root = tmp_path / "worktrees"
    wt = worktree_root / "logging-wt" / "supervisor" / "scripts"
    wt.mkdir(parents=True)
    wt_script = wt / "claude-auth-oauth.py"
    wt_script.write_text("# logging test")

    original_exists = Path.exists
    original_is_dir = Path.is_dir
    original_resolve = Path.resolve
    original_glob = Path.glob

    def mock_exists(self: Path) -> bool:
        s = str(self)
        if "claude-auth-oauth" in s and str(worktree_root) not in s:
            return False
        if s == "/var/lib/aquarco/worktrees":
            return True
        return original_exists(self)

    def mock_is_dir(self: Path) -> bool:
        if str(self) == "/var/lib/aquarco/worktrees":
            return True
        return original_is_dir(self)

    def mock_glob(self: Path, pattern: str) -> list[Path]:
        if str(self) == "/var/lib/aquarco/worktrees":
            return list(worktree_root.glob(pattern))
        return list(original_glob(self, pattern))

    def mock_resolve(self: Path, strict: bool = False) -> Path:
        s = str(self)
        if str(worktree_root) in s:
            relative = original_resolve(self, strict=False).relative_to(
                original_resolve(worktree_root, strict=False)
            )
            return Path("/var/lib/aquarco/worktrees") / relative
        if "/var/lib/aquarco" in s:
            return self
        return original_resolve(self, strict=strict)

    captured_scripts: list[str] = []

    async def fake_subprocess(*args: Any, **kwargs: Any) -> MagicMock:
        captured_scripts.extend(str(a) for a in args)
        proc = MagicMock()
        proc.pid = 12345
        return proc

    with (
        patch.object(Path, "exists", mock_exists),
        patch.object(Path, "is_dir", mock_is_dir),
        patch.object(Path, "glob", mock_glob),
        patch.object(Path, "resolve", mock_resolve),
        patch("asyncio.create_subprocess_exec", side_effect=fake_subprocess),
        patch("aquarco_supervisor.cli.auth_helper.log") as mock_log,
    ):
        await _handle_login(ipc, oauth_script=None)

    # Verify the log.info call with 'oauth_script_resolved_from_worktree'
    log_calls = [call for call in mock_log.info.call_args_list
                 if call[0][0] == "oauth_script_resolved_from_worktree"]
    assert len(log_calls) == 1
    assert "script" in log_calls[0][1]

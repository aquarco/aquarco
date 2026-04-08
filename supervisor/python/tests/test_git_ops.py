"""Tests for pipeline.git_ops — extracted git helper functions."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from aquarco_supervisor.pipeline.git_ops import (
    _auto_commit,
    _get_ahead_count,
    _git_checkout,
    _push_if_ahead,
)


@pytest.fixture
def mock_run_git():
    """Patch the module-level _run_git used by git_ops."""
    with patch("aquarco_supervisor.pipeline.git_ops._run_git", new_callable=AsyncMock) as m:
        yield m


# -----------------------------------------------------------------------
# _git_checkout
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_checkout_calls_run_git(mock_run_git):
    await _git_checkout("/repo", "feature-branch")
    mock_run_git.assert_awaited_once_with("/repo", "checkout", "feature-branch")


# -----------------------------------------------------------------------
# _auto_commit
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_commit_with_changes(mock_run_git):
    """When there are uncommitted changes, should add and commit."""
    mock_run_git.return_value = " M some-file.py\n"
    await _auto_commit("/repo", "task-42", 3, "implement")
    assert mock_run_git.await_count == 3
    calls = [c.args for c in mock_run_git.await_args_list]
    assert calls[0] == ("/repo", "status", "--porcelain")
    assert calls[1] == ("/repo", "add", "-A")
    assert calls[2][0] == "/repo"
    assert calls[2][1] == "commit"
    assert "implement stage 3" in calls[2][3]


@pytest.mark.asyncio
async def test_auto_commit_no_changes(mock_run_git):
    """When working tree is clean, should not add or commit."""
    mock_run_git.return_value = "   "
    await _auto_commit("/repo", "task-42", 3, "implement")
    mock_run_git.assert_awaited_once_with("/repo", "status", "--porcelain")


@pytest.mark.asyncio
async def test_auto_commit_empty_string(mock_run_git):
    """Empty string from status means no changes."""
    mock_run_git.return_value = ""
    await _auto_commit("/repo", "task-42", 0, "analyze")
    assert mock_run_git.await_count == 1


# -----------------------------------------------------------------------
# _get_ahead_count
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ahead_count_returns_int(mock_run_git):
    mock_run_git.return_value = "5"
    result = await _get_ahead_count("/repo", "my-branch")
    assert result == 5
    mock_run_git.assert_awaited_once_with(
        "/repo", "rev-list", "--count", "origin/main..my-branch", check=False
    )


@pytest.mark.asyncio
async def test_get_ahead_count_custom_base(mock_run_git):
    mock_run_git.return_value = "2"
    result = await _get_ahead_count("/repo", "feat", base="develop")
    assert result == 2
    mock_run_git.assert_awaited_once_with(
        "/repo", "rev-list", "--count", "origin/develop..feat", check=False
    )


@pytest.mark.asyncio
async def test_get_ahead_count_empty_output(mock_run_git):
    mock_run_git.return_value = ""
    result = await _get_ahead_count("/repo", "branch")
    assert result == 0


@pytest.mark.asyncio
async def test_get_ahead_count_whitespace(mock_run_git):
    mock_run_git.return_value = "   "
    result = await _get_ahead_count("/repo", "branch")
    assert result == 0


@pytest.mark.asyncio
async def test_get_ahead_count_non_numeric(mock_run_git):
    mock_run_git.return_value = "fatal: not a git repository"
    result = await _get_ahead_count("/repo", "branch")
    assert result == 0


# -----------------------------------------------------------------------
# _push_if_ahead
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_if_ahead_pushes_when_ahead(mock_run_git):
    # First call is rev-list (from _get_ahead_count), second is push
    mock_run_git.return_value = "3"
    await _push_if_ahead("/repo", "my-branch")
    assert mock_run_git.await_count == 2
    push_call = mock_run_git.await_args_list[1]
    assert push_call.args == ("/repo", "push", "origin", "my-branch")


@pytest.mark.asyncio
async def test_push_if_ahead_skips_when_zero(mock_run_git):
    mock_run_git.return_value = "0"
    await _push_if_ahead("/repo", "my-branch")
    # Only _get_ahead_count call, no push
    assert mock_run_git.await_count == 1


@pytest.mark.asyncio
async def test_push_if_ahead_skips_when_error(mock_run_git):
    mock_run_git.return_value = "not-a-number"
    await _push_if_ahead("/repo", "my-branch")
    assert mock_run_git.await_count == 1

"""Tests for the GitHub source poller's back-merge polling logic.

Covers:
- _poll_merged_prs: back-merge triggering for Git Flow repos
- Idempotency: processed PR tracking
- Skipping non-Git-Flow repos
- Skipping aquarco back-merge PRs (loop prevention)
- Active release branch pre-computation (once per poll cycle)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.models import GitFlowConfig
from aquarco_supervisor.pollers.github_source import GitHubSourcePoller
from aquarco_supervisor.task_queue import TaskQueue


def _make_poller(
    sample_config: Any,
    tmp_path: Path,
    *,
    git_flow_config: dict | None = None,
    processed_prs: list[int] | None = None,
) -> tuple[GitHubSourcePoller, AsyncMock, AsyncMock]:
    """Create a GitHubSourcePoller with mocked DB and task queue."""
    mock_tq = AsyncMock(spec=TaskQueue)
    mock_db = AsyncMock(spec=Database)

    poller = GitHubSourcePoller(sample_config, mock_tq, mock_db)

    # Use a real directory so Path.exists() passes
    clone_dir = str(tmp_path / "clone")
    Path(clone_dir).mkdir(parents=True, exist_ok=True)

    # Mock _get_repo_git_flow_config
    async def mock_get_git_flow_config(repo_name):
        if git_flow_config is None:
            return None
        cfg = GitFlowConfig(**git_flow_config)
        return cfg if cfg.enabled else None

    poller._get_repo_git_flow_config = AsyncMock(side_effect=mock_get_git_flow_config)
    poller._get_repo_clone_dir = AsyncMock(return_value=clone_dir)

    # Mock poll_state for processed PRs tracking
    state_data = {}
    if processed_prs is not None:
        state_data[f"back_merged_prs:test-repo"] = processed_prs
    mock_db.fetch_one = AsyncMock(return_value={"state_data": state_data})
    mock_db.execute = AsyncMock()

    return poller, mock_db, mock_tq


DEFAULT_GIT_FLOW = {
    "enabled": True,
    "branches": {
        "stable": "main",
        "development": "develop",
        "release": "release/*",
        "feature": "feature/*",
        "bugfix": "bugfix/*",
        "hotfix": "hotfix/*",
    },
}


@pytest.mark.asyncio
class TestPollMergedPrs:
    """Tests for _poll_merged_prs back-merge detection."""

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_skips_non_git_flow_repo(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should skip repos without git_flow_config."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=None)
        cursor = datetime.now(timezone.utc).isoformat()

        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_list_prs.assert_not_called()
        mock_backmerge.assert_not_called()

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_triggers_back_merge_for_release_branch_pr(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should trigger back-merge to develop when PR merged into release branch."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = "release/1.2"
        mock_backmerge.return_value = True

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 10,
                "title": "Fix payment bug",
                "headRefName": "bugfix/67-crash",
                "baseRefName": "release/1.2",
                "mergedAt": (now + timedelta(minutes=1)).isoformat(),
            },
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_backmerge.assert_called_once()
        call_args = mock_backmerge.call_args
        assert call_args.args[2] == "release/1.2"  # source
        assert call_args.args[3] == "develop"  # target

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_triggers_back_merge_for_main_branch_pr(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should trigger back-merge to active release when PR merged into main."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = "release/1.3"
        mock_backmerge.return_value = True

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 20,
                "title": "Release 1.2",
                "headRefName": "release/1.2",
                "baseRefName": "main",
                "mergedAt": (now + timedelta(minutes=1)).isoformat(),
            },
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_backmerge.assert_called_once()
        call_args = mock_backmerge.call_args
        assert call_args.args[2] == "main"  # source
        assert call_args.args[3] == "release/1.3"  # target (active release)

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_main_pr_back_merges_to_develop_when_no_release(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should back-merge to develop when no active release branch exists."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = None
        mock_backmerge.return_value = True

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 25,
                "title": "Hotfix",
                "headRefName": "hotfix/99-critical",
                "baseRefName": "main",
                "mergedAt": (now + timedelta(minutes=1)).isoformat(),
            },
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_backmerge.assert_called_once()
        call_args = mock_backmerge.call_args
        assert call_args.args[3] == "develop"  # target

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_skips_already_processed_prs(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should skip PRs that have already been processed."""
        poller, _, _ = _make_poller(
            sample_config, tmp_path,
            git_flow_config=DEFAULT_GIT_FLOW,
            processed_prs=[10],
        )
        mock_find.return_value = None

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 10,
                "title": "Already processed",
                "headRefName": "bugfix/10-old",
                "baseRefName": "release/1.0",
                "mergedAt": (now + timedelta(minutes=1)).isoformat(),
            },
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_backmerge.assert_not_called()

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_skips_back_merge_prs_to_prevent_loops(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should skip PRs from aquarco/back-merge/ branches to prevent infinite loops."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = None

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 30,
                "title": "chore: back-merge release/1.2 into develop",
                "headRefName": "aquarco/back-merge/release-1.2-to-develop",
                "baseRefName": "develop",
                "mergedAt": (now + timedelta(minutes=1)).isoformat(),
            },
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_backmerge.assert_not_called()

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_skips_prs_merged_before_cursor(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should skip PRs merged before the cursor timestamp."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = None

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 5,
                "title": "Old PR",
                "headRefName": "bugfix/5-old",
                "baseRefName": "release/1.0",
                "mergedAt": (now - timedelta(hours=2)).isoformat(),
            },
        ]

        cursor = (now - timedelta(hours=1)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_backmerge.assert_not_called()

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_no_back_merge_for_develop_target(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """PRs merged into develop should not trigger a back-merge."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = None

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 50,
                "title": "Feature PR",
                "headRefName": "feature/50-new-thing",
                "baseRefName": "develop",
                "mergedAt": (now + timedelta(minutes=1)).isoformat(),
            },
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_backmerge.assert_not_called()

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_persists_processed_prs(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should persist processed PR numbers to poll_state after processing."""
        poller, mock_db, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = None
        mock_backmerge.return_value = True

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 40,
                "title": "Test PR",
                "headRefName": "bugfix/40-test",
                "baseRefName": "release/1.0",
                "mergedAt": (now + timedelta(minutes=1)).isoformat(),
            },
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        # Verify DB execute was called to persist processed PRs
        mock_db.execute.assert_called_once()
        call_args = mock_db.execute.call_args
        params = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("params", {})
        assert "back_merged_prs:test-repo" in str(params)
        prs_json = params.get("prs", "")
        assert 40 in json.loads(prs_json)

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_active_release_computed_once_per_cycle(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Active release branch should be computed once, not per PR."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = "release/1.2"
        mock_backmerge.return_value = True

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 60,
                "title": "PR 1",
                "headRefName": "bugfix/60-one",
                "baseRefName": "release/1.2",
                "mergedAt": (now + timedelta(minutes=1)).isoformat(),
            },
            {
                "number": 61,
                "title": "PR 2",
                "headRefName": "bugfix/61-two",
                "baseRefName": "release/1.2",
                "mergedAt": (now + timedelta(minutes=2)).isoformat(),
            },
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_find.assert_called_once()
        assert mock_backmerge.call_count == 2

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_handles_bad_merged_at_timestamp(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should still process PR if mergedAt timestamp is unparseable."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_find.return_value = None
        mock_backmerge.return_value = True

        mock_list_prs.return_value = [
            {
                "number": 70,
                "title": "Bad timestamp PR",
                "headRefName": "bugfix/70-bad",
                "baseRefName": "release/1.0",
                "mergedAt": "not-a-date",
            },
        ]

        cursor = datetime.now(timezone.utc).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        # Should still try to process (back-merge) despite bad timestamp
        mock_backmerge.assert_called_once()

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_empty_merged_prs_list(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should handle empty merged PRs list gracefully."""
        poller, _, _ = _make_poller(sample_config, tmp_path, git_flow_config=DEFAULT_GIT_FLOW)
        mock_list_prs.return_value = []

        cursor = datetime.now(timezone.utc).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        mock_find.assert_not_called()
        mock_backmerge.assert_not_called()

    @patch("aquarco_supervisor.pollers.github_source._gh_list_merged_prs", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.perform_back_merge", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pollers.github_source.find_active_release_branch", new_callable=AsyncMock)
    async def test_processed_prs_capped_at_200(
        self, mock_find, mock_backmerge, mock_list_prs, sample_config, tmp_path,
    ):
        """Should cap processed PRs list at 200 entries."""
        existing_prs = list(range(1, 200))
        poller, mock_db, _ = _make_poller(
            sample_config, tmp_path,
            git_flow_config=DEFAULT_GIT_FLOW,
            processed_prs=existing_prs,
        )
        mock_find.return_value = None
        mock_backmerge.return_value = True

        now = datetime.now(timezone.utc)
        mock_list_prs.return_value = [
            {
                "number": 1000 + i,
                "title": f"PR {1000 + i}",
                "headRefName": f"bugfix/{1000 + i}-fix",
                "baseRefName": "release/1.0",
                "mergedAt": (now + timedelta(minutes=i + 1)).isoformat(),
            }
            for i in range(5)
        ]

        cursor = (now - timedelta(minutes=5)).isoformat()
        await poller._poll_merged_prs("test-repo", "org/repo", cursor)

        # Check persisted data: should be capped at 200
        mock_db.execute.assert_called_once()
        call_args = mock_db.execute.call_args
        params = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("params", {})
        prs_json = params.get("prs", "[]")
        persisted_prs = json.loads(prs_json)
        assert len(persisted_prs) <= 200

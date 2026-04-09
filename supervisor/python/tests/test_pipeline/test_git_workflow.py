"""Unit tests for the git_workflow module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.models import GitFlowBranches, GitFlowConfig
from aquarco_supervisor.pipeline.git_workflow import (
    BranchInfo,
    _extract_task_numeric_id,
    _parse_labels,
    _slugify,
    _validate_branch_name,
    find_active_release_branch,
    perform_back_merge,
    resolve_back_merge_target,
    resolve_branch_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert _slugify("Payment Gateway") == "payment-gateway"

    def test_special_chars(self):
        assert _slugify("Fix: crash on /admin page!") == "fix-crash-on-admin-page"

    def test_truncation(self):
        long = "a" * 100
        assert len(_slugify(long, max_len=50)) <= 50

    def test_strips_dashes(self):
        assert _slugify("--hello--world--") == "hello-world"


class TestExtractTaskNumericId:
    def test_github_issue(self):
        assert _extract_task_numeric_id("github-issue-aquarco-118") == "118"

    def test_plain_number(self):
        assert _extract_task_numeric_id("42") == "42"

    def test_no_number(self):
        assert _extract_task_numeric_id("task-abc") == "abc"

    def test_multiple_numbers(self):
        # Should pick the trailing number
        assert _extract_task_numeric_id("repo-123-issue-456") == "456"


class TestParseLabels:
    def test_feature(self):
        assert _parse_labels(["feature"]) == ("feature", None)

    def test_bugfix(self):
        assert _parse_labels(["bugfix"]) == ("bugfix", None)

    def test_hotfix_with_target(self):
        assert _parse_labels(["hotfix", "target: main"]) == ("hotfix", "main")

    def test_target_override(self):
        assert _parse_labels(["feature", "target: release/1.2"]) == (
            "feature",
            "release/1.2",
        )

    def test_no_labels(self):
        assert _parse_labels([]) == (None, None)

    def test_unrelated_labels(self):
        assert _parse_labels(["enhancement", "agent-task"]) == (None, None)

    def test_case_insensitive(self):
        assert _parse_labels(["Feature"]) == ("feature", None)

    def test_target_with_spaces(self):
        assert _parse_labels(["target:  develop "]) == (None, "develop")


class TestValidateBranchName:
    def test_valid(self):
        assert _validate_branch_name("feature/42-payment") == "feature/42-payment"

    def test_valid_with_dots(self):
        assert _validate_branch_name("release/1.2.3") == "release/1.2.3"

    def test_invalid_starts_with_dash(self):
        with pytest.raises(ValueError):
            _validate_branch_name("-bad")

    def test_invalid_shell_chars(self):
        with pytest.raises(ValueError):
            _validate_branch_name("branch;rm -rf /")


# ---------------------------------------------------------------------------
# resolve_branch_info
# ---------------------------------------------------------------------------


class TestResolveBranchInfo:
    """Tests for resolve_branch_info in various label scenarios."""

    def _cfg(self) -> GitFlowConfig:
        return GitFlowConfig(enabled=True, branches=GitFlowBranches())

    def test_feature_default(self):
        result = resolve_branch_info(
            self._cfg(), "github-issue-aquarco-42", "Payment Gateway", ["feature"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_name == "feature/42-payment-gateway"
        assert result.base_branch == "develop"
        assert result.branch_type == "feature"

    def test_feature_no_label_defaults_to_feature(self):
        """When no branch type label is present, default to feature."""
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-99",
            "Some task",
            ["enhancement"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "feature"
        assert result.base_branch == "develop"

    def test_bugfix_with_active_release(self):
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-67",
            "Crash on payment",
            ["bugfix"],
            active_release_branch="release/1.2",
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_name == "bugfix/67-crash-on-payment"
        assert result.base_branch == "release/1.2"
        assert result.branch_type == "bugfix"

    def test_bugfix_without_active_release(self):
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-67",
            "Crash on payment",
            ["bugfix"],
            active_release_branch=None,
        )
        assert isinstance(result, BranchInfo)
        assert result.base_branch == "develop"

    def test_hotfix_with_target(self):
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-89",
            "Critical bug",
            ["hotfix", "target: main"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_name == "hotfix/89-critical-bug"
        assert result.base_branch == "main"
        assert result.branch_type == "hotfix"

    def test_hotfix_without_target_returns_error(self):
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-89",
            "Critical bug",
            ["hotfix"],
        )
        assert isinstance(result, str)
        assert "target:" in result.lower()

    def test_target_override_on_feature(self):
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-14",
            "Direct to release",
            ["feature", "target: release/1.2"],
        )
        assert isinstance(result, BranchInfo)
        assert result.base_branch == "release/1.2"
        assert result.branch_type == "feature"

    def test_custom_branch_patterns(self):
        cfg = GitFlowConfig(
            enabled=True,
            branches=GitFlowBranches(
                feature="feat/*",
                development="dev",
            ),
        )
        result = resolve_branch_info(
            cfg, "github-issue-aquarco-10", "New widget", ["feature"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_name == "feat/10-new-widget"
        assert result.base_branch == "dev"


# ---------------------------------------------------------------------------
# resolve_back_merge_target
# ---------------------------------------------------------------------------


class TestResolveBackMergeTarget:
    def _cfg(self) -> GitFlowConfig:
        return GitFlowConfig(enabled=True, branches=GitFlowBranches())

    def test_release_to_develop(self):
        target = resolve_back_merge_target(self._cfg(), "release/1.2")
        assert target == "develop"

    def test_main_to_active_release(self):
        target = resolve_back_merge_target(
            self._cfg(), "main", active_release_branch="release/1.3",
        )
        assert target == "release/1.3"

    def test_main_to_develop_no_release(self):
        target = resolve_back_merge_target(self._cfg(), "main")
        assert target == "develop"

    def test_develop_no_back_merge(self):
        target = resolve_back_merge_target(self._cfg(), "develop")
        assert target is None

    def test_feature_no_back_merge(self):
        target = resolve_back_merge_target(self._cfg(), "feature/42-foo")
        assert target is None


# ---------------------------------------------------------------------------
# find_active_release_branch (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFindActiveReleaseBranch:
    """Async tests for find_active_release_branch with mocked git."""

    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_returns_newest_active_release(self, mock_git):
        """Should return the newest release branch with unmerged commits."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "fetch":
                return ""
            if cmd == "for-each-ref":
                return "origin/release/1.1\norigin/release/1.2\norigin/release/1.3\n"
            if cmd == "rev-list":
                # release/1.1 fully merged, 1.2 and 1.3 have unmerged commits
                ref = args[2] if len(args) > 2 else ""
                if "release/1.1" in ref:
                    return "0"
                return "3"
            return ""

        mock_git.side_effect = git_side_effect
        result = await find_active_release_branch("/repo", "main", "release/*")
        assert result == "release/1.3"

    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_returns_none_when_all_merged(self, mock_git):
        """Should return None when all release branches are fully merged."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "for-each-ref":
                return "origin/release/1.0\n"
            if cmd == "rev-list":
                return "0"
            return ""

        mock_git.side_effect = git_side_effect
        result = await find_active_release_branch("/repo", "main", "release/*")
        assert result is None

    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_returns_none_when_no_release_branches(self, mock_git):
        """Should return None when no release branches exist."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "for-each-ref":
                return ""
            return ""

        mock_git.side_effect = git_side_effect
        result = await find_active_release_branch("/repo", "main", "release/*")
        assert result is None

    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_logs_warning_on_fetch_failure(self, mock_git):
        """Should log warning but still work when fetch fails."""
        call_count = 0

        async def git_side_effect(clone_dir, *args, **kwargs):
            nonlocal call_count
            cmd = args[0] if args else ""
            if cmd == "fetch":
                raise RuntimeError("network error")
            if cmd == "for-each-ref":
                return "origin/release/2.0\n"
            if cmd == "rev-list":
                return "5"
            return ""

        mock_git.side_effect = git_side_effect
        result = await find_active_release_branch("/repo", "main", "release/*")
        # Should still return a result despite fetch failure
        assert result == "release/2.0"


# ---------------------------------------------------------------------------
# perform_back_merge (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerformBackMerge:
    """Async tests for perform_back_merge with mocked git and gh."""

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_successful_merge_pushes(self, mock_git, mock_cmd):
        """Should push merge result on successful merge."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                # Return different HEADs to simulate merge advancing HEAD
                if not hasattr(git_side_effect, "_head_called"):
                    git_side_effect._head_called = True
                    return "aaa111"
                return "bbb222"
            if cmd == "status":
                return ""  # Clean working tree
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is True
        # Should have called push
        push_calls = [
            c for c in mock_git.call_args_list
            if len(c.args) > 1 and c.args[1] == "push"
        ]
        assert len(push_calls) == 1

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_conflict_creates_pr(self, mock_git, mock_cmd):
        """Should create a conflict PR when merge has conflicts."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "UU conflicted-file.txt\n"
            return ""

        mock_git.side_effect = git_side_effect
        mock_cmd.return_value = "https://github.com/org/repo/pull/99"

        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is True
        # Should have called gh pr create
        assert mock_cmd.called
        pr_create_calls = [
            c for c in mock_cmd.call_args_list
            if "pr" in c.args and "create" in c.args
        ]
        assert len(pr_create_calls) == 1

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_dirty_state_without_conflicts_returns_false(self, mock_git, mock_cmd):
        """Should return False when merge leaves dirty state but no conflict markers."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "M  some-file.txt\n"  # Dirty but not UU/AA/DD
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is False
        # Should NOT have pushed
        push_calls = [
            c for c in mock_git.call_args_list
            if len(c.args) > 1 and c.args[1] == "push"
        ]
        assert len(push_calls) == 0

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_already_up_to_date_returns_true(self, mock_git, mock_cmd):
        """Should return True without pushing when already up to date."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "same_sha_111"  # HEAD unchanged = already up to date
            if cmd == "status":
                return ""  # Clean
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is True
        # Should NOT have pushed (nothing to push)
        push_calls = [
            c for c in mock_git.call_args_list
            if len(c.args) > 1 and c.args[1] == "push"
        ]
        assert len(push_calls) == 0

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_truncates_long_task_description(self, mock_git, mock_cmd):
        """Should truncate task_description to prevent excessively long gh CLI args."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "UU file.txt\n"  # Trigger conflict path to inspect body
            return ""

        mock_git.side_effect = git_side_effect
        mock_cmd.return_value = ""

        long_desc = "x" * 1000
        await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
            task_description=long_desc,
        )

        # Find the --body arg in the gh pr create call
        for call in mock_cmd.call_args_list:
            args = call.args
            if "pr" in args and "create" in args:
                body_idx = list(args).index("--body") + 1
                body = args[body_idx]
                # Description should be truncated (500 chars + ellipsis)
                assert len(long_desc) > 500
                assert "x" * 500 in body
                assert "…" in body
                break

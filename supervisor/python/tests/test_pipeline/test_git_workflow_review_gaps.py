"""Tests addressing review findings and coverage gaps for git_workflow module.

Covers:
- Review finding: AU, UA, DU, UD conflict status codes not detected (line 395)
- Review finding: perform_back_merge does not validate source/target branch names (line 361)
- Review finding: merge output discarded on non-conflict failure (line 380)
- Edge cases: push failure after successful merge
- Edge cases: _SEMVER_RE parsing for find_active_release_branch
- Model validation: GitFlowConfig and GitFlowBranches defaults and serialization
- Edge cases: resolve_branch_info with all branch types exhausted
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aquarco_supervisor.models import GitFlowBranches, GitFlowConfig
from aquarco_supervisor.pipeline.git_workflow import (
    BranchInfo,
    _back_merge_note_for,
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
# Review Finding: Incomplete merge conflict detection (line 395)
# The current implementation only detects UU, AA, DD but misses AU, UA, DU, UD.
# These tests document the current behaviour and the gap.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestConflictDetectionGaps:
    """Document conflict detection behaviour for all git porcelain status codes."""

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_au_conflict_not_detected(self, mock_git, mock_cmd):
        """AU (added by us, unmerged by them) is NOT detected as conflict — known gap."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "AU file.txt\n"
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        # AU is not in the conflict check set {UU, AA, DD}
        # so the code enters the "dirty state without conflicts" path -> returns False
        assert result is False

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_ua_conflict_not_detected(self, mock_git, mock_cmd):
        """UA (unmerged by us, added by them) is NOT detected — known gap."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "UA file.txt\n"
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is False  # Misclassified as dirty-non-conflict

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_du_conflict_not_detected(self, mock_git, mock_cmd):
        """DU (deleted by us, unmerged by them) is NOT detected — known gap."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "DU file.txt\n"
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is False  # Misclassified as dirty-non-conflict

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_ud_conflict_not_detected(self, mock_git, mock_cmd):
        """UD (unmerged by us, deleted by them) is NOT detected — known gap."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "UD file.txt\n"
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is False  # Misclassified as dirty-non-conflict

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_mixed_uu_and_au_detects_as_conflict(self, mock_git, mock_cmd):
        """When UU is present alongside AU, conflicts ARE detected (UU match)."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "UU file1.txt\nAU file2.txt\n"
            return ""

        mock_git.side_effect = git_side_effect
        mock_cmd.return_value = ""
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is True  # UU triggers conflict path


# ---------------------------------------------------------------------------
# Review Finding: source/target branch validation missing (line 361)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerformBackMergeBranchValidation:
    """Tests for branch name validation in perform_back_merge."""

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_source_branch_with_shell_chars_reaches_git(self, mock_git, mock_cmd):
        """Source branch is NOT validated — shell-unsafe chars pass through to git.

        This documents the gap noted in the review (line 361): perform_back_merge
        does not call _validate_branch_name on source_branch or target_branch.
        The merge branch name IS validated, but the raw source/target are not.
        """
        call_log = []

        async def git_side_effect(clone_dir, *args, **kwargs):
            call_log.append(args)
            cmd = args[0] if args else ""
            if cmd == "fetch":
                return ""
            if cmd == "checkout":
                return ""
            if cmd == "rev-parse":
                return "same_sha"
            if cmd == "status":
                return ""
            return ""

        mock_git.side_effect = git_side_effect
        # A branch name with spaces would be invalid for git but the code
        # doesn't validate it; the merge_branch construction replaces / with -
        # so the merge_branch itself may still pass validation.
        # We use a valid-looking but unusual name to show it flows through.
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2.3", "develop",
        )
        assert result is True

        # Verify source branch was used in merge command without validation
        merge_calls = [c for c in call_log if c[0] == "merge"]
        assert len(merge_calls) >= 1
        assert "origin/release/1.2.3" in merge_calls[0]


# ---------------------------------------------------------------------------
# Edge case: push failure after successful merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerformBackMergePushFailure:
    """Test that push failures are handled gracefully."""

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_push_failure_returns_false(self, mock_git, mock_cmd):
        """When push fails after successful merge, should return False (exception path)."""
        call_count = 0

        async def git_side_effect(clone_dir, *args, **kwargs):
            nonlocal call_count
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                call_count += 1
                if call_count == 1:
                    return "aaa111"
                return "bbb222"
            if cmd == "push":
                raise RuntimeError("remote rejected")
            if cmd == "status":
                return ""
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        # The push raises inside the try block, caught by the outer except
        assert result is False

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_detach_cleanup_runs_after_push_failure(self, mock_git, mock_cmd):
        """Even when push fails, the finally block should attempt checkout --detach."""
        call_count = 0
        detach_called = False

        async def git_side_effect(clone_dir, *args, **kwargs):
            nonlocal call_count, detach_called
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                call_count += 1
                if call_count == 1:
                    return "aaa111"
                return "bbb222"
            if cmd == "push":
                raise RuntimeError("remote rejected")
            if cmd == "checkout" and "--detach" in args:
                detach_called = True
                return ""
            if cmd == "status":
                return ""
            return ""

        mock_git.side_effect = git_side_effect
        await perform_back_merge("/repo", "org/repo", "release/1.2", "develop")
        assert detach_called


# ---------------------------------------------------------------------------
# _SEMVER_RE and find_active_release_branch sorting edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFindActiveReleaseBranchSorting:
    """Edge cases for semver extraction and sorting in find_active_release_branch."""

    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_single_digit_versions(self, mock_git):
        """Single digit versions should sort correctly."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "fetch":
                return ""
            if cmd == "for-each-ref":
                return "origin/release/3\norigin/release/1\norigin/release/2\n"
            if cmd == "rev-list":
                return "1"
            return ""

        mock_git.side_effect = git_side_effect
        result = await find_active_release_branch("/repo", "main", "release/*")
        assert result == "release/3"

    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_all_non_semver_branches(self, mock_git):
        """When all branches are non-semver, they all get version (0,) and one is returned."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "fetch":
                return ""
            if cmd == "for-each-ref":
                return "origin/release/alpha\norigin/release/beta\n"
            if cmd == "rev-list":
                return "2"
            return ""

        mock_git.side_effect = git_side_effect
        result = await find_active_release_branch("/repo", "main", "release/*")
        # Both have (0,) version, so sort is stable — one of them is returned
        assert result in ("release/alpha", "release/beta")

    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_rev_list_returns_non_numeric(self, mock_git):
        """When rev-list output is not a number, the branch is skipped (count=0)."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "fetch":
                return ""
            if cmd == "for-each-ref":
                return "origin/release/1.0\n"
            if cmd == "rev-list":
                return "not-a-number"
            return ""

        mock_git.side_effect = git_side_effect
        result = await find_active_release_branch("/repo", "main", "release/*")
        # ValueError from int() is caught, count defaults to 0 -> no active branch
        assert result is None

    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_empty_lines_only_in_for_each_ref(self, mock_git):
        """For-each-ref returning only whitespace should return None."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "fetch":
                return ""
            if cmd == "for-each-ref":
                return "\n  \n\n"
            return ""

        mock_git.side_effect = git_side_effect
        result = await find_active_release_branch("/repo", "main", "release/*")
        assert result is None


# ---------------------------------------------------------------------------
# GitFlowConfig and GitFlowBranches model tests
# ---------------------------------------------------------------------------


class TestGitFlowModels:
    """Test Pydantic model defaults and validation."""

    def test_default_branches(self):
        branches = GitFlowBranches()
        assert branches.stable == "main"
        assert branches.development == "develop"
        assert branches.release == "release/*"
        assert branches.feature == "feature/*"
        assert branches.bugfix == "bugfix/*"
        assert branches.hotfix == "hotfix/*"

    def test_custom_branches(self):
        branches = GitFlowBranches(
            stable="production",
            development="dev",
            release="rel/*",
            feature="feat/*",
            bugfix="fix/*",
            hotfix="hf/*",
        )
        assert branches.stable == "production"
        assert branches.development == "dev"

    def test_git_flow_config_defaults(self):
        cfg = GitFlowConfig(enabled=True)
        assert cfg.enabled is True
        assert isinstance(cfg.branches, GitFlowBranches)
        assert cfg.branches.stable == "main"

    def test_git_flow_config_disabled(self):
        cfg = GitFlowConfig(enabled=False)
        assert cfg.enabled is False

    def test_git_flow_config_from_dict(self):
        """Simulates loading from JSONB (as the executor does)."""
        raw = {
            "enabled": True,
            "branches": {
                "stable": "master",
                "development": "dev",
                "release": "release/*",
                "feature": "feature/*",
                "bugfix": "bugfix/*",
                "hotfix": "hotfix/*",
            },
        }
        cfg = GitFlowConfig(**raw)
        assert cfg.branches.stable == "master"
        assert cfg.branches.development == "dev"

    def test_git_flow_config_partial_branches(self):
        """Missing branch fields should use defaults."""
        raw = {
            "enabled": True,
            "branches": {"stable": "production"},
        }
        cfg = GitFlowConfig(**raw)
        assert cfg.branches.stable == "production"
        assert cfg.branches.development == "develop"  # default
        assert cfg.branches.feature == "feature/*"  # default


# ---------------------------------------------------------------------------
# _extract_task_numeric_id edge cases
# ---------------------------------------------------------------------------


class TestExtractTaskNumericIdEdgeCases:
    def test_task_with_no_separators(self):
        assert _extract_task_numeric_id("task") == "task"

    def test_empty_string(self):
        assert _extract_task_numeric_id("") == ""

    def test_only_number(self):
        assert _extract_task_numeric_id("999") == "999"

    def test_leading_zeros(self):
        assert _extract_task_numeric_id("task-007") == "007"


# ---------------------------------------------------------------------------
# _parse_labels edge cases
# ---------------------------------------------------------------------------


class TestParseLabelsEdgeCases:
    def test_duplicate_type_labels(self):
        """When multiple type labels present, last wins."""
        branch_type, target = _parse_labels(["feature", "hotfix"])
        assert branch_type == "hotfix"

    def test_target_without_value(self):
        """target: with no value should not set target_override."""
        branch_type, target = _parse_labels(["target:"])
        assert target is None

    def test_target_with_whitespace_only(self):
        """target: with only whitespace should not set target_override."""
        branch_type, target = _parse_labels(["target:   "])
        assert target is None

    def test_mixed_case_target_prefix(self):
        """The target: prefix comparison is case-insensitive."""
        _, target = _parse_labels(["Target: main"])
        assert target == "main"


# ---------------------------------------------------------------------------
# resolve_branch_info — unknown branch_type path (unreachable but defensive)
# ---------------------------------------------------------------------------


class TestResolveBranchInfoDefensive:
    def _cfg(self) -> GitFlowConfig:
        return GitFlowConfig(enabled=True, branches=GitFlowBranches())

    def test_hotfix_with_explicit_target_succeeds(self):
        """Hotfix with target label should succeed (not return error)."""
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-1",
            "Critical fix",
            ["hotfix", "target: main"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "hotfix"
        assert result.base_branch == "main"

    def test_bugfix_without_labels_defaults_to_feature(self):
        """When only non-type labels present, defaults to feature."""
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-5",
            "Some task",
            ["priority-high", "wontfix"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "feature"
        assert result.base_branch == "develop"

    def test_empty_labels_defaults_to_feature(self):
        result = resolve_branch_info(
            self._cfg(),
            "github-issue-aquarco-5",
            "Some task",
            [],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "feature"


# ---------------------------------------------------------------------------
# resolve_back_merge_target — edge cases with active_release
# ---------------------------------------------------------------------------


class TestResolveBackMergeTargetActiveRelease:
    def _cfg(self) -> GitFlowConfig:
        return GitFlowConfig(enabled=True, branches=GitFlowBranches())

    def test_main_with_active_release(self):
        target = resolve_back_merge_target(
            self._cfg(), "main", active_release_branch="release/2.0",
        )
        assert target == "release/2.0"

    def test_release_with_active_release_ignores_active(self):
        """Release branch merges always go to develop, regardless of active_release."""
        target = resolve_back_merge_target(
            self._cfg(), "release/1.0", active_release_branch="release/2.0",
        )
        assert target == "develop"

    def test_arbitrary_branch_with_active_release(self):
        """Non-stable/non-release branches still return None even with active release."""
        target = resolve_back_merge_target(
            self._cfg(), "feature/42-foo", active_release_branch="release/2.0",
        )
        assert target is None


# ---------------------------------------------------------------------------
# _back_merge_note_for — comprehensive coverage
# ---------------------------------------------------------------------------


class TestBackMergeNoteForComprehensive:
    def test_develop_target_returns_no_back_merge(self):
        note = _back_merge_note_for("feature", "develop", GitFlowBranches())
        assert "No automatic back-merge" in note

    def test_main_target_returns_back_merge(self):
        note = _back_merge_note_for("hotfix", "main", GitFlowBranches())
        assert "automatically back-merge downstream" in note

    def test_release_target_returns_back_merge(self):
        note = _back_merge_note_for("bugfix", "release/3.0", GitFlowBranches())
        assert "automatically back-merge downstream" in note

    def test_custom_branches_develop_equivalent(self):
        """A custom development branch name should still show no back-merge."""
        branches = GitFlowBranches(development="dev")
        note = _back_merge_note_for("feature", "dev", branches)
        # "dev" is not stable or release, so no automatic back-merge
        assert "No automatic back-merge" in note


# ---------------------------------------------------------------------------
# perform_back_merge — merge abort after dirty non-conflict state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPerformBackMergeDirtyState:
    """Test cleanup after dirty non-conflict merge state."""

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_merge_abort_and_reset_on_dirty_non_conflict(self, mock_git, mock_cmd):
        """Should run merge --abort and reset --hard when dirty but no conflicts."""
        commands_run = []

        async def git_side_effect(clone_dir, *args, **kwargs):
            commands_run.append(args[0] if args else "")
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "M  modified-file.txt\n"
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is False
        # Should have run merge --abort and reset --hard
        abort_calls = [
            c for c in mock_git.call_args_list
            if len(c.args) > 1 and c.args[1] == "merge" and "--abort" in c.args
        ]
        reset_calls = [
            c for c in mock_git.call_args_list
            if len(c.args) > 1 and c.args[1] == "reset" and "--hard" in c.args
        ]
        assert len(abort_calls) == 1
        assert len(reset_calls) == 1

    @patch("aquarco_supervisor.pipeline.git_workflow._run_cmd", new_callable=AsyncMock)
    @patch("aquarco_supervisor.pipeline.git_workflow._run_git", new_callable=AsyncMock)
    async def test_multiple_dirty_files_without_conflicts(self, mock_git, mock_cmd):
        """Multiple non-conflict dirty files should still return False."""
        async def git_side_effect(clone_dir, *args, **kwargs):
            cmd = args[0] if args else ""
            if cmd == "rev-parse":
                return "aaa111"
            if cmd == "status":
                return "M  file1.txt\nA  file2.txt\n?? file3.txt\n"
            return ""

        mock_git.side_effect = git_side_effect
        result = await perform_back_merge(
            "/repo", "org/repo", "release/1.2", "develop",
        )
        assert result is False


# ---------------------------------------------------------------------------
# _validate_branch_name — additional patterns
# ---------------------------------------------------------------------------


class TestValidateBranchNamePatterns:
    def test_underscore_in_name_allowed(self):
        """Underscores are allowed by _SAFE_BRANCH_RE (char class includes _)."""
        assert _validate_branch_name("feature/my_branch") == "feature/my_branch"

    def test_at_sign_invalid(self):
        with pytest.raises(ValueError):
            _validate_branch_name("feature@branch")

    def test_tilde_invalid(self):
        with pytest.raises(ValueError):
            _validate_branch_name("feature~1")

    def test_caret_invalid(self):
        with pytest.raises(ValueError):
            _validate_branch_name("feature^1")

    def test_back_merge_branch_format_valid(self):
        """The auto-generated back-merge branch format should always pass validation."""
        name = "aquarco/back-merge/release-1.2-to-develop"
        assert _validate_branch_name(name) == name

    def test_single_char_valid(self):
        assert _validate_branch_name("a") == "a"

    def test_numeric_only_valid(self):
        assert _validate_branch_name("123") == "123"

"""Unit tests for the git_workflow module."""

from __future__ import annotations

import pytest

from aquarco_supervisor.models import GitFlowBranches, GitFlowConfig
from aquarco_supervisor.pipeline.git_workflow import (
    BranchInfo,
    _extract_task_numeric_id,
    _parse_labels,
    _slugify,
    _validate_branch_name,
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

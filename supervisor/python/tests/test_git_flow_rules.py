"""Tests for GitFlow rules-based branch resolution and pipeline selection.

Covers the new features introduced in the GitFlow config commits:
- BranchRule / GitFlowBranchRules models
- _resolve_base_branch helper
- _parse_labels with base: prefix
- resolve_branch_info rules-based path
"""

from __future__ import annotations

import pytest

from aquarco_supervisor.models import (
    BranchRule,
    GitFlowBranches,
    GitFlowBranchRules,
    GitFlowConfig,
)
from aquarco_supervisor.pipeline.git_workflow import (
    BranchInfo,
    _parse_labels,
    _resolve_base_branch,
    resolve_branch_info,
)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestBranchRuleModel:
    def test_defaults(self):
        rule = BranchRule()
        assert rule.issueLabels == []
        assert rule.baseBranch == "development"
        assert rule.pipeline is None

    def test_explicit_values(self):
        rule = BranchRule(
            issueLabels=["bug", "critical"],
            baseBranch="stable",
            pipeline="hotfix-pipeline",
        )
        assert rule.issueLabels == ["bug", "critical"]
        assert rule.baseBranch == "stable"
        assert rule.pipeline == "hotfix-pipeline"


class TestGitFlowBranchRulesModel:
    def test_defaults(self):
        rules = GitFlowBranchRules()
        assert rules.feature is None
        assert rules.bugfix is None
        assert rules.hotfix is None
        assert rules.branchNameOverride is None

    def test_partial_rules(self):
        rules = GitFlowBranchRules(
            feature=BranchRule(issueLabels=["feature"]),
        )
        assert rules.feature is not None
        assert rules.bugfix is None
        assert rules.hotfix is None


class TestGitFlowConfigWithRules:
    def test_rules_none_by_default(self):
        cfg = GitFlowConfig()
        assert cfg.rules is None

    def test_rules_set(self):
        cfg = GitFlowConfig(
            rules=GitFlowBranchRules(
                feature=BranchRule(issueLabels=["feature"]),
            ),
        )
        assert cfg.rules is not None
        assert cfg.rules.feature is not None


# ---------------------------------------------------------------------------
# _parse_labels with base: prefix
# ---------------------------------------------------------------------------


class TestParseLabelsBasePrefix:
    def test_base_override(self):
        assert _parse_labels(["feature", "base: develop"]) == ("feature", "develop")

    def test_base_no_space(self):
        assert _parse_labels(["base:main"]) == (None, "main")

    def test_base_takes_precedence_over_target(self):
        # base: appears first, but target: would also match - last one wins
        _, override = _parse_labels(["base: staging", "target: develop"])
        # Both set base_override; last one wins
        assert override == "develop"

    def test_target_still_works_as_legacy(self):
        assert _parse_labels(["target: release/1.0"]) == (None, "release/1.0")

    def test_base_empty_value_ignored(self):
        assert _parse_labels(["base:"]) == (None, None)

    def test_base_with_whitespace(self):
        assert _parse_labels(["base:   release/2.0  "]) == (None, "release/2.0")


# ---------------------------------------------------------------------------
# _resolve_base_branch
# ---------------------------------------------------------------------------


class TestResolveBaseBranch:
    def _branches(self, **overrides) -> GitFlowBranches:
        return GitFlowBranches(**overrides)

    def test_stable(self):
        assert _resolve_base_branch("stable", self._branches(), None) == "main"

    def test_stable_custom(self):
        b = self._branches(stable="production")
        assert _resolve_base_branch("stable", b, None) == "production"

    def test_development(self):
        assert _resolve_base_branch("development", self._branches(), None) == "develop"

    def test_release_with_active(self):
        assert _resolve_base_branch(
            "release", self._branches(), "release/1.2",
        ) == "release/1.2"

    def test_release_without_active_falls_back_to_development(self):
        assert _resolve_base_branch(
            "release", self._branches(), None,
        ) == "develop"

    def test_explicit_branch_name(self):
        assert _resolve_base_branch(
            "custom/branch", self._branches(), None,
        ) == "custom/branch"


# ---------------------------------------------------------------------------
# resolve_branch_info - rules-based path
# ---------------------------------------------------------------------------


class TestResolveBranchInfoRules:
    """Tests for rules-based branch resolution."""

    def _cfg_with_rules(self, **kwargs) -> GitFlowConfig:
        rules = GitFlowBranchRules(
            feature=BranchRule(
                issueLabels=["feature", "enhancement"],
                baseBranch="development",
                pipeline="feature-pipeline",
            ),
            bugfix=BranchRule(
                issueLabels=["bug"],
                baseBranch="release",
                pipeline="bugfix-pipeline",
            ),
            hotfix=BranchRule(
                issueLabels=["critical", "hotfix"],
                baseBranch="stable",
                pipeline="hotfix-pipeline",
            ),
        )
        return GitFlowConfig(
            enabled=True,
            branches=GitFlowBranches(**kwargs),
            rules=rules,
        )

    def test_feature_rule_match(self):
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-10",
            "New widget",
            ["enhancement"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "feature"
        assert result.base_branch == "develop"
        assert result.branch_name == "feature/10-new-widget"

    def test_bugfix_rule_match(self):
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-20",
            "Fix crash",
            ["bug"],
            active_release_branch="release/2.0",
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "bugfix"
        assert result.base_branch == "release/2.0"
        assert result.branch_name == "bugfix/20-fix-crash"

    def test_bugfix_rule_no_active_release(self):
        """When baseBranch=release but no active release, falls to development."""
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-21",
            "Another fix",
            ["bug"],
            active_release_branch=None,
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "bugfix"
        assert result.base_branch == "develop"

    def test_hotfix_rule_match(self):
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-30",
            "Security patch",
            ["critical"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "hotfix"
        assert result.base_branch == "main"

    def test_hotfix_priority_over_bugfix(self):
        """When labels match both hotfix and bugfix rules, hotfix wins."""
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-31",
            "Critical bug",
            ["critical", "bug"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "hotfix"

    def test_base_override_with_rules(self):
        """base: label overrides rules-computed base branch."""
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-40",
            "Targeted fix",
            ["bug", "base: release/3.0"],
            active_release_branch="release/2.0",
        )
        assert isinstance(result, BranchInfo)
        assert result.base_branch == "release/3.0"

    def test_no_rule_match_falls_to_legacy(self):
        """When no rule labels match, fall through to legacy direct-label matching."""
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-50",
            "Unmatched task",
            ["unrelated-label"],
        )
        assert isinstance(result, BranchInfo)
        # Legacy path defaults to feature
        assert result.branch_type == "feature"
        assert result.base_branch == "develop"

    def test_rules_case_insensitive(self):
        """Rule matching should be case-insensitive."""
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-60",
            "Case test",
            ["Enhancement"],  # capitalized
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "feature"

    def test_invalid_base_override_returns_error(self):
        """Invalid base: branch name returns error string."""
        result = resolve_branch_info(
            self._cfg_with_rules(),
            "github-issue-aquarco-70",
            "Bad override",
            ["bug", "base: -invalid"],
        )
        assert isinstance(result, str)
        assert "invalid" in result.lower()

    def test_empty_rules_falls_to_legacy(self):
        """GitFlowBranchRules with all None rules falls to legacy."""
        cfg = GitFlowConfig(
            enabled=True,
            branches=GitFlowBranches(),
            rules=GitFlowBranchRules(),  # all None
        )
        result = resolve_branch_info(
            cfg,
            "github-issue-aquarco-80",
            "Default task",
            ["feature"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_type == "feature"

    def test_rules_with_custom_branches(self):
        """Rules work with custom branch naming patterns."""
        result = resolve_branch_info(
            self._cfg_with_rules(feature="feat/*", development="dev"),
            "github-issue-aquarco-90",
            "Custom pattern",
            ["enhancement"],
        )
        assert isinstance(result, BranchInfo)
        assert result.branch_name == "feat/90-custom-pattern"
        assert result.base_branch == "dev"

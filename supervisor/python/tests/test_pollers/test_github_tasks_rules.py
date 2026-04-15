"""Tests for GitHubTasksPoller rule-based pipeline selection.

Covers _extract_repo_rules and _select_pipeline with repo-level rules.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.models import PipelineConfig, SupervisorConfig
from aquarco_supervisor.pollers.github_tasks import GitHubTasksPoller
from aquarco_supervisor.task_queue import TaskQueue


def _make_poller(
    sample_config: SupervisorConfig, sample_pipelines: list[PipelineConfig],
) -> GitHubTasksPoller:
    tq = AsyncMock(spec=TaskQueue)
    db = AsyncMock(spec=Database)
    return GitHubTasksPoller(sample_config, tq, db, sample_pipelines)


# ---------------------------------------------------------------------------
# _extract_repo_rules
# ---------------------------------------------------------------------------


class TestExtractRepoRules:
    def test_none_repo(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        assert poller._extract_repo_rules(None) is None

    def test_no_git_flow_config(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        assert poller._extract_repo_rules({"name": "repo"}) is None

    def test_empty_git_flow_config(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        assert poller._extract_repo_rules({"git_flow_config": {}}) is None

    def test_git_flow_config_no_rules(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        repo = {"git_flow_config": {"enabled": True, "branches": {}}}
        assert poller._extract_repo_rules(repo) is None

    def test_git_flow_config_with_rules(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {"feature": {"issueLabels": ["feature"], "baseBranch": "development"}}
        repo = {"git_flow_config": {"rules": rules}}
        result = poller._extract_repo_rules(repo)
        assert result == rules

    def test_git_flow_config_json_string(self, sample_config, sample_pipelines):
        """When git_flow_config comes as a JSON string from the DB."""
        poller = _make_poller(sample_config, sample_pipelines)
        import json
        rules = {"bugfix": {"issueLabels": ["bug"], "baseBranch": "release"}}
        repo = {"git_flow_config": json.dumps({"rules": rules})}
        result = poller._extract_repo_rules(repo)
        assert result == rules

    def test_invalid_json_string(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        repo = {"git_flow_config": "not-valid-json"}
        assert poller._extract_repo_rules(repo) is None

    def test_non_dict_git_flow_config(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        repo = {"git_flow_config": 42}
        assert poller._extract_repo_rules(repo) is None

    def test_empty_rules(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        repo = {"git_flow_config": {"rules": {}}}
        assert poller._extract_repo_rules(repo) is None


# ---------------------------------------------------------------------------
# _select_pipeline with repo rules
# ---------------------------------------------------------------------------


class TestSelectPipelineWithRules:
    def test_hotfix_priority_over_bugfix(self, sample_config, sample_pipelines):
        """Hotfix rules take priority over bugfix when labels match both."""
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "bugfix": {"issueLabels": ["bug"], "pipeline": "bugfix-pipeline"},
            "hotfix": {"issueLabels": ["bug", "critical"], "pipeline": "hotfix-pipeline"},
        }
        result = poller._select_pipeline(["bug", "critical"], rules)
        assert result == "hotfix-pipeline"

    def test_bugfix_priority_over_feature(self, sample_config, sample_pipelines):
        """Bugfix rules take priority over feature."""
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "feature": {"issueLabels": ["enhancement"], "pipeline": "feature-pipeline"},
            "bugfix": {"issueLabels": ["enhancement", "regression"], "pipeline": "bugfix-pipeline"},
        }
        result = poller._select_pipeline(["enhancement", "regression"], rules)
        assert result == "bugfix-pipeline"

    def test_no_explicit_pipeline_uses_default_mapping(self, sample_config, sample_pipelines):
        """When rule has no 'pipeline' field, default from _BRANCH_TYPE_PIPELINE."""
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "feature": {"issueLabels": ["feature"], "baseBranch": "development"},
        }
        result = poller._select_pipeline(["feature"], rules)
        assert result == "feature-pipeline"

    def test_bugfix_no_explicit_pipeline(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "bugfix": {"issueLabels": ["bug"], "baseBranch": "release"},
        }
        result = poller._select_pipeline(["bug"], rules)
        assert result == "bugfix-pipeline"

    def test_hotfix_no_explicit_pipeline(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "hotfix": {"issueLabels": ["critical"], "baseBranch": "stable"},
        }
        result = poller._select_pipeline(["critical"], rules)
        assert result == "bugfix-pipeline"  # hotfix maps to bugfix-pipeline

    def test_case_insensitive_label_matching(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "feature": {"issueLabels": ["Feature"], "pipeline": "feature-pipeline"},
        }
        result = poller._select_pipeline(["FEATURE"], rules)
        assert result == "feature-pipeline"

    def test_no_match_falls_to_default(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "feature": {"issueLabels": ["feature"]},
        }
        result = poller._select_pipeline(["unrelated"], rules)
        assert result == "feature-pipeline"

    def test_rule_with_non_dict_value_skipped(self, sample_config, sample_pipelines):
        """Malformed rules (non-dict) are gracefully skipped."""
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "feature": "not-a-dict",
            "bugfix": {"issueLabels": ["bug"], "pipeline": "bugfix-pipeline"},
        }
        result = poller._select_pipeline(["bug"], rules)
        assert result == "bugfix-pipeline"

    def test_empty_issue_labels_never_matches(self, sample_config, sample_pipelines):
        poller = _make_poller(sample_config, sample_pipelines)
        rules = {
            "feature": {"issueLabels": [], "pipeline": "feature-pipeline"},
        }
        result = poller._select_pipeline(["feature"], rules)
        # Empty issueLabels means the rule never matches
        assert result == "feature-pipeline"  # falls to default


@pytest.mark.asyncio
async def test_process_issue_uses_repo_rules_for_pipeline(
    sample_config: SupervisorConfig, sample_pipelines: list[PipelineConfig],
) -> None:
    """Integration: _process_issue passes repo rules to _select_pipeline."""
    tq = AsyncMock(spec=TaskQueue)
    tq.task_exists = AsyncMock(return_value=False)
    tq.create_task = AsyncMock(return_value=True)
    db = AsyncMock(spec=Database)
    poller = GitHubTasksPoller(sample_config, tq, db, sample_pipelines)

    issue = {
        "number": 100,
        "title": "Hotfix needed",
        "body": "Critical issue",
        "url": "https://github.com/owner/repo/issues/100",
        "labels": [{"name": "critical"}],
    }
    repo = {
        "name": "my-repo",
        "url": "git@github.com:owner/repo.git",
        "git_flow_config": {
            "rules": {
                "hotfix": {
                    "issueLabels": ["critical"],
                    "baseBranch": "stable",
                    "pipeline": "hotfix-pipeline",
                },
            },
        },
    }
    result = await poller._process_issue(issue, "my-repo", "owner/repo", repo)
    assert result is True
    call_kwargs = tq.create_task.call_args[1]
    assert call_kwargs["pipeline"] == "hotfix-pipeline"


@pytest.mark.asyncio
async def test_process_issue_without_repo_defaults_to_feature(
    sample_config: SupervisorConfig, sample_pipelines: list[PipelineConfig],
) -> None:
    """Without repo argument, falls to feature-pipeline."""
    tq = AsyncMock(spec=TaskQueue)
    tq.task_exists = AsyncMock(return_value=False)
    tq.create_task = AsyncMock(return_value=True)
    db = AsyncMock(spec=Database)
    poller = GitHubTasksPoller(sample_config, tq, db, sample_pipelines)

    issue = {
        "number": 101,
        "title": "Some task",
        "body": "",
        "url": "https://github.com/owner/repo/issues/101",
        "labels": [{"name": "bug"}],
    }
    result = await poller._process_issue(issue, "my-repo", "owner/repo")
    assert result is True
    call_kwargs = tq.create_task.call_args[1]
    assert call_kwargs["pipeline"] == "feature-pipeline"

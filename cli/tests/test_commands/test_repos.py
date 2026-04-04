"""Tests for the repos command."""

from __future__ import annotations

from unittest.mock import patch

import httpx
from typer.testing import CliRunner

from aquarco_cli.commands.repos import _repo_name_from_url
from aquarco_cli.main import app

runner = CliRunner()


class TestReposAdd:
    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_add_repo(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "registerRepository": {
                "repository": {"name": "myrepo", "url": "https://github.com/user/myrepo", "branch": None, "cloneStatus": "PENDING", "pollers": []},
                "errors": None,
            }
        }
        result = runner.invoke(app, ["repos", "add", "https://github.com/user/myrepo"])
        assert result.exit_code == 0
        assert "myrepo" in result.output
        assert "registered" in result.output.lower()

    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_add_repo_with_options(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "registerRepository": {
                "repository": {"name": "custom", "url": "https://github.com/user/repo", "branch": "develop", "cloneStatus": "PENDING", "pollers": ["github-tasks"]},
                "errors": None,
            }
        }
        result = runner.invoke(app, [
            "repos", "add", "https://github.com/user/repo",
            "--name", "custom", "--branch", "develop", "--poller", "github-tasks",
        ])
        assert result.exit_code == 0
        # Verify mutation was called with correct variables
        call_args = mock_client.execute.call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("variables", {})
        assert variables["input"]["name"] == "custom"
        assert variables["input"]["branch"] == "develop"


class TestReposList:
    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_list_repos(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "repositories": [
                {"name": "repo1", "url": "https://github.com/u/repo1", "branch": "main", "cloneStatus": "READY", "pollers": ["github-tasks"], "taskCount": 5, "lastClonedAt": None, "lastPulledAt": None, "headSha": None, "errorMessage": None},
            ]
        }
        result = runner.invoke(app, ["repos", "list"])
        assert result.exit_code == 0
        assert "repo1" in result.output
        assert "READY" in result.output


class TestReposRemove:
    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_remove_repo(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "removeRepository": {
                "repository": {"name": "repo1"},
                "errors": None,
            }
        }
        result = runner.invoke(app, ["repos", "remove", "repo1"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()

    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_remove_repo_with_errors(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "removeRepository": {
                "repository": None,
                "errors": [{"message": "Repository not found"}],
            }
        }
        result = runner.invoke(app, ["repos", "remove", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRepoNameFromUrl:
    def test_github_url(self):
        assert _repo_name_from_url("https://github.com/user/myrepo") == "myrepo"

    def test_github_url_with_git_suffix(self):
        assert _repo_name_from_url("https://github.com/user/myrepo.git") == "myrepo"

    def test_simple_name(self):
        assert _repo_name_from_url("myrepo") == "myrepo"

    def test_nested_path(self):
        assert _repo_name_from_url("https://gitlab.com/org/sub/repo") == "repo"


class TestReposListEmpty:
    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_empty_repos(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {"repositories": []}
        result = runner.invoke(app, ["repos", "list"])
        assert result.exit_code == 0
        assert "no repositories" in result.output.lower()


class TestReposAddErrors:
    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_add_repo_api_connection_error(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["repos", "add", "https://github.com/u/r"])
        assert result.exit_code == 1
        assert "cannot reach" in result.output.lower()

    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_add_repo_mutation_errors(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "registerRepository": {
                "repository": None,
                "errors": [{"field": "url", "message": "Invalid URL"}],
            }
        }
        result = runner.invoke(app, ["repos", "add", "bad-url"])
        assert result.exit_code == 1
        assert "invalid url" in result.output.lower()

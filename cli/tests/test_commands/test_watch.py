"""Tests for the watch command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestWatchAdd:
    @patch("aquarco_cli.commands.watch.GraphQLClient")
    def test_add_repo(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "registerRepository": {
                "repository": {"name": "myrepo", "url": "https://github.com/user/myrepo", "branch": None, "cloneStatus": "PENDING", "pollers": []},
                "errors": None,
            }
        }
        result = runner.invoke(app, ["watch", "add", "https://github.com/user/myrepo"])
        assert result.exit_code == 0
        assert "myrepo" in result.output
        assert "registered" in result.output.lower()

    @patch("aquarco_cli.commands.watch.GraphQLClient")
    def test_add_repo_with_options(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "registerRepository": {
                "repository": {"name": "custom", "url": "https://github.com/user/repo", "branch": "develop", "cloneStatus": "PENDING", "pollers": ["github-tasks"]},
                "errors": None,
            }
        }
        result = runner.invoke(app, [
            "watch", "add", "https://github.com/user/repo",
            "--name", "custom", "--branch", "develop", "--poller", "github-tasks",
        ])
        assert result.exit_code == 0
        # Verify mutation was called with correct variables
        call_args = mock_client.execute.call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("variables", {})
        assert variables["input"]["name"] == "custom"
        assert variables["input"]["branch"] == "develop"


class TestWatchList:
    @patch("aquarco_cli.commands.watch.GraphQLClient")
    def test_list_repos(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "repositories": [
                {"name": "repo1", "url": "https://github.com/u/repo1", "branch": "main", "cloneStatus": "READY", "pollers": ["github-tasks"], "taskCount": 5, "lastClonedAt": None, "lastPulledAt": None, "headSha": None, "errorMessage": None},
            ]
        }
        result = runner.invoke(app, ["watch", "list"])
        assert result.exit_code == 0
        assert "repo1" in result.output
        assert "READY" in result.output


class TestWatchRemove:
    @patch("aquarco_cli.commands.watch.GraphQLClient")
    def test_remove_repo(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "removeRepository": {
                "repository": {"name": "repo1"},
                "errors": None,
            }
        }
        result = runner.invoke(app, ["watch", "remove", "repo1"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()

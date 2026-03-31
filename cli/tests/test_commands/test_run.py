"""Tests for the run command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestRunCommand:
    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_create_task(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "createTask": {
                "task": {"id": "42", "title": "Fix bug", "status": "PENDING", "pipeline": "feature-pipeline", "repository": {"name": "myrepo"}},
                "errors": None,
            }
        }
        result = runner.invoke(app, ["run", "--repo", "myrepo", "Fix bug"])
        assert result.exit_code == 0
        assert "42" in result.output

        # Verify correct mutation variables
        call_args = mock_client.execute.call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("variables", {})
        assert variables["input"]["title"] == "Fix bug"
        assert variables["input"]["repository"] == "myrepo"
        assert variables["input"]["source"] == "cli"

    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_create_task_with_pipeline(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "createTask": {
                "task": {"id": "43", "title": "Test", "status": "PENDING", "pipeline": "review-pipeline", "repository": {"name": "repo"}},
                "errors": None,
            }
        }
        result = runner.invoke(app, ["run", "--repo", "repo", "--pipeline", "review-pipeline", "Test"])
        assert result.exit_code == 0
        call_args = mock_client.execute.call_args
        variables = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("variables", {})
        assert variables["input"]["pipeline"] == "review-pipeline"

    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_create_task_api_error(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "createTask": {
                "task": None,
                "errors": [{"field": "repository", "message": "Repository not found"}],
            }
        }
        result = runner.invoke(app, ["run", "--repo", "nonexistent", "Test"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

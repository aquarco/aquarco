"""Tests for the run command."""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


def _task_response(task_id="42", status="PENDING"):
    return {
        "createTask": {
            "task": {"id": task_id, "title": "Test", "status": status, "pipeline": "fp", "repository": {"name": "repo"}},
            "errors": None,
        }
    }


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


class TestRunContext:
    """Tests for the --context flag with JSON and file inputs."""

    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_context_json_string(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = _task_response()
        result = runner.invoke(app, [
            "run", "--repo", "repo", "--context", '{"key": "value"}', "task",
        ])
        assert result.exit_code == 0
        call_args = mock_client.execute.call_args
        variables = call_args[0][1]
        assert variables["input"]["initialContext"] == {"key": "value"}

    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_context_plain_text_fallback(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = _task_response()
        result = runner.invoke(app, [
            "run", "--repo", "repo", "--context", "not json", "task",
        ])
        assert result.exit_code == 0
        call_args = mock_client.execute.call_args
        variables = call_args[0][1]
        assert variables["input"]["initialContext"] == {"text": "not json"}

    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_context_from_file(self, mock_cls, tmp_path):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = _task_response()
        ctx_file = tmp_path / "context.json"
        ctx_file.write_text('{"from": "file"}')
        result = runner.invoke(app, [
            "run", "--repo", "repo", "--context", f"@{ctx_file}", "task",
        ])
        assert result.exit_code == 0
        call_args = mock_client.execute.call_args
        variables = call_args[0][1]
        assert variables["input"]["initialContext"] == {"from": "file"}

    def test_context_file_not_found(self):
        result = runner.invoke(app, [
            "run", "--repo", "repo", "--context", "@/nonexistent/file.json", "task",
        ])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_context_file_invalid_json(self, tmp_path):
        ctx_file = tmp_path / "bad.json"
        ctx_file.write_text("not valid json {{{")
        result = runner.invoke(app, [
            "run", "--repo", "repo", "--context", f"@{ctx_file}", "task",
        ])
        assert result.exit_code == 1
        assert "invalid json" in result.output.lower()

    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_context_with_priority(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = _task_response()
        result = runner.invoke(app, [
            "run", "--repo", "repo", "--priority", "5", "task",
        ])
        assert result.exit_code == 0
        call_args = mock_client.execute.call_args
        variables = call_args[0][1]
        assert variables["input"]["priority"] == 5


class TestRunConnectionError:
    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_connection_error_friendly_message(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = httpx.ConnectError("Connection refused")
        result = runner.invoke(app, ["run", "--repo", "repo", "task"])
        assert result.exit_code == 1
        assert "cannot reach" in result.output.lower()


class TestRunFollow:
    """Tests for the --follow flag with circuit breaker."""

    @patch("aquarco_cli.task.time.sleep")
    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_follow_until_completed(self, mock_cls, mock_sleep):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            _task_response("42"),
            {"pipelineStatus": {
                "taskId": "42", "status": "EXECUTING",
                "stages": [{"stageNumber": 0, "category": "analyze", "status": "COMPLETED", "agent": "a1"}],
            }},
            {"pipelineStatus": {
                "taskId": "42", "status": "COMPLETED",
                "stages": [{"stageNumber": 0, "category": "analyze", "status": "COMPLETED", "agent": "a1"}],
            }},
        ]
        result = runner.invoke(app, ["run", "--repo", "repo", "--follow", "task"])
        assert result.exit_code == 0
        assert "COMPLETED" in result.output

    @patch("aquarco_cli.task.time.sleep")
    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_follow_circuit_breaker(self, mock_cls, mock_sleep):
        mock_client = mock_cls.return_value
        # First call succeeds (create task), next 5 poll calls all fail
        mock_client.execute.side_effect = [
            _task_response("42"),
            RuntimeError("poll fail 1"),
            RuntimeError("poll fail 2"),
            RuntimeError("poll fail 3"),
            RuntimeError("poll fail 4"),
            RuntimeError("poll fail 5"),
        ]
        result = runner.invoke(app, ["run", "--repo", "repo", "--follow", "task"])
        assert result.exit_code == 1
        assert "too many consecutive errors" in result.output.lower()

    @patch("aquarco_cli.task.time.sleep")
    @patch("aquarco_cli.commands.run.GraphQLClient")
    def test_follow_error_resets_on_success(self, mock_cls, mock_sleep):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            _task_response("42"),
            RuntimeError("fail 1"),
            RuntimeError("fail 2"),
            # Reset after a successful poll
            {"pipelineStatus": {
                "taskId": "42", "status": "EXECUTING",
                "stages": [{"stageNumber": 0, "category": "analyze", "status": "EXECUTING", "agent": "a1"}],
            }},
            # Then complete
            {"pipelineStatus": {
                "taskId": "42", "status": "COMPLETED",
                "stages": [{"stageNumber": 0, "category": "analyze", "status": "COMPLETED", "agent": "a1"}],
            }},
        ]
        result = runner.invoke(app, ["run", "--repo", "repo", "--follow", "task"])
        assert result.exit_code == 0
        assert "COMPLETED" in result.output

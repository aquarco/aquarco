"""Tests for the status command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestStatusDashboard:
    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_dashboard(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {
                "dashboardStats": {
                    "totalTasks": 100,
                    "pendingTasks": 5,
                    "executingTasks": 2,
                    "completedTasks": 80,
                    "failedTasks": 10,
                    "blockedTasks": 3,
                    "activeAgents": 2,
                    "totalTokensToday": 50000,
                    "totalCostToday": 1.23,
                    "tasksByPipeline": [],
                    "tasksByRepository": [],
                }
            },
            {
                "tasks": {
                    "nodes": [
                        {
                            "id": "1",
                            "title": "Task One",
                            "status": "COMPLETED",
                            "priority": 0,
                            "source": "github",
                            "pipeline": "feature-pipeline",
                            "repository": {"name": "repo1"},
                            "createdAt": "2026-03-31T00:00:00Z",
                            "startedAt": None,
                            "completedAt": None,
                            "errorMessage": None,
                            "retryCount": 0,
                            "branchName": None,
                            "prNumber": None,
                            "totalCostUsd": None,
                        }
                    ],
                    "totalCount": 1,
                }
            },
        ]
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "100" in result.output  # totalTasks
        assert "Task One" in result.output

    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_dashboard_json(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"dashboardStats": {"totalTasks": 1, "pendingTasks": 0, "executingTasks": 0, "completedTasks": 1, "failedTasks": 0, "blockedTasks": 0, "activeAgents": 0, "totalTokensToday": 0, "totalCostToday": 0.0, "tasksByPipeline": [], "tasksByRepository": []}},
            {"tasks": {"nodes": [], "totalCount": 0}},
        ]
        result = runner.invoke(app, ["status", "--json"])
        assert result.exit_code == 0
        assert "dashboardStats" in result.output


class TestStatusDetail:
    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_task_detail(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "task": {
                "id": "42",
                "title": "Fix something",
                "status": "COMPLETED",
                "priority": 0,
                "source": "cli",
                "sourceRef": None,
                "pipeline": "feature-pipeline",
                "repository": {"name": "myrepo"},
                "createdAt": "2026-03-31T00:00:00Z",
                "updatedAt": "2026-03-31T01:00:00Z",
                "startedAt": "2026-03-31T00:01:00Z",
                "completedAt": "2026-03-31T01:00:00Z",
                "retryCount": 0,
                "errorMessage": None,
                "branchName": "fix/something",
                "prNumber": 10,
                "totalCostUsd": 0.05,
                "stages": [
                    {"id": "1", "stageNumber": 0, "iteration": 1, "run": 1, "category": "analyze", "agent": "analyze-agent", "status": "COMPLETED", "startedAt": "2026-03-31T00:01:00Z", "completedAt": "2026-03-31T00:02:00Z", "tokensInput": 100, "tokensOutput": 200, "costUsd": 0.01, "errorMessage": None},
                ],
            }
        }
        result = runner.invoke(app, ["status", "42"])
        assert result.exit_code == 0
        assert "Fix something" in result.output
        assert "myrepo" in result.output
        assert "analyze" in result.output

    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_task_not_found(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {"task": None}
        result = runner.invoke(app, ["status", "999"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_task_detail_json(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "task": {"id": "1", "title": "t", "status": "COMPLETED"}
        }
        result = runner.invoke(app, ["status", "--json", "1"])
        assert result.exit_code == 0
        assert "COMPLETED" in result.output

    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_task_detail_nullable_repository(self, mock_cls):
        """Task with null repository should display '-' instead of crashing."""
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "task": {
                "id": "42", "title": "Test", "status": "PENDING",
                "priority": 0, "source": "cli", "sourceRef": None,
                "pipeline": "fp", "repository": None,
                "createdAt": "2026-01-01", "updatedAt": None,
                "startedAt": None, "completedAt": None,
                "retryCount": 0, "errorMessage": None,
                "branchName": None, "prNumber": None,
                "totalCostUsd": None, "stages": [],
            }
        }
        result = runner.invoke(app, ["status", "42"])
        assert result.exit_code == 0
        assert "-" in result.output  # null repo should show '-'


class TestStatusFollowWarning:
    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_follow_without_task_id_warns(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"dashboardStats": {"totalTasks": 0, "pendingTasks": 0, "executingTasks": 0, "completedTasks": 0, "failedTasks": 0, "blockedTasks": 0, "activeAgents": 0, "totalTokensToday": 0, "totalCostToday": 0.0, "tasksByPipeline": [], "tasksByRepository": []}},
            {"tasks": {"nodes": [], "totalCount": 0}},
        ]
        result = runner.invoke(app, ["status", "--follow"])
        assert result.exit_code == 0
        assert "only supported" in result.output.lower() or "ignoring" in result.output.lower()


class TestStatusDashboardNullable:
    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_null_total_cost_today(self, mock_cls):
        """totalCostToday being null should not crash."""
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"dashboardStats": {"totalTasks": 1, "pendingTasks": 0, "executingTasks": 0, "completedTasks": 1, "failedTasks": 0, "blockedTasks": 0, "activeAgents": 0, "totalTokensToday": 0, "totalCostToday": None, "tasksByPipeline": [], "tasksByRepository": []}},
            {"tasks": {"nodes": [], "totalCount": 0}},
        ]
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "$0.00" in result.output

    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_tasks_with_null_repository(self, mock_cls):
        """Task node with null repository should not crash."""
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"dashboardStats": {"totalTasks": 1, "pendingTasks": 0, "executingTasks": 0, "completedTasks": 1, "failedTasks": 0, "blockedTasks": 0, "activeAgents": 0, "totalTokensToday": 0, "totalCostToday": 0.0, "tasksByPipeline": [], "tasksByRepository": []}},
            {"tasks": {"nodes": [
                {"id": "1", "title": "T", "status": "COMPLETED", "priority": 0, "source": "cli", "pipeline": "fp", "repository": None, "createdAt": "2026-01-01", "startedAt": None, "completedAt": None, "errorMessage": None, "retryCount": 0, "branchName": None, "prNumber": None, "totalCostUsd": None},
            ], "totalCount": 1}},
        ]
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0


class TestStatusConnectionError:
    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_connection_error_friendly_message(self, mock_cls):
        mock_client = mock_cls.return_value
        import httpx
        mock_client.execute.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 1
        assert "cannot reach" in result.output.lower()


class TestStatusFollow:
    @patch("aquarco_cli.task.time.sleep")
    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_follow_task_completes(self, mock_cls, mock_sleep):
        mock_client = mock_cls.return_value
        task_data = {
            "task": {
                "id": "42", "title": "Test", "status": "EXECUTING",
                "priority": 0, "source": "cli", "sourceRef": None,
                "pipeline": "fp", "repository": {"name": "repo"},
                "createdAt": "2026-01-01", "updatedAt": None,
                "startedAt": "2026-01-01", "completedAt": None,
                "retryCount": 0, "errorMessage": None,
                "branchName": None, "prNumber": None,
                "totalCostUsd": None, "stages": [],
            }
        }
        completed_task_data = {**task_data, "task": {**task_data["task"], "status": "COMPLETED"}}
        mock_client.execute.side_effect = [
            task_data,  # Initial detail
            {"pipelineStatus": {"taskId": "42", "status": "COMPLETED", "stages": []}},
            completed_task_data,  # Re-print after completion
        ]
        result = runner.invoke(app, ["status", "--follow", "42"])
        assert result.exit_code == 0

    @patch("aquarco_cli.task.time.sleep")
    @patch("aquarco_cli.commands.status.GraphQLClient")
    def test_follow_circuit_breaker(self, mock_cls, mock_sleep):
        mock_client = mock_cls.return_value
        task_data = {
            "task": {
                "id": "42", "title": "Test", "status": "EXECUTING",
                "priority": 0, "source": "cli", "sourceRef": None,
                "pipeline": "fp", "repository": {"name": "repo"},
                "createdAt": "2026-01-01", "updatedAt": None,
                "startedAt": "2026-01-01", "completedAt": None,
                "retryCount": 0, "errorMessage": None,
                "branchName": None, "prNumber": None,
                "totalCostUsd": None, "stages": [],
            }
        }
        mock_client.execute.side_effect = [
            task_data,  # Initial detail
            RuntimeError("fail1"),
            RuntimeError("fail2"),
            RuntimeError("fail3"),
            RuntimeError("fail4"),
            RuntimeError("fail5"),
        ]
        result = runner.invoke(app, ["status", "--follow", "42"])
        assert result.exit_code == 1
        assert "too many" in result.output.lower()

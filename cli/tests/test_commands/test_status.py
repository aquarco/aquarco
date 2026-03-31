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

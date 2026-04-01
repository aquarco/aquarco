"""Unit tests for cli/status.py — status reporting."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aquarco_supervisor.cli.status import (
    _get_registry_summary,
    _get_supervisor_process_status,
    _render_human,
)


# ---------------------------------------------------------------------------
# _get_supervisor_process_status
# ---------------------------------------------------------------------------


class TestGetSupervisorProcessStatus:
    def test_no_pid_file_returns_stopped(self, tmp_path: Path) -> None:
        pid_file = str(tmp_path / "supervisor.pid")
        result = _get_supervisor_process_status(pid_file)

        assert result["status"] == "stopped"
        assert result["pid"] == ""
        assert result["uptime"] == "unknown"

    def test_pid_file_with_invalid_content_returns_stopped(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "supervisor.pid"
        pid_file.write_text("not-a-number")

        result = _get_supervisor_process_status(str(pid_file))

        assert result["status"] == "stopped"
        assert result["pid"] == ""

    def test_pid_file_with_empty_content_returns_stopped(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "supervisor.pid"
        pid_file.write_text("   ")

        result = _get_supervisor_process_status(str(pid_file))

        assert result["status"] == "stopped"

    def test_running_process_detected(self, tmp_path: Path) -> None:
        # Use the current process PID — guaranteed to exist
        current_pid = os.getpid()
        pid_file = tmp_path / "supervisor.pid"
        pid_file.write_text(str(current_pid))

        result = _get_supervisor_process_status(str(pid_file))

        assert result["status"] == "running"
        assert result["pid"] == str(current_pid)

    def test_stale_pid_file_returns_stale_pid(self, tmp_path: Path) -> None:
        # PID 99999999 almost certainly does not exist
        pid_file = tmp_path / "supervisor.pid"
        pid_file.write_text("99999999")

        with patch("os.kill", side_effect=ProcessLookupError):
            result = _get_supervisor_process_status(str(pid_file))

        assert result["status"] == "stale-pid"
        assert result["pid"] == "99999999"

    def test_permission_error_counts_as_running(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "supervisor.pid"
        pid_file.write_text("12345")

        with patch("os.kill", side_effect=PermissionError):
            result = _get_supervisor_process_status(str(pid_file))

        assert result["status"] == "running"
        assert result["pid"] == "12345"

    def test_result_has_required_keys(self, tmp_path: Path) -> None:
        pid_file = str(tmp_path / "missing.pid")
        result = _get_supervisor_process_status(pid_file)

        assert "status" in result
        assert "pid" in result
        assert "uptime" in result

    def test_pid_value_is_string_when_present(self, tmp_path: Path) -> None:
        current_pid = os.getpid()
        pid_file = tmp_path / "supervisor.pid"
        pid_file.write_text(str(current_pid))

        result = _get_supervisor_process_status(str(pid_file))

        # pid field must always be a string (JSON-serialisation friendly)
        assert isinstance(result["pid"], str)


# ---------------------------------------------------------------------------
# _get_registry_summary
# ---------------------------------------------------------------------------


class TestGetRegistrySummary:
    def test_no_agents_dir_returns_zeros(self, tmp_path: Path) -> None:
        result = _get_registry_summary(str(tmp_path / "nonexistent"))

        assert result["agent_count"] == 0
        assert result["categories"] == []

    def test_counts_yaml_files_when_no_registry_json(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents" / "definitions"
        agents_dir.mkdir(parents=True)
        (agents_dir / "agent-a.yaml").write_text("name: a")
        (agents_dir / "agent-b.yaml").write_text("name: b")
        (agents_dir / "agent-c.yaml").write_text("name: c")

        result = _get_registry_summary(str(agents_dir))

        assert result["agent_count"] == 3
        assert result["categories"] == []

    def test_reads_agent_count_from_registry_json(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents" / "definitions"
        agents_dir.mkdir(parents=True)

        # Registry lives at agents_dir/../schemas/agent-registry.json
        schemas_dir = tmp_path / "agents" / "schemas"
        schemas_dir.mkdir()
        registry = {
            "agents": [
                {"spec": {"categories": ["review"]}},
                {"spec": {"categories": ["test", "review"]}},
            ]
        }
        (schemas_dir / "agent-registry.json").write_text(json.dumps(registry))

        result = _get_registry_summary(str(agents_dir))

        assert result["agent_count"] == 2

    def test_extracts_unique_categories_from_registry_json(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents" / "definitions"
        agents_dir.mkdir(parents=True)

        schemas_dir = tmp_path / "agents" / "schemas"
        schemas_dir.mkdir()
        registry = {
            "agents": [
                {"spec": {"categories": ["review", "test"]}},
                {"spec": {"categories": ["test", "document"]}},
            ]
        }
        (schemas_dir / "agent-registry.json").write_text(json.dumps(registry))

        result = _get_registry_summary(str(agents_dir))

        assert result["categories"] == sorted(["document", "review", "test"])

    def test_malformed_registry_json_returns_zeros(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents" / "definitions"
        agents_dir.mkdir(parents=True)

        schemas_dir = tmp_path / "agents" / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "agent-registry.json").write_text("{broken json")

        result = _get_registry_summary(str(agents_dir))

        assert result["agent_count"] == 0
        assert result["categories"] == []

    def test_empty_agents_list_in_registry(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents" / "definitions"
        agents_dir.mkdir(parents=True)

        schemas_dir = tmp_path / "agents" / "schemas"
        schemas_dir.mkdir()
        (schemas_dir / "agent-registry.json").write_text(json.dumps({"agents": []}))

        result = _get_registry_summary(str(agents_dir))

        assert result["agent_count"] == 0
        assert result["categories"] == []

    def test_agents_without_spec_categories_tolerated(self, tmp_path: Path) -> None:
        agents_dir = tmp_path / "agents" / "definitions"
        agents_dir.mkdir(parents=True)

        schemas_dir = tmp_path / "agents" / "schemas"
        schemas_dir.mkdir()
        # Agents with no spec or no categories key
        registry = {"agents": [{"name": "bare-agent"}, {"spec": {}}]}
        (schemas_dir / "agent-registry.json").write_text(json.dumps(registry))

        result = _get_registry_summary(str(agents_dir))

        assert result["agent_count"] == 2
        assert result["categories"] == []

    def test_result_has_required_keys(self, tmp_path: Path) -> None:
        result = _get_registry_summary(str(tmp_path / "nonexistent"))

        assert "agent_count" in result
        assert "categories" in result


# ---------------------------------------------------------------------------
# _render_human — smoke tests (output is printed, not returned)
# ---------------------------------------------------------------------------


class TestRenderHuman:
    def _call(
        self,
        task_stats: dict | None = None,
        instances: list | None = None,
        recent: list | None = None,
        db_error: str | None = None,
    ) -> None:
        proc = {"status": "running", "pid": "42", "uptime": "1h 2m 3s"}
        registry = {"agent_count": 3, "categories": ["review", "test"]}
        _render_human(
            generated_at="2026-01-01T00:00:00Z",
            proc=proc,
            registry=registry,
            task_stats=task_stats or {},
            instances=instances or [],
            recent=recent or [],
            db_error=db_error,
        )

    def test_runs_without_error_when_empty(self, capsys: pytest.CaptureFixture) -> None:
        self._call()
        out = capsys.readouterr().out
        assert "Aquarco" in out

    def test_shows_supervisor_status(self, capsys: pytest.CaptureFixture) -> None:
        self._call()
        out = capsys.readouterr().out
        assert "running" in out
        assert "42" in out

    def test_shows_db_error_message(self, capsys: pytest.CaptureFixture) -> None:
        self._call(db_error="connection refused")
        out = capsys.readouterr().out
        assert "database unavailable" in out
        assert "connection refused" in out

    def test_shows_task_stats(self, capsys: pytest.CaptureFixture) -> None:
        self._call(task_stats={"pending": 5, "running": 2})
        out = capsys.readouterr().out
        assert "pending" in out
        assert "5" in out

    def test_shows_no_data_when_task_stats_empty(self, capsys: pytest.CaptureFixture) -> None:
        self._call(task_stats={})
        out = capsys.readouterr().out
        assert "no data" in out

    def test_shows_instances(self, capsys: pytest.CaptureFixture) -> None:
        instances = [
            {
                "agent_name": "my-agent",
                "active_count": 1,
                "total_executions": 10,
                "last_execution_at": "2026-01-01T00:00:00Z",
            }
        ]
        self._call(instances=instances)
        out = capsys.readouterr().out
        assert "my-agent" in out

    def test_shows_none_when_instances_empty(self, capsys: pytest.CaptureFixture) -> None:
        self._call(instances=[])
        out = capsys.readouterr().out
        assert "(none)" in out

    def test_shows_recent_tasks(self, capsys: pytest.CaptureFixture) -> None:
        recent = [
            {
                "id": "abc-123",
                "title": "Fix bug",
                "category": "implement",
                "status": "pending",
                "pipeline": "bugfix-pipeline",
                "repository": "my-repo",
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:01:00Z",
            }
        ]
        self._call(recent=recent)
        out = capsys.readouterr().out
        assert "abc-123" in out

    def test_shows_registry_categories(self, capsys: pytest.CaptureFixture) -> None:
        self._call()
        out = capsys.readouterr().out
        assert "review" in out
        assert "test" in out

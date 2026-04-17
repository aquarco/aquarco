"""Tests for the restore command."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aquarco_cli.main import app
from aquarco_cli.vagrant import VagrantError

runner = CliRunner()


def _make_vagrant(is_running: bool = True) -> MagicMock:
    v = MagicMock()
    v.is_running.return_value = is_running
    return v


def _make_backup_dir(root: Path, name: str = "20240101T120000", *, sql: str = "-- dump", creds: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if sql is not None:
        (d / "aquarco.sql").write_text(sql)
    if creds:
        (d / "github-token").write_text("gh-token")
        (d / "credentials.json").write_text('{"token": "claude"}')
    return d


class TestRestoreVmNotRunning:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_exits_when_vm_not_running(self, mock_cls, tmp_path):
        mock_cls.return_value = _make_vagrant(is_running=False)
        result = runner.invoke(app, ["restore", "--from-file", str(tmp_path)])
        assert result.exit_code == 1
        assert "not running" in result.output.lower()

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_no_ssh_calls_when_vm_not_running(self, mock_cls, tmp_path):
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant
        runner.invoke(app, ["restore", "--from-file", str(tmp_path)])
        vagrant.ssh.assert_not_called()


class TestRestoreLatestBackup:
    @patch("aquarco_cli.commands.restore.DEFAULT_BACKUP_ROOT")
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_uses_latest_backup_by_default(self, mock_cls, mock_root, tmp_path):
        mock_root.__str__ = lambda _: str(tmp_path)
        # patch the module-level DEFAULT_BACKUP_ROOT used in _latest_backup
        with patch("aquarco_cli.commands.restore.DEFAULT_BACKUP_ROOT", tmp_path):
            _make_backup_dir(tmp_path, "20240101T100000")
            _make_backup_dir(tmp_path, "20240101T120000")  # latest

            vagrant = _make_vagrant()
            mock_cls.return_value = vagrant

            result = runner.invoke(app, ["restore", "--no-creds"])

            assert result.exit_code == 0
            cmds = [c.kwargs.get("input", "") or "" for c in vagrant.ssh.call_args_list]
            assert any("-- dump" in c for c in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_error_when_no_backups(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant
        with patch("aquarco_cli.commands.restore.DEFAULT_BACKUP_ROOT", tmp_path):
            result = runner.invoke(app, ["restore"])
        assert result.exit_code == 1
        assert "no backups" in result.output.lower()


class TestRestoreFromFile:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_from_file_uses_specified_dir(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 0
        cmds = [c.kwargs.get("input", "") or "" for c in vagrant.ssh.call_args_list]
        assert any("-- dump" in c for c in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_from_file_nonexistent_dir_exits(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(tmp_path / "nonexistent")])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRestoreDatabase:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_pipes_sql_to_psql(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path, sql="INSERT INTO foo VALUES (1);")
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 0
        cmds_inputs = [(c.args[0], c.kwargs.get("input", "")) for c in vagrant.ssh.call_args_list]
        assert any("psql" in cmd and "INSERT INTO foo VALUES (1);" in inp for cmd, inp in cmds_inputs)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_uses_docker_compose_exec(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("docker compose exec -T postgres" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_runs_as_agent_user(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("sudo -u agent" in cmd and "psql" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_missing_sql_file_skips_db_restore(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path, sql=None)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert "skipping" in result.output.lower()
        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert not any("psql" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_failure_exits_nonzero(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        vagrant.ssh.side_effect = VagrantError("psql failed")
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 1


class TestRestoreCredentials:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_credentials_written_to_vm(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        assert result.exit_code == 0
        cmds_inputs = [(c.args[0], c.kwargs.get("input", "")) for c in vagrant.ssh.call_args_list]
        assert any("github-token" in cmd and "gh-token" in inp for cmd, inp in cmds_inputs)
        assert any("credentials.json" in cmd and '"token": "claude"' in inp for cmd, inp in cmds_inputs)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_credentials_restore_runs_as_agent_user(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert all("sudo -u agent" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_credentials_restore_sets_permissions(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert all("chmod 600" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_missing_credential_file_skipped(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path, creds=False)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        assert "skipping" in result.output.lower()
        vagrant.ssh.assert_not_called()


class TestRunMigrationsGracefulSkip:
    """Tests for the graceful skip when db build context is missing on VM."""

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_skips_migrations_when_db_context_missing(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        # First call: test -d check returns "missing"
        # Second call: psql for db restore
        # Third call: psql for clone status reset
        check_result = MagicMock()
        check_result.stdout = "missing"
        vagrant.ssh.side_effect = [
            None,   # restore_db: psql
            None,   # restore_db: clone status reset
            check_result,  # run_migrations: directory check
        ]
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 0
        assert "skipping migrations" in result.output.lower()
        # Should NOT have attempted to run docker compose run --rm migrations
        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert not any("migrations" in cmd and "docker compose run" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_runs_migrations_when_db_context_exists(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        check_result = MagicMock()
        check_result.stdout = "ok"
        vagrant.ssh.side_effect = [
            None,          # restore_db: psql
            None,          # restore_db: clone status reset
            check_result,  # run_migrations: directory check → ok
            None,          # run_migrations: docker compose run --rm migrations
        ]
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 0
        assert "skipping migrations" not in result.output.lower()
        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("docker compose run --rm migrations" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_continues_to_run_migrations_when_check_raises(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        vagrant.ssh.side_effect = [
            None,                          # restore_db: psql
            None,                          # restore_db: clone status reset
            VagrantError("ssh failed"),    # run_migrations: directory check fails
            None,                          # run_migrations: proceeds to run migrations
        ]
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 0
        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("docker compose run --rm migrations" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_skips_migrations_when_stdout_is_none(self, mock_cls, tmp_path):
        """When stdout is None but contains 'missing' after or-default, should pass through."""
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        check_result = MagicMock()
        check_result.stdout = None
        vagrant.ssh.side_effect = [
            None,          # restore_db: psql
            None,          # restore_db: clone status reset
            check_result,  # run_migrations: directory check → stdout is None
            None,          # run_migrations: docker compose run (stdout=None means "" which has no "missing")
        ]
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        # stdout=None → (result.stdout or "") → "" → "missing" not in "" → proceeds to run
        assert result.exit_code == 0
        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("docker compose run --rm migrations" in cmd for cmd in cmds)

    def test_db_context_constant_value(self):
        from aquarco_cli.commands.restore import _DB_CONTEXT
        assert _DB_CONTEXT == "/home/agent/aquarco/db"


class TestRunMigrationsUnit:
    """Direct unit tests for run_migrations function."""

    def test_skip_returns_true(self):
        """When db context is missing, run_migrations returns True (success)."""
        from aquarco_cli.commands.restore import run_migrations

        vagrant = _make_vagrant()
        check_result = MagicMock()
        check_result.stdout = "missing"
        vagrant.ssh.return_value = check_result

        result = run_migrations(vagrant)
        assert result is True

    def test_skip_does_not_run_compose(self):
        """When db context is missing, no docker compose run call is made."""
        from aquarco_cli.commands.restore import run_migrations

        vagrant = _make_vagrant()
        check_result = MagicMock()
        check_result.stdout = "missing"
        vagrant.ssh.return_value = check_result

        run_migrations(vagrant)

        # Only one ssh call: the directory check
        assert vagrant.ssh.call_count == 1
        assert "test -d" in vagrant.ssh.call_args_list[0].args[0]

    def test_check_failure_falls_through_to_migrations(self):
        """When the directory check raises, migrations still run."""
        from aquarco_cli.commands.restore import run_migrations

        vagrant = _make_vagrant()
        vagrant.ssh.side_effect = [
            VagrantError("ssh failed"),  # directory check
            None,                        # migrations run
        ]

        result = run_migrations(vagrant)
        assert result is True
        assert vagrant.ssh.call_count == 2


class TestLatestBackupFunction:
    """Tests for the latest_backup helper in restore.py."""

    def test_returns_most_recent_sorted(self, tmp_path):
        from aquarco_cli.commands.restore import latest_backup
        (tmp_path / "20260101T120000").mkdir()
        (tmp_path / "20260315T080000").mkdir()
        (tmp_path / "20260201T090000").mkdir()
        result = latest_backup(tmp_path)
        assert result == tmp_path / "20260315T080000"

    def test_returns_none_when_no_dirs(self, tmp_path):
        from aquarco_cli.commands.restore import latest_backup
        # Create a file (not a directory) — should be ignored
        (tmp_path / "not-a-dir.txt").touch()
        result = latest_backup(tmp_path)
        assert result is None

    def test_returns_none_when_root_missing(self, tmp_path):
        from aquarco_cli.commands.restore import latest_backup
        result = latest_backup(tmp_path / "nonexistent")
        assert result is None


class TestRestoreSelectiveFlags:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_no_db_skips_database(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert not any("psql" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_no_creds_skips_credentials(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert not any("github-token" in cmd or "credentials.json" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_success_message_shown(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir)])

        assert result.exit_code == 0
        assert "complete" in result.output.lower()

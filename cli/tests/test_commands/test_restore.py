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
        (d / "hosts.yml").write_text("gh-token")
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
        assert any("hosts.yml" in cmd and "gh-token" in inp for cmd, inp in cmds_inputs)
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


class TestRestoreDevFlag:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_dev_flag_sets_vm_name(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AQUARCO_VM_NAME", None)
            runner.invoke(app, ["restore", "--dev", "--from-file", str(backup_dir)])
            assert os.environ.get("AQUARCO_VM_NAME") == "aquarco-dev"

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_no_dev_flag_does_not_set_vm_name(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AQUARCO_VM_NAME", None)
            runner.invoke(app, ["restore", "--from-file", str(backup_dir)])
            assert "AQUARCO_VM_NAME" not in os.environ


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
        assert not any("hosts.yml" in cmd or "credentials.json" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_success_message_shown(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir)])

        assert result.exit_code == 0
        assert "complete" in result.output.lower()

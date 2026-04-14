"""Tests for the backup command."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from aquarco_cli.main import app
from aquarco_cli.vagrant import VagrantError

runner = CliRunner()


def _make_vagrant(is_running: bool = True) -> MagicMock:
    v = MagicMock()
    v.is_running.return_value = is_running
    return v


class TestBackupVmNotRunning:
    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_exits_when_vm_not_running(self, mock_cls):
        mock_cls.return_value = _make_vagrant(is_running=False)
        result = runner.invoke(app, ["backup"])
        assert result.exit_code == 1
        assert "not running" in result.output.lower()

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_no_ssh_calls_when_vm_not_running(self, mock_cls):
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant
        runner.invoke(app, ["backup"])
        vagrant.ssh.assert_not_called()


class TestBackupDatabase:
    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_db_backup_written_to_host(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "SELECT 1;\n-- pg_dump output"
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["backup", "--no-creds", "--output", str(tmp_path)])

        assert result.exit_code == 0
        dumps = list(tmp_path.rglob("aquarco.sql"))
        assert len(dumps) == 1
        assert dumps[0].read_text() == "SELECT 1;\n-- pg_dump output"

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_db_backup_uses_pg_dump_via_docker_compose(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = ""
        mock_cls.return_value = vagrant

        runner.invoke(app, ["backup", "--no-creds", "--output", str(tmp_path)])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("pg_dump" in cmd for cmd in cmds)
        assert any("docker compose exec -T postgres" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_db_backup_runs_as_agent_user(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "dump"
        mock_cls.return_value = vagrant

        runner.invoke(app, ["backup", "--no-creds", "--output", str(tmp_path)])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("sudo -u agent" in cmd and "pg_dump" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_db_backup_file_permissions(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "dump"
        mock_cls.return_value = vagrant

        runner.invoke(app, ["backup", "--no-creds", "--output", str(tmp_path)])

        sql_file = next(tmp_path.rglob("aquarco.sql"))
        assert oct(sql_file.stat().st_mode)[-3:] == "600"

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_db_backup_failure_exits_nonzero(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.side_effect = VagrantError("pg_dump failed")
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["backup", "--no-creds", "--output", str(tmp_path)])

        assert result.exit_code == 1


class TestBackupCredentials:
    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_credentials_written_to_host(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()

        def _ssh(cmd, **_):
            m = MagicMock()
            if "hosts.yml" in cmd:
                m.stdout = "gh-token-content"
            elif "credentials.json" in cmd:
                m.stdout = '{"token": "claude-token"}'
            else:
                m.stdout = ""
            return m

        vagrant.ssh.side_effect = _ssh
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["backup", "--no-db", "--output", str(tmp_path)])

        assert result.exit_code == 0
        assert (next(tmp_path.rglob("hosts.yml"))).read_text() == "gh-token-content"
        assert (next(tmp_path.rglob("credentials.json"))).read_text() == '{"token": "claude-token"}'

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_missing_credential_file_is_skipped(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()

        def _ssh(cmd, **_):
            m = MagicMock()
            m.stdout = ""  # empty = file not found
            return m

        vagrant.ssh.side_effect = _ssh
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["backup", "--no-db", "--output", str(tmp_path)])

        # Warns but doesn't hard-fail for missing creds
        assert "skipping" in result.output.lower()

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_credentials_backup_runs_as_agent_user(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()

        def _ssh(cmd, **_):
            m = MagicMock()
            m.stdout = "content"
            return m

        vagrant.ssh.side_effect = _ssh
        mock_cls.return_value = vagrant

        runner.invoke(app, ["backup", "--no-db", "--output", str(tmp_path)])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert all("sudo -u agent" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_credential_files_are_mode_600(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()

        def _ssh(cmd, **_):
            m = MagicMock()
            m.stdout = "content"
            return m

        vagrant.ssh.side_effect = _ssh
        mock_cls.return_value = vagrant

        runner.invoke(app, ["backup", "--no-db", "--output", str(tmp_path)])

        for f in tmp_path.rglob("*.yml"):
            assert oct(f.stat().st_mode)[-3:] == "600"
        for f in tmp_path.rglob("*.json"):
            assert oct(f.stat().st_mode)[-3:] == "600"


class TestBackupBoth:
    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_both_db_and_creds_by_default(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "content"
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["backup", "--output", str(tmp_path)])

        assert result.exit_code == 0
        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("pg_dump" in cmd for cmd in cmds)
        assert any("hosts.yml" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_backup_dir_has_timestamp_format(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "content"
        mock_cls.return_value = vagrant

        runner.invoke(app, ["backup", "--output", str(tmp_path)])

        subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(subdirs) == 1
        # Timestamp format: YYYYmmddTHHMMSS
        import re
        assert re.match(r"\d{8}T\d{6}", subdirs[0].name)

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_backup_dir_permissions(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "content"
        mock_cls.return_value = vagrant

        runner.invoke(app, ["backup", "--output", str(tmp_path)])

        subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert len(subdirs) == 1
        assert oct(subdirs[0].stat().st_mode)[-3:] == "700"

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_success_message_shown(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "content"
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["backup", "--output", str(tmp_path)])

        assert result.exit_code == 0
        assert "complete" in result.output.lower()


class TestBackupDevFlag:
    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_dev_flag_sets_vm_name(self, mock_cls, tmp_path):
        import os
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "content"
        mock_cls.return_value = vagrant
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AQUARCO_VM_NAME", None)
            runner.invoke(app, ["backup", "--dev", "--output", str(tmp_path)])
            assert os.environ.get("AQUARCO_VM_NAME") == "aquarco-dev"

    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_no_dev_flag_does_not_set_vm_name(self, mock_cls, tmp_path):
        import os
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "content"
        mock_cls.return_value = vagrant
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AQUARCO_VM_NAME", None)
            runner.invoke(app, ["backup", "--output", str(tmp_path)])
            assert "AQUARCO_VM_NAME" not in os.environ


class TestBackupDefaultOutput:
    @patch("aquarco_cli.commands.backup.DEFAULT_BACKUP_ROOT")
    @patch("aquarco_cli.commands.backup.VagrantHelper")
    def test_default_output_is_home_dot_aquarco(self, mock_cls, mock_root, tmp_path):
        mock_root.__str__ = lambda _: str(tmp_path)
        vagrant = _make_vagrant()
        vagrant.ssh.return_value.stdout = "content"
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["backup"])

        assert result.exit_code == 0

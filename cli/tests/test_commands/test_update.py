"""Tests for the update command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestUpdateCommand:
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.subprocess.run")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_success(self, mock_cls, mock_subprocess, mock_health):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_subprocess.return_value.returncode = 0
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "successfully" in result.output.lower()

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_vm_not_running(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        assert "not running" in result.output.lower()

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_dry_run(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--dry-run"])
        assert result.exit_code == 0
        assert "dry run" in result.output.lower()
        # Ensure no SSH commands were executed
        mock_vagrant.ssh.assert_not_called()

    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.subprocess.run")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_skip_migrations(self, mock_cls, mock_subprocess, mock_health):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_subprocess.return_value.returncode = 0
        result = runner.invoke(app, ["update", "--skip-migrations"])
        assert result.exit_code == 0
        # Verify migrations step was not called
        for call in mock_vagrant.ssh.call_args_list:
            cmd = call[0][0] if call[0] else call[1].get("command", "")
            assert "migrations" not in cmd

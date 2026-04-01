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

    @patch("aquarco_cli.commands.update.print_health_table", return_value=False)
    @patch("aquarco_cli.commands.update.subprocess.run")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_unhealthy_warns(self, mock_cls, mock_subprocess, mock_health):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_subprocess.return_value.returncode = 0
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "unhealthy" in result.output.lower()

    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.subprocess.run")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_git_pull_failure_continues(self, mock_cls, mock_subprocess, mock_health):
        """git pull failing should warn but continue."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        import subprocess as sp
        mock_subprocess.side_effect = sp.CalledProcessError(1, "git", stderr="merge conflict")
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        # ssh steps should still be called
        assert mock_vagrant.ssh.called

    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.subprocess.run")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_ssh_step_failure_continues(self, mock_cls, mock_subprocess, mock_health):
        """SSH step failure should continue to next step."""
        from aquarco_cli.vagrant import VagrantError
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = VagrantError("step failed")
        mock_subprocess.return_value.returncode = 0
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "continuing" in result.output.lower()

    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.subprocess.run")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_skip_provision(self, mock_cls, mock_subprocess, mock_health):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_subprocess.return_value.returncode = 0
        result = runner.invoke(app, ["update", "--skip-provision"])
        assert result.exit_code == 0
        mock_vagrant.provision.assert_not_called()

    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.subprocess.run")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_provision_failure_warns(self, mock_cls, mock_subprocess, mock_health):
        from aquarco_cli.vagrant import VagrantError
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.provision.side_effect = VagrantError("provision failed")
        mock_subprocess.return_value.returncode = 0
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "provisioning failed" in result.output.lower()

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_dry_run_shows_provision_step(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--dry-run"])
        assert result.exit_code == 0
        assert "provision" in result.output.lower()
        assert "health" in result.output.lower()

"""Tests for the vm start/stop/destroy commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from aquarco_cli.main import app
from aquarco_cli.vagrant import VagrantError

runner = CliRunner()


def _make_vagrant(is_running: bool = True) -> MagicMock:
    v = MagicMock()
    v.is_running.return_value = is_running
    return v


# ── Start ────────────────────────────────────────────────────────────


class TestStartCommand:
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_start_when_vm_not_running(self, mock_cls):
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["start"])

        assert result.exit_code == 0
        vagrant.up.assert_called_once()
        assert "running" in result.output.lower()

    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_start_when_vm_already_running(self, mock_cls):
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["start"])

        assert result.exit_code == 0
        vagrant.up.assert_not_called()
        assert "already running" in result.output.lower()

    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_start_failure_exits_nonzero(self, mock_cls):
        vagrant = _make_vagrant(is_running=False)
        vagrant.up.side_effect = VagrantError("vagrant up failed")
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["start"])

        assert result.exit_code == 1
        assert "failed" in result.output.lower()


# ── Stop ─────────────────────────────────────────────────────────────


class TestStopCommand:
    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_stop_halts_running_vm(self, mock_cls, mock_backup):
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0
        vagrant.halt.assert_called_once()
        mock_backup.assert_called_once_with(vagrant)
        assert "stopped" in result.output.lower()

    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_stop_when_vm_already_stopped(self, mock_cls):
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["stop"])

        assert result.exit_code == 0
        vagrant.halt.assert_not_called()
        assert "already stopped" in result.output.lower()

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_stop_no_backup_skips_backup(self, mock_cls, mock_backup):
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["stop", "--no-backup"])

        assert result.exit_code == 0
        mock_backup.assert_not_called()
        vagrant.halt.assert_called_once()

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_stop_default_runs_backup(self, mock_cls, mock_backup):
        """Without --no-backup, backup is performed before stopping."""
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant

        runner.invoke(app, ["stop"])

        mock_backup.assert_called_once_with(vagrant)

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_stop_backup_error_prompts_confirmation(self, mock_cls, mock_backup):
        """When backup fails, user is prompted whether to stop anyway."""
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant
        mock_backup.side_effect = typer.Exit(code=1)

        # User answers "y" to "Stop anyway?"
        result = runner.invoke(app, ["stop"], input="y\n")

        assert result.exit_code == 0
        vagrant.halt.assert_called_once()

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_stop_backup_error_abort_on_no(self, mock_cls, mock_backup):
        """When backup fails and user declines, stop is aborted."""
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant
        mock_backup.side_effect = typer.Exit(code=1)

        result = runner.invoke(app, ["stop"], input="n\n")

        assert result.exit_code != 0
        vagrant.halt.assert_not_called()

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_stop_halt_failure_exits_nonzero(self, mock_cls, mock_backup):
        vagrant = _make_vagrant(is_running=True)
        vagrant.halt.side_effect = VagrantError("halt failed")
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["stop", "--no-backup"])

        assert result.exit_code == 1
        assert "failed" in result.output.lower()


# ── Destroy ──────────────────────────────────────────────────────────


class TestDestroyCommand:
    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_with_yes_flag(self, mock_cls, mock_backup):
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["destroy", "--yes"])

        assert result.exit_code == 0
        vagrant.destroy.assert_called_once()
        mock_backup.assert_called_once_with(vagrant)
        assert "destroyed" in result.output.lower()

    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_prompts_confirmation(self, mock_cls):
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant

        # User confirms with "y"
        result = runner.invoke(app, ["destroy"], input="y\n")

        assert result.exit_code == 0
        vagrant.destroy.assert_called_once()

    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_abort_on_no(self, mock_cls):
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["destroy"], input="n\n")

        assert result.exit_code != 0
        vagrant.destroy.assert_not_called()

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_no_backup_skips_backup(self, mock_cls, mock_backup):
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["destroy", "--yes", "--no-backup"])

        assert result.exit_code == 0
        mock_backup.assert_not_called()
        vagrant.destroy.assert_called_once()

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_default_runs_backup_when_running(self, mock_cls, mock_backup):
        """Without --no-backup, backup runs if VM is running."""
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant

        runner.invoke(app, ["destroy", "--yes"])

        mock_backup.assert_called_once_with(vagrant)

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_skips_backup_when_vm_not_running(self, mock_cls, mock_backup):
        """Backup is skipped when VM is not running, even without --no-backup."""
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant

        runner.invoke(app, ["destroy", "--yes"])

        mock_backup.assert_not_called()

    @patch("aquarco_cli.commands.vm.perform_backup")
    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_backup_error_prompts_confirmation(self, mock_cls, mock_backup):
        vagrant = _make_vagrant(is_running=True)
        mock_cls.return_value = vagrant
        mock_backup.side_effect = typer.Exit(code=1)

        # User confirms destroy despite backup error
        result = runner.invoke(app, ["destroy", "--yes"], input="y\n")

        assert result.exit_code == 0
        vagrant.destroy.assert_called_once()

    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_failure_exits_nonzero(self, mock_cls):
        vagrant = _make_vagrant(is_running=False)
        vagrant.destroy.side_effect = VagrantError("destroy failed")
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["destroy", "--yes"])

        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    @patch("aquarco_cli.commands.vm.VagrantHelper")
    def test_destroy_short_yes_flag(self, mock_cls):
        """The -y short flag works as an alias for --yes."""
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["destroy", "-y"])

        assert result.exit_code == 0
        vagrant.destroy.assert_called_once()

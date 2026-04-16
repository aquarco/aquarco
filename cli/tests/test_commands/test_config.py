"""Tests for the config commands (update / export)."""

from __future__ import annotations

import os
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from aquarco_cli.commands.config import _DEV_VM_NAME, _SUPERVISOR_CMD, _SUPERVISOR_CONFIG, _run
from aquarco_cli.main import app

runner = CliRunner()


class TestRunHelper:
    """Unit tests for the _run() helper."""

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_calls_ssh_with_update_command(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.return_value = CompletedProcess(args=[], returncode=0)

        _run("update", dev=False)

        expected_cmd = _SUPERVISOR_CMD.format(subcommand="update", config=_SUPERVISOR_CONFIG)
        mock_vagrant.ssh.assert_called_once_with(expected_cmd, stream=True)

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_calls_ssh_with_export_command(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.return_value = CompletedProcess(args=[], returncode=0)

        _run("export", dev=False)

        expected_cmd = _SUPERVISOR_CMD.format(subcommand="export", config=_SUPERVISOR_CONFIG)
        mock_vagrant.ssh.assert_called_once_with(expected_cmd, stream=True)

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_dev_flag_passes_vm_name(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.return_value = CompletedProcess(args=[], returncode=0)

        _run("update", dev=True)

        mock_cls.assert_called_once_with(vm_name=_DEV_VM_NAME)

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_no_dev_flag_passes_empty_vm_name(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.return_value = CompletedProcess(args=[], returncode=0)

        _run("update", dev=False)

        mock_cls.assert_called_once_with(vm_name="")

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_dev_flag_does_not_mutate_os_environ(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.return_value = CompletedProcess(args=[], returncode=0)

        env_before = os.environ.copy()
        _run("update", dev=True)
        # os.environ should not have AQUARCO_VM_NAME added as a side effect
        assert os.environ.get("AQUARCO_VM_NAME") == env_before.get("AQUARCO_VM_NAME")

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_vm_not_running_exits_with_code_1(self, mock_cls: MagicMock) -> None:
        from click.exceptions import Exit

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False

        with pytest.raises(Exit) as exc_info:
            _run("update", dev=False)
        assert exc_info.value.exit_code == 1


class TestConfigCLIIntegration:
    """Integration tests via the Typer CLI runner."""

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_config_update_success(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.return_value = CompletedProcess(args=[], returncode=0)

        result = runner.invoke(app, ["config", "update"])
        assert result.exit_code == 0

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_config_export_success(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.return_value = CompletedProcess(args=[], returncode=0)

        result = runner.invoke(app, ["config", "export"])
        assert result.exit_code == 0

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_config_update_vm_not_running(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False

        result = runner.invoke(app, ["config", "update"])
        assert result.exit_code == 1

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_config_update_dev_flag(self, mock_cls: MagicMock) -> None:
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.return_value = CompletedProcess(args=[], returncode=0)

        result = runner.invoke(app, ["config", "update", "--dev"])
        assert result.exit_code == 0
        mock_cls.assert_called_once_with(vm_name=_DEV_VM_NAME)

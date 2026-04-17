"""Tests for the config command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app
from aquarco_cli.vagrant import LOAD_SUPERVISOR_SECRETS

runner = CliRunner()


class TestConfigUsesSupervsorSecrets:
    """Config command must use LOAD_SUPERVISOR_SECRETS, not LOAD_SECRETS."""

    def test_supervisor_cmd_contains_supervisor_secrets(self):
        from aquarco_cli.commands.config import _SUPERVISOR_CMD
        assert "secrets.env" in _SUPERVISOR_CMD
        # Must use the supervisor secrets, not docker-secrets
        assert LOAD_SUPERVISOR_SECRETS.split(";")[1].strip() in _SUPERVISOR_CMD

    def test_supervisor_cmd_uses_venv_path(self):
        from aquarco_cli.commands.config import _SUPERVISOR_CMD
        assert "/home/agent/.venv/bin/aquarco-supervisor" in _SUPERVISOR_CMD


class TestConfigUpdate:
    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_update_vm_not_running(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False
        result = runner.invoke(app, ["config", "update"])
        assert result.exit_code == 1
        assert "not running" in result.output.lower()

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_update_success(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["config", "update"])
        assert result.exit_code == 0
        mock_vagrant.ssh.assert_called_once()
        cmd = mock_vagrant.ssh.call_args[0][0]
        assert "update" in cmd


class TestConfigExport:
    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_export_vm_not_running(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False
        result = runner.invoke(app, ["config", "export"])
        assert result.exit_code == 1

    @patch("aquarco_cli.commands.config.VagrantHelper")
    def test_export_success(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["config", "export"])
        assert result.exit_code == 0
        mock_vagrant.ssh.assert_called_once()
        cmd = mock_vagrant.ssh.call_args[0][0]
        assert "export" in cmd

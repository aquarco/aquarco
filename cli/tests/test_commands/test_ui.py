"""Tests for the ui command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestUiStart:
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_start(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 0
        mock_vagrant.ssh.assert_called_once()
        assert "running" in result.output.lower()

    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_start_vm_not_running(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 1
        assert "not running" in result.output.lower()

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_start_with_open(self, mock_cls, mock_browser):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "--open"])
        assert result.exit_code == 0
        mock_browser.assert_called_once_with("http://localhost:8080")


class TestUiStop:
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_stop(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "stop"])
        assert result.exit_code == 0
        assert "stopped" in result.output.lower()

    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_stop_vm_not_running(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False
        result = runner.invoke(app, ["ui", "stop"])
        assert result.exit_code == 1
        assert "not running" in result.output.lower()

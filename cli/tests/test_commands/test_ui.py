"""Tests for the ui command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestUiStart:
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_start(self, mock_cls, mock_config):
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "--no-open"])
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
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_start_opens_browser_by_default(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 0
        mock_browser.assert_called_once_with("http://localhost:8080")

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_no_open_suppresses_browser(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "--no-open"])
        assert result.exit_code == 0
        mock_browser.assert_not_called()


class TestUiWeb:
    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_web(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "web", "--no-open"])
        assert result.exit_code == 0
        mock_browser.assert_not_called()
        assert "running" in result.output.lower()


class TestUiDb:
    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_db(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 9090
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        assert result.exit_code == 0
        assert "9090/adminer" in result.output


class TestUiApi:
    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_api(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "api", "--no-open"])
        assert result.exit_code == 0
        assert "graphql" in result.output.lower()


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

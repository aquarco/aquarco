"""Tests for the install command."""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestInstallCommand:
    @patch("aquarco_cli.commands.install.shutil.which")
    def test_missing_virtualbox(self, mock_which):
        mock_which.side_effect = lambda b: None if b == "VBoxManage" else "/usr/bin/vagrant"
        result = runner.invoke(app, ["install"])
        assert result.exit_code == 1
        assert "VirtualBox not found" in result.output

    @patch("aquarco_cli.commands.install.shutil.which")
    def test_missing_vagrant(self, mock_which):
        mock_which.side_effect = lambda b: "/usr/bin/VBoxManage" if b == "VBoxManage" else None
        result = runner.invoke(app, ["install"])
        assert result.exit_code == 1
        assert "Vagrant not found" in result.output

    @patch("aquarco_cli.commands.install.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.install.VagrantHelper")
    @patch("aquarco_cli.commands.install.shutil.which", return_value="/usr/bin/mock")
    def test_successful_install(self, mock_which, mock_vagrant_cls, mock_health):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        result = runner.invoke(app, ["install"])
        assert result.exit_code == 0
        mock_vagrant.up.assert_called_once_with(provision=True)
        assert "successfully" in result.output.lower()

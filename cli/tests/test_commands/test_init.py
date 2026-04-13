"""Tests for the init command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestInitCommand:
    @patch("aquarco_cli.commands.init.shutil.which")
    def test_missing_virtualbox(self, mock_which):
        mock_which.side_effect = lambda b: None if b == "VBoxManage" else "/usr/bin/vagrant"
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert "VirtualBox not found" in result.output

    @patch("aquarco_cli.commands.init.shutil.which")
    def test_missing_vagrant(self, mock_which):
        mock_which.side_effect = lambda b: "/usr/bin/VBoxManage" if b == "VBoxManage" else None
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert "Vagrant not found" in result.output

    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_successful_init(self, mock_which, mock_vagrant_cls, mock_health):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        mock_vagrant.up.assert_called_once_with(provision=True)
        assert "successfully" in result.output.lower()

    @patch("aquarco_cli.commands.init.print_health_table", return_value=False)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_init_unhealthy(self, mock_which, mock_vagrant_cls, mock_health):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert "not healthy" in result.output.lower()

    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_init_vagrant_up_failure(self, mock_which, mock_vagrant_cls):
        from aquarco_cli.vagrant import VagrantError
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        mock_vagrant.up.side_effect = VagrantError("VM failed to start")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()

    @patch("aquarco_cli.commands.init.shutil.which")
    def test_both_missing(self, mock_which):
        mock_which.return_value = None
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert "VirtualBox" in result.output
        assert "Vagrant" in result.output


class TestInitFromBackup:
    @patch("aquarco_cli.commands.init._find_latest_backup")
    @patch("aquarco_cli.commands.init._restore_from_backup", return_value=True)
    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_from_backup_latest(self, mock_which, mock_vagrant_cls, mock_health, mock_restore, mock_find):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        mock_find.return_value = Path("/fake/backups/20260101T120000")
        result = runner.invoke(app, ["init", "--from-backup", "latest"])
        assert result.exit_code == 0
        mock_find.assert_called_once()
        mock_restore.assert_called_once_with(mock_vagrant, Path("/fake/backups/20260101T120000"))

    @patch("aquarco_cli.commands.init._restore_from_backup", return_value=True)
    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_from_backup_explicit_path(self, mock_which, mock_vagrant_cls, mock_health, mock_restore, tmp_path):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        result = runner.invoke(app, ["init", "--from-backup", str(tmp_path)])
        assert result.exit_code == 0
        mock_restore.assert_called_once_with(mock_vagrant, tmp_path)

    @patch("aquarco_cli.commands.init._restore_from_backup", return_value=True)
    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_from_backup_nonexistent_path(self, mock_which, mock_vagrant_cls, mock_health, mock_restore):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        result = runner.invoke(app, ["init", "--from-backup", "/nonexistent/backup"])
        assert result.exit_code == 1
        assert "not found" in result.output
        mock_restore.assert_not_called()

    @patch("aquarco_cli.commands.init._find_latest_backup", return_value=None)
    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_from_backup_no_backups_found(self, mock_which, mock_vagrant_cls, mock_health, mock_find):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        result = runner.invoke(app, ["init", "--from-backup", "latest"])
        assert result.exit_code == 1
        assert "No backups found" in result.output

    @patch("aquarco_cli.commands.init._restore_from_backup", return_value=False)
    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_from_backup_restore_errors(self, mock_which, mock_vagrant_cls, mock_health, mock_restore, tmp_path):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        result = runner.invoke(app, ["init", "--from-backup", str(tmp_path)])
        assert result.exit_code == 1
        assert "errors" in result.output

    @patch("aquarco_cli.commands.init._restore_from_backup")
    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    def test_no_from_backup_skips_restore(self, mock_which, mock_vagrant_cls, mock_health, mock_restore):
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        mock_restore.assert_not_called()


class TestFindLatestBackup:
    def test_returns_most_recent(self, tmp_path):
        from aquarco_cli.commands.init import _find_latest_backup
        (tmp_path / "20260101T120000").mkdir()
        (tmp_path / "20260315T080000").mkdir()
        (tmp_path / "20260201T090000").mkdir()
        with patch("aquarco_cli.commands.init.DEFAULT_BACKUP_ROOT", tmp_path):
            result = _find_latest_backup()
        assert result == tmp_path / "20260315T080000"

    def test_returns_none_when_empty(self, tmp_path):
        from aquarco_cli.commands.init import _find_latest_backup
        with patch("aquarco_cli.commands.init.DEFAULT_BACKUP_ROOT", tmp_path):
            result = _find_latest_backup()
        assert result is None

    def test_returns_none_when_dir_missing(self, tmp_path):
        from aquarco_cli.commands.init import _find_latest_backup
        with patch("aquarco_cli.commands.init.DEFAULT_BACKUP_ROOT", tmp_path / "nonexistent"):
            result = _find_latest_backup()
        assert result is None

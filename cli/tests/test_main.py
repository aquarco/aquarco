"""Tests for the main CLI entry point."""

from __future__ import annotations

from typer.testing import CliRunner

from aquarco_cli import __version__
from aquarco_cli.main import app

runner = CliRunner()


class TestMainApp:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_version_short_flag(self):
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        # Typer returns exit code 0 or 2 for no_args_is_help
        assert result.exit_code in (0, 2)
        assert "aquarco" in result.output.lower()

    def test_help_flag(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "install" in result.output
        assert "update" in result.output
        assert "auth" in result.output
        assert "watch" in result.output
        assert "run" in result.output
        assert "status" in result.output
        assert "ui" in result.output

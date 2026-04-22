"""Tests for the main CLI entry point."""

from __future__ import annotations

import subprocess

from typer.testing import CliRunner

from aquarco_cli import __version__
from aquarco_cli.main import app

runner = CliRunner()


class TestMainApp:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "aquarco" in result.output

    def test_version_short_flag(self):
        result = runner.invoke(app, ["-v"])
        assert result.exit_code == 0
        assert "aquarco" in result.output

    def test_version_old_short_flag_rejected(self):
        """The old short flag `-V` should no longer be accepted."""
        result = runner.invoke(app, ["-V"])
        assert result.exit_code != 0

    def test_version_dev_mode_git_available(self, monkeypatch):
        """Development build with git available returns local-dev <branch>@<hash>."""
        monkeypatch.setattr("aquarco_cli.main.BUILD_TYPE", "development")

        def fake_check_output(cmd, *args, **kwargs):
            # Expect two calls: abbrev-ref HEAD and --short HEAD
            if "--abbrev-ref" in cmd:
                return "main\n"
            if "--short" in cmd:
                return "abc1234\n"
            raise AssertionError(f"unexpected git invocation: {cmd}")

        monkeypatch.setattr(
            "aquarco_cli.main.subprocess.check_output", fake_check_output
        )
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "aquarco local-dev main@abc1234" in result.output

    def test_version_dev_mode_git_unavailable(self, monkeypatch):
        """Development build without git falls back to `local-dev unknown`."""
        monkeypatch.setattr("aquarco_cli.main.BUILD_TYPE", "development")

        def fake_check_output(cmd, *args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(
            "aquarco_cli.main.subprocess.check_output", fake_check_output
        )
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "aquarco local-dev unknown" in result.output

    def test_version_dev_mode_git_error(self, monkeypatch):
        """Development build where git errors (e.g. not a repo) falls back to `local-dev unknown`."""
        monkeypatch.setattr("aquarco_cli.main.BUILD_TYPE", "development")

        def fake_check_output(cmd, *args, **kwargs):
            raise subprocess.CalledProcessError(128, cmd)

        monkeypatch.setattr(
            "aquarco_cli.main.subprocess.check_output", fake_check_output
        )
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "aquarco local-dev unknown" in result.output

    def test_version_dev_mode_git_empty_output(self, monkeypatch):
        """Development build where git returns empty branch/commit falls back to `local-dev unknown`.

        Exercises the defensive ``if not branch or not commit`` guard in
        ``_get_dev_version()`` — e.g. a freshly ``git init``-ed repo with no
        commits could surface empty output.
        """
        monkeypatch.setattr("aquarco_cli.main.BUILD_TYPE", "development")

        def fake_check_output(cmd, *args, **kwargs):
            return "\n"

        monkeypatch.setattr(
            "aquarco_cli.main.subprocess.check_output", fake_check_output
        )
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "aquarco local-dev unknown" in result.output

    def test_version_dev_mode_git_runs_in_package_dir(self, monkeypatch):
        """`_get_dev_version()` must anchor git to the package directory, not CWD.

        This prevents editable installs from reporting the wrong repo's
        branch/hash when the user runs ``aquarco --version`` from an unrelated
        git repository.
        """
        import aquarco_cli.main as main_mod

        monkeypatch.setattr("aquarco_cli.main.BUILD_TYPE", "development")
        captured_cwds: list[object] = []

        def fake_check_output(cmd, *args, **kwargs):
            captured_cwds.append(kwargs.get("cwd"))
            if "--abbrev-ref" in cmd:
                return "feature-branch\n"
            if "--short" in cmd:
                return "deadbee\n"
            raise AssertionError(f"unexpected git invocation: {cmd}")

        monkeypatch.setattr(
            "aquarco_cli.main.subprocess.check_output", fake_check_output
        )
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "aquarco local-dev feature-branch@deadbee" in result.output
        # Both subprocess calls must pass cwd=<package directory>.
        assert len(captured_cwds) == 2
        assert all(cwd == main_mod._PACKAGE_DIR for cwd in captured_cwds)

    def test_version_production_mode(self, monkeypatch):
        """Production build outputs the static `__version__`."""
        monkeypatch.setattr("aquarco_cli.main.BUILD_TYPE", "production")
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert f"aquarco {__version__}" in result.output

    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        # Typer returns exit code 0 or 2 for no_args_is_help
        assert result.exit_code in (0, 2)
        assert "aquarco" in result.output.lower()

    def test_help_flag(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "update" in result.output
        assert "auth" in result.output
        assert "repos" in result.output
        assert "run" in result.output
        assert "status" in result.output
        assert "ui" in result.output

    def test_h_alias(self):
        result = runner.invoke(app, ["-h"])
        assert result.exit_code == 0
        assert "init" in result.output

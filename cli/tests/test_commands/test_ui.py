"""Tests for the ui command."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from aquarco_cli.main import app
from aquarco_cli.vagrant import VagrantError

runner = CliRunner()


def _make_db_ssh_side_effect(password_stdout: str | None = "POSTGRES_PASSWORD=s3cret"):
    """Return an ssh.side_effect that mimics the three SSH calls made by `ui db`.

    The calls in order are:
      1. get_compose_prefix — reads /etc/aquarco/env
      2. docker compose up -d adminer postgres
      3. sudo grep POSTGRES_PASSWORD=... (only reached if the first two succeed)

    If ``password_stdout`` is None the third call raises VagrantError so the
    fallback warning path can be exercised.
    """
    calls = [
        # 1. get_compose_prefix probes env
        MagicMock(stdout="development\n", stderr=""),
        # 2. compose up (stdout not used)
        MagicMock(stdout="", stderr=""),
    ]

    def _side_effect(cmd, *args, **kwargs):
        if "/etc/aquarco/docker-secrets.env" in cmd and "grep" in cmd:
            if password_stdout is None:
                raise VagrantError("secrets file missing")
            return MagicMock(stdout=password_stdout, stderr="")
        return calls.pop(0) if calls else MagicMock(stdout="", stderr="")

    return _side_effect


class TestUiStart:
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_start(self, mock_cls, mock_config):
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "--no-open"])
        assert result.exit_code == 0
        # get_compose_prefix makes 1 SSH call + the compose up call = 2 total
        assert mock_vagrant.ssh.call_count == 2
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
        mock_vagrant.ssh.side_effect = _make_db_ssh_side_effect()
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        assert result.exit_code == 0
        assert "9090/adminer" in result.output

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_db_prints_adminer_credentials(self, mock_cls, mock_config, mock_browser):
        """`ui db` must print Server/Database/Username/Password so the user can
        paste them into the Adminer login form."""
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = _make_db_ssh_side_effect(
            password_stdout="POSTGRES_PASSWORD=s3cret",
        )
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        assert result.exit_code == 0
        assert "Server:   postgres" in result.output
        assert "Database: aquarco" in result.output
        assert "Username: aquarco" in result.output
        assert "Password: s3cret" in result.output

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_db_warns_when_password_unreadable(self, mock_cls, mock_config, mock_browser):
        """When the secrets file read fails, the command warns but does not exit."""
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = _make_db_ssh_side_effect(password_stdout=None)
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        assert result.exit_code == 0
        # Credentials block still prints the known fields...
        assert "Server:   postgres" in result.output
        assert "Username: aquarco" in result.output
        # ...and a warning replaces the password line.
        assert "could not read" in result.output.lower()


class TestUiDbHelpDocumentation:
    """The `ui db` help text must warn the user that the password is printed
    in plaintext, so users are aware before recording terminals or screen
    shares."""

    def test_help_mentions_plaintext_password(self):
        result = runner.invoke(app, ["ui", "db", "--help"])
        assert result.exit_code == 0
        # Help text must flag the plaintext-password behavior so the user is
        # not surprised by it showing up in terminal recordings / screenshots.
        assert "plaintext" in result.output.lower()

    def test_help_mentions_local_developer_scope(self):
        result = runner.invoke(app, ["ui", "db", "--help"])
        assert result.exit_code == 0
        # Clarifies that the credentials display is for local dev, not prod.
        assert "local" in result.output.lower()


class TestUiDbExceptionHandling:
    """The password-read block narrowed its exception handler from bare
    ``except Exception`` to ``(VagrantError, CalledProcessError, OSError)``.
    Ensure unexpected exception types propagate so they are not silently
    swallowed (e.g. KeyboardInterrupt, programming errors)."""

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_unexpected_exception_propagates(self, mock_cls, mock_config, mock_browser):
        """A TypeError from a programming error must NOT be swallowed."""
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True

        calls = {"n": 0}

        def _ssh(cmd, *args, **kwargs):
            calls["n"] += 1
            # 1st = compose prefix probe, 2nd = compose up
            if calls["n"] <= 2:
                return MagicMock(stdout="development\n" if calls["n"] == 1 else "", stderr="")
            # 3rd = password read — raise a type not in the narrowed tuple
            raise TypeError("unexpected bug")

        mock_vagrant.ssh.side_effect = _ssh
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        # TypeError is outside the narrowed except clause; must propagate.
        assert result.exit_code != 0
        assert isinstance(result.exception, TypeError)

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_oserror_is_handled(self, mock_cls, mock_config, mock_browser):
        """OSError (e.g. SSH socket failure) during password read must be
        caught and produce the fallback warning."""
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True

        calls = {"n": 0}

        def _ssh(cmd, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                return MagicMock(stdout="development\n" if calls["n"] == 1 else "", stderr="")
            raise OSError("connection reset")

        mock_vagrant.ssh.side_effect = _ssh
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        assert result.exit_code == 0
        assert "could not read" in result.output.lower()


class TestUiDbPasswordParsing:
    """Verify the password is correctly extracted from the env-file line."""

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_password_with_trailing_whitespace_is_stripped(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = _make_db_ssh_side_effect(
            password_stdout="POSTGRES_PASSWORD=hunter2   \n",
        )
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        assert result.exit_code == 0
        assert "Password: hunter2" in result.output
        # Ensure trailing whitespace from SSH stdout is stripped.
        assert "hunter2   " not in result.output

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_empty_password_line_triggers_warning(self, mock_cls, mock_config, mock_browser):
        """If grep returns an empty line (key present with empty value), the
        falsy check in ui.py triggers the fallback warning."""
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = _make_db_ssh_side_effect(
            password_stdout="POSTGRES_PASSWORD=\n",  # key present, empty value
        )
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        assert result.exit_code == 0
        # Empty password → falsy → fallback warning path.
        assert "could not read" in result.output.lower()


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

"""Tests for issue #71 — Aquarco CLI enhancements.

Covers acceptance criteria from the design:
  1. -h alias on main app and sub-apps
  2. auth bare invocation auto-detect flow
  3. --json output for auth status and repos list
  4. ui subcommands URL display and --no-open
  5. Drain mode three-way prompt (yes/no/plan) and pending drain (keep/now/cancel)
  6. init --port persistence
  7. No stale references to watch/install commands
  8. Graceful error when VM is not running (httpx.ConnectError guard)
  9. --open default for ui (item 12)
 10. Renamed commands (repos, init) work end-to-end
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Item 1: -h alias on sub-apps
# ---------------------------------------------------------------------------


class TestHelpAliasOnSubApps:
    """context_settings -h alias tested on sub-apps."""

    def test_auth_h_alias(self):
        result = runner.invoke(app, ["auth", "-h"])
        assert result.exit_code == 0
        assert "status" in result.output.lower()

    def test_repos_h_alias(self):
        result = runner.invoke(app, ["repos", "-h"])
        assert result.exit_code == 0
        assert "add" in result.output.lower()

    def test_ui_h_alias(self):
        result = runner.invoke(app, ["ui", "-h"])
        assert result.exit_code == 0

    def test_update_h_alias(self):
        result = runner.invoke(app, ["update", "-h"])
        assert result.exit_code == 0

    def test_init_h_alias(self):
        result = runner.invoke(app, ["init", "-h"])
        assert result.exit_code == 0

    def test_status_h_alias(self):
        result = runner.invoke(app, ["status", "-h"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Item 5: Smart aquarco auth (bare invocation auto-detect)
# ---------------------------------------------------------------------------


class TestAuthBareInvocation:
    """Bare 'aquarco auth' auto-detects unauthenticated services."""

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_both_authenticated_shows_status(self, mock_cls):
        """When both services authenticated, shows info and status table."""
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            # callback checks
            {"claudeAuthStatus": {"authenticated": True, "email": "a@b.com"}},
            {"githubAuthStatus": {"authenticated": True, "username": "user"}},
            # status subcommand
            {"claudeAuthStatus": {"authenticated": True, "email": "a@b.com"}},
            {"githubAuthStatus": {"authenticated": True, "username": "user"}},
        ]
        result = runner.invoke(app, ["auth"])
        assert result.exit_code == 0
        assert "already authenticated" in result.output.lower()

    @patch("aquarco_cli.commands.auth.webbrowser.open")
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_claude_unauthenticated_triggers_login(self, mock_cls, mock_browser):
        """When Claude not authenticated, runs Claude login flow."""
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            # callback checks
            {"claudeAuthStatus": {"authenticated": False, "email": None}},
            {"githubAuthStatus": {"authenticated": True, "username": "user"}},
            # Claude login flow
            {"claudeLoginStart": {"authorizeUrl": "https://auth.example.com", "expiresIn": 300}},
            {"claudeSubmitCode": {"success": True, "email": "new@test.com", "error": None}},
            # final status
            {"claudeAuthStatus": {"authenticated": True, "email": "new@test.com"}},
            {"githubAuthStatus": {"authenticated": True, "username": "user"}},
        ]
        result = runner.invoke(app, ["auth"], input="auth-code-123\n")
        assert result.exit_code == 0
        assert "claude is not authenticated" in result.output.lower()

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_auth_callback_connection_error(self, mock_cls):
        """When API unreachable, shows friendly error."""
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["auth"])
        assert result.exit_code == 1

    @patch("aquarco_cli.commands.auth.webbrowser.open")
    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_claude_login_failure_continues_to_github(self, mock_cls, mock_browser):
        """If Claude login fails, GitHub login flow still runs."""
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            # callback checks
            {"claudeAuthStatus": {"authenticated": False, "email": None}},
            {"githubAuthStatus": {"authenticated": False, "username": None}},
            # Claude login start fails
            httpx.ConnectError("refused during claude login"),
            # GitHub login flow
            {"githubLoginStart": {"userCode": "XY-1234", "verificationUri": "https://github.com/login/device", "expiresIn": 900}},
            {"githubLoginPoll": {"success": True, "username": "ghuser", "error": None}},
            # final status
            {"claudeAuthStatus": {"authenticated": False, "email": None}},
            {"githubAuthStatus": {"authenticated": True, "username": "ghuser"}},
        ]
        result = runner.invoke(app, ["auth"])
        # Should not crash — should continue to GitHub flow
        assert "continuing" in result.output.lower() or "github" in result.output.lower()


# ---------------------------------------------------------------------------
# Item 11: --json on auth status and repos list
# ---------------------------------------------------------------------------


class TestAuthStatusJson:
    """--json flag on auth status outputs JSON."""

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_auth_status_json_output(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"claudeAuthStatus": {"authenticated": True, "email": "me@test.com"}},
            {"githubAuthStatus": {"authenticated": False, "username": None}},
        ]
        result = runner.invoke(app, ["auth", "status", "--json"])
        assert result.exit_code == 0
        assert "claudeAuthStatus" in result.output
        assert "githubAuthStatus" in result.output

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_auth_status_json_parseable(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.side_effect = [
            {"claudeAuthStatus": {"authenticated": True, "email": "e@t.com"}},
            {"githubAuthStatus": {"authenticated": True, "username": "u"}},
        ]
        result = runner.invoke(app, ["auth", "status", "--json"])
        assert result.exit_code == 0
        # Output should be valid JSON (Rich may add formatting, but content should parse)
        output = result.output.strip()
        parsed = json.loads(output)
        assert parsed["claudeAuthStatus"]["authenticated"] is True


class TestReposListJson:
    """--json flag on repos list outputs JSON."""

    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_repos_list_json(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {
            "repositories": [
                {"name": "r1", "url": "https://github.com/u/r1", "branch": "main",
                 "cloneStatus": "READY", "pollers": ["github-tasks"], "taskCount": 2,
                 "lastClonedAt": None, "lastPulledAt": None, "headSha": None, "errorMessage": None},
            ]
        }
        result = runner.invoke(app, ["repos", "list", "--json"])
        assert result.exit_code == 0
        assert "repositories" in result.output

    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_repos_list_json_empty(self, mock_cls):
        mock_client = mock_cls.return_value
        mock_client.execute.return_value = {"repositories": []}
        result = runner.invoke(app, ["repos", "list", "--json"])
        assert result.exit_code == 0
        output = result.output.strip()
        parsed = json.loads(output)
        assert parsed["repositories"] == []


# ---------------------------------------------------------------------------
# Item 13: ui subcommands — URL display, services, --no-open
# ---------------------------------------------------------------------------


class TestUiSubcommandUrls:
    """Each ui subcommand shows the correct URL."""

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_web_shows_url(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui", "web"])
        assert result.exit_code == 0
        assert "http://localhost:8080" in result.output
        mock_browser.assert_called_once_with("http://localhost:8080")

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_db_shows_adminer_url(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui", "db"])
        assert result.exit_code == 0
        assert "http://localhost:8080/adminer/" in result.output
        mock_browser.assert_called_once_with("http://localhost:8080/adminer/")

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_api_shows_graphql_url(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui", "api"])
        assert result.exit_code == 0
        assert "http://localhost:8080/api/graphql" in result.output
        mock_browser.assert_called_once_with("http://localhost:8080/api/graphql")

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_db_no_open(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui", "db", "--no-open"])
        assert result.exit_code == 0
        assert "adminer" in result.output.lower()
        mock_browser.assert_not_called()

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_api_no_open(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui", "api", "--no-open"])
        assert result.exit_code == 0
        assert "graphql" in result.output.lower()
        mock_browser.assert_not_called()

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_custom_port(self, mock_cls, mock_config, mock_browser):
        """UI subcommands use configured port."""
        mock_config.return_value.port = 9999
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui", "web"])
        assert result.exit_code == 0
        assert "http://localhost:9999" in result.output
        mock_browser.assert_called_once_with("http://localhost:9999")


class TestUiStopServices:
    """ui stop stops correct services (web, adminer) but NOT api."""

    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_stop_targets_correct_services(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["ui", "stop"])
        assert result.exit_code == 0
        # Check the SSH command targets web and adminer, not api
        ssh_cmd = mock_vagrant.ssh.call_args[0][0]
        assert "web" in ssh_cmd
        assert "adminer" in ssh_cmd
        # api should NOT be stopped
        assert ssh_cmd.count("api") == 0 or "stop web adminer" in ssh_cmd


class TestUiStartFailure:
    """ui subcommand handles service start failure gracefully."""

    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_web_ssh_failure(self, mock_cls, mock_config):
        mock_config.return_value.port = 8080
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = Exception("docker compose failed")
        result = runner.invoke(app, ["ui", "web", "--no-open"])
        assert result.exit_code == 1
        assert "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# Item 8: Drain mode — three-way prompt (yes/no/plan) and pending (keep/now/cancel)
# ---------------------------------------------------------------------------


class TestDrainModeThreeWayPrompt:
    """Active work with no drain → prompt yes/no/plan."""

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="yes")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_active_work_choose_yes_proceeds_with_update(self, mock_cls, mock_health, mock_drain, mock_prompt, mock_ver):
        """User chooses 'yes' → immediate update proceeds."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 2, "activeTasks": 3}

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        # Update steps should have been executed
        assert mock_vagrant.ssh.called
        assert "successfully" in result.output.lower() or "completed" in result.output.lower()

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="no")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_active_work_choose_no_aborts(self, mock_cls, mock_drain, mock_prompt, mock_ver):
        """User chooses 'no' → update aborted, no step SSH calls."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 1, "activeTasks": 2}

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "aborted" in result.output.lower()
        # get_compose_prefix makes 1 SSH call, but no update step SSH calls
        assert mock_vagrant.ssh.call_count <= 1

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="plan")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_active_work_choose_plan_enables_drain(self, mock_cls, mock_drain, mock_prompt, mock_ver):
        """User chooses 'plan' → drain mode enabled via API."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 3, "activeTasks": 5}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.return_value = {"setDrainMode": {"enabled": True, "activeAgents": 3, "activeTasks": 5}}
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0
        assert "drain mode enabled" in result.output.lower()
        # get_compose_prefix makes 1 SSH call, but no update step SSH calls
        assert mock_vagrant.ssh.call_count <= 1

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="yes")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_prompt_shows_agent_and_task_count(self, mock_cls, mock_health, mock_drain, mock_prompt, mock_ver):
        """Warning message shows the number of agents and tasks."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 4, "activeTasks": 7}

        result = runner.invoke(app, ["update"])
        assert "4" in result.output
        assert "7" in result.output


class TestDrainModePendingPrompt:
    """Drain already active with work in progress → prompt keep/now/cancel."""

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="keep")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_pending_drain_keep(self, mock_cls, mock_drain, mock_prompt, mock_ver):
        """User keeps waiting for drain to complete."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 1, "activeTasks": 2}

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "keeping" in result.output.lower() or "auto-restart" in result.output.lower()
        # get_compose_prefix makes 1 SSH call, but no update step SSH calls
        assert mock_vagrant.ssh.call_count <= 1

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="now")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_pending_drain_force_now(self, mock_cls, mock_health, mock_drain, mock_prompt, mock_ver):
        """User forces immediate restart despite drain pending."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 1, "activeTasks": 1}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.return_value = {"setDrainMode": {"enabled": False, "activeAgents": 0, "activeTasks": 0}}
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0
        # Should have executed update steps
        assert mock_vagrant.ssh.called

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="cancel")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_pending_drain_cancel(self, mock_cls, mock_drain, mock_prompt, mock_ver):
        """User cancels planned update, disabling drain mode."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 2, "activeTasks": 3}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.return_value = {"setDrainMode": {"enabled": False, "activeAgents": 0, "activeTasks": 0}}
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0
        assert "cancel" in result.output.lower() or "normal operation" in result.output.lower()
        # get_compose_prefix makes 1 SSH call, but no update step SSH calls
        assert mock_vagrant.ssh.call_count <= 1

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="cancel")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_pending_drain_cancel_api_failure(self, mock_cls, mock_drain, mock_prompt, mock_ver):
        """Cancel fails because API is unreachable — exits with error."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 1, "activeTasks": 1}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.side_effect = httpx.ConnectError("refused")
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 1


class TestDrainModeEdgeCases:
    """Edge cases in drain mode logic."""

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_no_active_work_proceeds_without_prompt(self, mock_cls, mock_health, mock_drain, mock_ver):
        """When no agents and no tasks, update proceeds without prompting."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 0, "activeTasks": 0}

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert mock_vagrant.ssh.called

    @patch("aquarco_cli.commands.update.get_postgres_version_mismatch", return_value=None)
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_drain_query_fails_proceeds(self, mock_cls, mock_health, mock_drain, mock_ver):
        """If drain status query fails (returns None), update proceeds anyway."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert mock_vagrant.ssh.called


# ---------------------------------------------------------------------------
# Item 9 + Item 2: init --port persistence
# ---------------------------------------------------------------------------


class TestInitPortPersistence:
    """aquarco init --port saves to ~/.aquarco.json."""

    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    @patch("aquarco_cli.commands.init.reset_config")
    def test_port_saved_when_explicitly_set(self, mock_reset, mock_which, mock_vagrant_cls, mock_health, tmp_path):
        """When --port is explicitly provided, config is written."""
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        mock_vagrant.is_running.return_value = False

        config_file = tmp_path / ".aquarco.json"
        with patch("aquarco_cli.commands.init.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["init", "--port", "9090"])

        assert result.exit_code == 0
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert data["port"] == 9090

    @patch("aquarco_cli.commands.init.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.init.VagrantHelper")
    @patch("aquarco_cli.commands.init.shutil.which", return_value="/usr/bin/mock")
    @patch("aquarco_cli.commands.init.reset_config")
    def test_port_updates_existing_config(self, mock_reset, mock_which, mock_vagrant_cls, mock_health, tmp_path):
        """When config file already exists, port is updated."""
        mock_vagrant = mock_vagrant_cls.return_value
        mock_vagrant.vagrant_dir = "/fake"
        mock_vagrant.is_running.return_value = False

        config_file = tmp_path / ".aquarco.json"
        config_file.write_text(json.dumps({"port": 8080, "other": "value"}))

        with patch("aquarco_cli.commands.init.Path.home", return_value=tmp_path):
            result = runner.invoke(app, ["init", "--port", "3000"])

        assert result.exit_code == 0
        data = json.loads(config_file.read_text())
        assert data["port"] == 3000
        assert data["other"] == "value"  # Other config preserved


# ---------------------------------------------------------------------------
# Item 7: No git pull in update steps
# ---------------------------------------------------------------------------


class TestNoGitPullInUpdate:
    def test_steps_contain_no_git_pull_command(self):
        """Update steps must not contain any git pull command."""
        from aquarco_cli.commands.update import _build_steps

        STEPS = _build_steps("sudo docker compose")
        for name, cmd in STEPS:
            assert "git pull" not in cmd.lower(), f"STEPS contains git pull: {name} → {cmd}"


# ---------------------------------------------------------------------------
# Item 10: status help text improved
# ---------------------------------------------------------------------------


class TestStatusHelpText:
    def test_status_help_mentions_task_id(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        # Help should explain task_id argument and flags
        output_lower = result.output.lower()
        assert "task" in output_lower or "id" in output_lower

    def test_status_help_mentions_follow(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        assert "--follow" in result.output

    def test_status_help_mentions_json(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output

    def test_status_help_mentions_limit(self):
        result = runner.invoke(app, ["status", "--help"])
        assert result.exit_code == 0
        assert "--limit" in result.output


# ---------------------------------------------------------------------------
# Item 12: --open default for ui (browser opens by default)
# ---------------------------------------------------------------------------


class TestUiDefaultOpen:
    """Browser opens by default, suppressed only with --no-open."""

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_opens_browser_by_default(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui"])
        assert result.exit_code == 0
        mock_browser.assert_called_once()

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_web_opens_browser_by_default(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui", "web"])
        assert result.exit_code == 0
        mock_browser.assert_called_once()

    @patch("aquarco_cli.commands.ui.webbrowser.open")
    @patch("aquarco_cli.commands.ui.get_config")
    @patch("aquarco_cli.commands.ui.VagrantHelper")
    def test_ui_db_opens_browser_by_default(self, mock_cls, mock_config, mock_browser):
        mock_config.return_value.port = 8080
        mock_cls.return_value.is_running.return_value = True
        result = runner.invoke(app, ["ui", "db"])
        assert result.exit_code == 0
        mock_browser.assert_called_once()


# ---------------------------------------------------------------------------
# Item 6 / 9: Renamed commands — no stale references
# ---------------------------------------------------------------------------


class TestRenamedCommands:
    """Verify renamed commands work and old names don't exist."""

    def test_repos_command_registered(self):
        result = runner.invoke(app, ["--help"])
        assert "repos" in result.output

    def test_init_command_registered(self):
        result = runner.invoke(app, ["--help"])
        assert "init" in result.output

    def test_watch_command_not_registered(self):
        result = runner.invoke(app, ["--help"])
        # 'watch' should not appear as a command name
        lines = result.output.split("\n")
        command_lines = [l for l in lines if l.strip().startswith("watch")]
        assert len(command_lines) == 0

    def test_install_command_not_registered(self):
        result = runner.invoke(app, ["--help"])
        lines = result.output.split("\n")
        command_lines = [l for l in lines if l.strip().startswith("install")]
        assert len(command_lines) == 0


# ---------------------------------------------------------------------------
# Item 4: Graceful error when VM is not running
# ---------------------------------------------------------------------------


class TestGracefulVmDownErrors:
    """Commands that talk to the API show friendly errors when VM is down."""

    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_repos_list_connection_error(self, mock_cls):
        mock_cls.return_value.execute.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["repos", "list"])
        assert result.exit_code == 1
        assert "cannot reach" in result.output.lower() or "not ready" in result.output.lower()

    @patch("aquarco_cli.commands.repos.GraphQLClient")
    def test_repos_remove_connection_error(self, mock_cls):
        mock_cls.return_value.execute.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["repos", "remove", "myrepo"])
        assert result.exit_code == 1

    @patch("aquarco_cli.commands.auth.GraphQLClient")
    def test_auth_status_connection_error_friendly(self, mock_cls):
        mock_cls.return_value.execute.side_effect = httpx.ConnectError("refused")
        result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 1
        assert "cannot reach" in result.output.lower() or "not ready" in result.output.lower()


# ---------------------------------------------------------------------------
# _query_drain_status edge cases
# ---------------------------------------------------------------------------


class TestQueryDrainStatus:
    """Unit tests for _query_drain_status handling different error types."""

    def test_returns_data_on_success(self):
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.return_value = {
            "drainStatus": {"enabled": False, "activeAgents": 0, "activeTasks": 0}
        }
        result = _query_drain_status(mock_client)
        assert result == {"enabled": False, "activeAgents": 0, "activeTasks": 0}

    def test_returns_none_on_connect_error(self):
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.side_effect = httpx.ConnectError("refused")
        assert _query_drain_status(mock_client) is None

    def test_returns_none_on_timeout(self):
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.side_effect = httpx.TimeoutException("timeout")
        assert _query_drain_status(mock_client) is None

    def test_returns_none_on_key_error(self):
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.return_value = {"unexpected": "response"}
        assert _query_drain_status(mock_client) is None

    def test_returns_none_on_unexpected_exception(self):
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.side_effect = RuntimeError("unexpected")
        assert _query_drain_status(mock_client) is None

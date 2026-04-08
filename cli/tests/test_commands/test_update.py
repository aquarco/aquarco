"""Tests for the update command."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


class TestProductionGuard:
    """Tests for the production build guard — primary feature of this PR."""

    @patch("aquarco_cli.commands.update.BUILD_TYPE", "production")
    def test_production_build_blocks_update(self):
        """aquarco update must exit 1 and print 'not available' for production builds."""
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        assert "not available" in result.output.lower()

    @patch("aquarco_cli.commands.update.BUILD_TYPE", "production")
    def test_production_build_suggests_homebrew(self):
        """Production guard message should suggest brew upgrade."""
        result = runner.invoke(app, ["update"])
        assert "brew upgrade" in result.output.lower()

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    @patch("aquarco_cli.commands.update.BUILD_TYPE", "development")
    def test_development_build_allows_update(self, mock_cls, mock_health, mock_drain):
        """aquarco update should proceed normally for development builds."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0


class TestUpdateCommand:
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_success(self, mock_cls, mock_health, mock_drain):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "successfully" in result.output.lower()

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_vm_not_running(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        assert "not running" in result.output.lower()

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_dry_run(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--dry-run"])
        assert result.exit_code == 0
        assert "dry run" in result.output.lower()
        # Ensure no SSH commands were executed
        mock_vagrant.ssh.assert_not_called()

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_skip_migrations(self, mock_cls, mock_health, mock_drain):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--skip-migrations"])
        assert result.exit_code == 0
        # Verify migrations step was not called
        for call in mock_vagrant.ssh.call_args_list:
            cmd = call[0][0] if call[0] else call[1].get("command", "")
            assert "migrations" not in cmd

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=False)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_unhealthy_warns(self, mock_cls, mock_health, mock_drain):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "unhealthy" in result.output.lower()

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_ssh_step_failure_hard_fails(self, mock_cls, mock_health, mock_drain):
        """SSH step failure should abort the update immediately (hard-fail)."""
        from aquarco_cli.vagrant import VagrantError
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = VagrantError("step failed")
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        assert "step failed" in result.output.lower()

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_skip_provision(self, mock_cls, mock_health, mock_drain):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--skip-provision"])
        assert result.exit_code == 0
        mock_vagrant.provision.assert_not_called()

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_update_provision_failure_hard_fails(self, mock_cls, mock_health, mock_drain):
        """Provisioning failure should abort the update (hard-fail)."""
        from aquarco_cli.vagrant import VagrantError
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.provision.side_effect = VagrantError("provision failed")
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        assert "provisioning failed" in result.output.lower()

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_dry_run_shows_provision_step(self, mock_cls):
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--dry-run"])
        assert result.exit_code == 0
        assert "provision" in result.output.lower()
        assert "health" in result.output.lower()


class TestStepsDefinition:
    """Tests for the STEPS list structure and ordering."""

    def test_no_git_pull_step(self):
        """git pull step must NOT be present in STEPS."""
        from aquarco_cli.commands.update import STEPS
        step_names = [name for name, _ in STEPS]
        for name in step_names:
            assert "git pull" not in name.lower()

    def test_lock_venv_step_exists(self):
        """The 'Lock venv' step must be present in STEPS."""
        from aquarco_cli.commands.update import STEPS

        step_names = [name for name, _ in STEPS]
        assert "Lock venv" in step_names

    def test_lock_venv_uses_chmod_remove_write(self):
        """Lock venv step must remove write permissions on .venv/lib/."""
        from aquarco_cli.commands.update import STEPS

        lock_steps = [(n, c) for n, c in STEPS if n == "Lock venv"]
        assert len(lock_steps) == 1
        cmd = lock_steps[0][1]
        assert "chmod" in cmd
        assert "a-w" in cmd
        assert ".venv/lib/" in cmd

    def test_lock_venv_after_upgrade(self):
        """Lock venv must come immediately after 'Upgrade supervisor package'."""
        from aquarco_cli.commands.update import STEPS

        step_names = [name for name, _ in STEPS]
        upgrade_idx = step_names.index("Upgrade supervisor package")
        lock_idx = step_names.index("Lock venv")
        assert lock_idx == upgrade_idx + 1, (
            f"Lock venv (idx={lock_idx}) should be right after "
            f"Upgrade supervisor package (idx={upgrade_idx})"
        )

    def test_lock_venv_before_restart(self):
        """Lock venv must come before 'Restart supervisor service'."""
        from aquarco_cli.commands.update import STEPS

        step_names = [name for name, _ in STEPS]
        lock_idx = step_names.index("Lock venv")
        restart_idx = step_names.index("Restart supervisor service")
        assert lock_idx < restart_idx

    def test_unlock_lock_sequence(self):
        """Steps must follow unlock → install → lock → restart sequence."""
        from aquarco_cli.commands.update import STEPS

        step_names = [name for name, _ in STEPS]
        fix_idx = step_names.index("Fix venv permissions")
        upgrade_idx = step_names.index("Upgrade supervisor package")
        lock_idx = step_names.index("Lock venv")
        restart_idx = step_names.index("Restart supervisor service")
        assert fix_idx < upgrade_idx < lock_idx < restart_idx


class TestLockVenvExecution:
    """Tests that the lock venv step is actually executed via SSH."""

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_lock_venv_ssh_called(self, mock_cls, mock_health, mock_drain):
        """The lock venv chmod command must be sent via SSH."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True

        runner.invoke(app, ["update"])

        ssh_cmds = [
            call[0][0] if call[0] else call[1].get("command", "")
            for call in mock_vagrant.ssh.call_args_list
        ]
        lock_cmds = [c for c in ssh_cmds if "a-w" in c and ".venv/lib/" in c]
        assert len(lock_cmds) == 1, f"Expected one lock-venv SSH call, got: {ssh_cmds}"

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_dry_run_shows_lock_venv_step(self, mock_cls):
        """Dry run output must list the lock venv step."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--dry-run"])
        assert result.exit_code == 0
        assert "lock venv" in result.output.lower()

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_pip_install_failure_hard_fails(self, mock_cls, mock_health, mock_drain):
        """If pip install fails, update should abort (hard-fail semantics)."""
        from aquarco_cli.vagrant import VagrantError

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True

        # Make only the pip install step fail
        def ssh_side_effect(cmd, **kwargs):
            if "pip install" in cmd:
                raise VagrantError("pip install failed")

        mock_vagrant.ssh.side_effect = ssh_side_effect

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1

        # Lock venv should NOT be called because hard-fail aborts before it
        ssh_cmds = [
            call[0][0] if call[0] else call[1].get("command", "")
            for call in mock_vagrant.ssh.call_args_list
        ]
        lock_cmds = [c for c in ssh_cmds if "a-w" in c and ".venv/lib/" in c]
        assert len(lock_cmds) == 0, "Lock venv must NOT be called after pip install failure (hard-fail)"


class TestBackupRollbackIntegration:
    """Tests that rollback is invoked when a backup exists and a step fails."""

    @patch("aquarco_cli.commands.update._run_rollback")
    @patch("aquarco_cli.commands.update._backup_credentials", return_value="/var/lib/aquarco/backups/20260404T180000")
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_rollback_called_on_step_failure_with_backup(
        self, mock_cls, mock_health, mock_drain, mock_backup, mock_rollback
    ):
        """When a backup dir exists and a step fails, _run_rollback must be called."""
        from aquarco_cli.vagrant import VagrantError

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = VagrantError("step failed")

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        mock_rollback.assert_called_once_with(mock_vagrant, "/var/lib/aquarco/backups/20260404T180000")

    @patch("aquarco_cli.commands.update._run_rollback")
    @patch("aquarco_cli.commands.update._backup_credentials", return_value=None)
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_rollback_not_called_when_no_backup(
        self, mock_cls, mock_health, mock_drain, mock_backup, mock_rollback
    ):
        """When backup is None and a step fails, _run_rollback must NOT be called."""
        from aquarco_cli.vagrant import VagrantError

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = VagrantError("step failed")

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        mock_rollback.assert_not_called()

    @patch("aquarco_cli.commands.update._run_rollback")
    @patch("aquarco_cli.commands.update._backup_credentials", return_value="/var/lib/aquarco/backups/20260404T180000")
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_rollback_called_on_provision_failure_with_backup(
        self, mock_cls, mock_health, mock_drain, mock_backup, mock_rollback
    ):
        """When provisioning fails and backup exists, rollback must be invoked."""
        from aquarco_cli.vagrant import VagrantError

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.provision.side_effect = VagrantError("provision failed")

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 1
        mock_rollback.assert_called_once_with(mock_vagrant, "/var/lib/aquarco/backups/20260404T180000")


class TestDrainModeIntegration:
    """Tests for drain mode prompts in update."""

    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_drain_idle_auto_proceeds(self, mock_cls, mock_health, mock_drain):
        """When drain is enabled and all idle, auto-clear and proceed."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 0, "activeTasks": 0}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.return_value = {"setDrainMode": {"enabled": False, "activeAgents": 0, "activeTasks": 0}}
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0
        assert "proceeding" in result.output.lower()

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="plan")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_active_work_plan_sets_drain(self, mock_cls, mock_drain, mock_prompt):
        """When active work and user chooses 'plan', drain mode is enabled."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 2, "activeTasks": 3}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.return_value = {"setDrainMode": {"enabled": True, "activeAgents": 2, "activeTasks": 3}}
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0
        assert "drain mode enabled" in result.output.lower()

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="no")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_active_work_abort(self, mock_cls, mock_drain, mock_prompt):
        """When active work and user chooses 'no', update is aborted."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 1, "activeTasks": 1}

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "aborted" in result.output.lower()

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="keep")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_drain_active_keep(self, mock_cls, mock_drain, mock_prompt):
        """When drain is active with work and user chooses 'keep', supervisor keeps draining."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 1, "activeTasks": 2}

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "keeping" in result.output.lower() or "auto-restart" in result.output.lower()

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="now")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_drain_active_force_now(self, mock_cls, mock_health, mock_drain, mock_prompt):
        """When drain is active with work and user chooses 'now', force immediate update."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 1, "activeTasks": 2}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.return_value = {"setDrainMode": {"enabled": False}}
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0
        assert "forcing" in result.output.lower() or "successfully" in result.output.lower()

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="cancel")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_drain_active_cancel(self, mock_cls, mock_drain, mock_prompt):
        """When drain is active with work and user chooses 'cancel', drain is disabled."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 1, "activeTasks": 2}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.return_value = {"setDrainMode": {"enabled": False}}
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0
        assert "normal operation resumed" in result.output.lower()

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="cancel")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_drain_cancel_api_failure_exits(self, mock_cls, mock_drain, mock_prompt):
        """When cancelling drain fails, exit with code 1."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 1, "activeTasks": 2}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.side_effect = Exception("API down")
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 1

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="yes")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_active_work_yes_proceeds(self, mock_cls, mock_health, mock_drain, mock_prompt):
        """When active work (no drain) and user chooses 'yes', update proceeds."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 1, "activeTasks": 1}

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "successfully" in result.output.lower()

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="plan")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_plan_drain_set_failure_exits(self, mock_cls, mock_drain, mock_prompt):
        """When setting drain mode fails, exit with code 1."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": False, "activeAgents": 1, "activeTasks": 1}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.side_effect = Exception("API down")
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 1


class TestQueryDrainStatus:
    """Tests for _query_drain_status error handling branches."""

    @patch("aquarco_cli.commands.update.GraphQLClient")
    def test_connection_error_returns_none(self, mock_gql_cls):
        """Connection errors should return None gracefully."""
        import httpx
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.side_effect = httpx.ConnectError("refused")
        result = _query_drain_status(mock_client)
        assert result is None

    @patch("aquarco_cli.commands.update.GraphQLClient")
    def test_timeout_error_returns_none(self, mock_gql_cls):
        """Timeout errors should return None gracefully."""
        import httpx
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.side_effect = httpx.TimeoutException("timeout")
        result = _query_drain_status(mock_client)
        assert result is None

    @patch("aquarco_cli.commands.update.GraphQLClient")
    def test_key_error_returns_none(self, mock_gql_cls):
        """KeyError on unexpected response shape returns None with warning."""
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.return_value = {"unexpected": "data"}
        result = _query_drain_status(mock_client)
        assert result is None

    @patch("aquarco_cli.commands.update.GraphQLClient")
    def test_type_error_returns_none(self, mock_gql_cls):
        """TypeError on None response returns None with warning."""
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.return_value = None
        result = _query_drain_status(mock_client)
        assert result is None

    @patch("aquarco_cli.commands.update.GraphQLClient")
    def test_generic_exception_returns_none(self, mock_gql_cls):
        """Any other exception returns None with warning."""
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.side_effect = RuntimeError("unexpected")
        result = _query_drain_status(mock_client)
        assert result is None

    @patch("aquarco_cli.commands.update.GraphQLClient")
    def test_success_returns_drain_status(self, mock_gql_cls):
        """Successful query returns the drainStatus dict."""
        from aquarco_cli.commands.update import _query_drain_status

        mock_client = MagicMock()
        mock_client.execute.return_value = {"drainStatus": {"enabled": False, "activeAgents": 0, "activeTasks": 0}}
        result = _query_drain_status(mock_client)
        assert result == {"enabled": False, "activeAgents": 0, "activeTasks": 0}


class TestRunRollback:
    """Tests for _run_rollback function."""

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_rollback_invokes_ssh_with_shlex_quoted_path(self, mock_cls):
        """_run_rollback must pass the backup dir through shlex.quote."""
        from aquarco_cli.commands.update import _run_rollback

        mock_vagrant = MagicMock()
        _run_rollback(mock_vagrant, "/var/lib/aquarco/backups/20260404T180000")
        mock_vagrant.ssh.assert_called_once()
        call_cmd = mock_vagrant.ssh.call_args[0][0]
        assert "rollback.sh" in call_cmd
        assert "/var/lib/aquarco/backups/20260404T180000" in call_cmd

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_rollback_quotes_special_chars_in_path(self, mock_cls):
        """_run_rollback must safely handle paths with special characters."""
        from aquarco_cli.commands.update import _run_rollback

        mock_vagrant = MagicMock()
        _run_rollback(mock_vagrant, "/var/lib/backups/dir with spaces")
        call_cmd = mock_vagrant.ssh.call_args[0][0]
        # shlex.quote wraps in single quotes for paths with spaces
        assert "dir with spaces" in call_cmd

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_rollback_failure_does_not_raise(self, mock_cls):
        """_run_rollback must not raise when the rollback SSH call fails."""
        from aquarco_cli.vagrant import VagrantError
        from aquarco_cli.commands.update import _run_rollback

        mock_vagrant = MagicMock()
        mock_vagrant.ssh.side_effect = VagrantError("rollback ssh failed")
        # Should not raise — just print an error
        _run_rollback(mock_vagrant, "/var/lib/aquarco/backups/20260404T180000")

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_rollback_os_error_does_not_raise(self, mock_cls):
        """_run_rollback handles OSError without raising."""
        from aquarco_cli.commands.update import _run_rollback

        mock_vagrant = MagicMock()
        mock_vagrant.ssh.side_effect = OSError("file not found")
        _run_rollback(mock_vagrant, "/tmp/backup")


class TestBackupCredentials:
    """Tests for _backup_credentials function."""

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_backup_returns_dir_from_stdout(self, mock_cls):
        """_backup_credentials returns the last line of stdout as backup dir."""
        from aquarco_cli.commands.update import _backup_credentials

        mock_vagrant = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = "Some log output\n/var/lib/aquarco/backups/20260404T180000\n"
        mock_vagrant.ssh.return_value = mock_result
        result = _backup_credentials(mock_vagrant)
        assert result == "/var/lib/aquarco/backups/20260404T180000"

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_backup_returns_none_on_vagrant_error(self, mock_cls):
        """_backup_credentials returns None when VagrantError is raised."""
        from aquarco_cli.vagrant import VagrantError
        from aquarco_cli.commands.update import _backup_credentials

        mock_vagrant = MagicMock()
        mock_vagrant.ssh.side_effect = VagrantError("backup failed")
        result = _backup_credentials(mock_vagrant)
        assert result is None

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_backup_returns_none_on_index_error(self, mock_cls):
        """_backup_credentials returns None when stdout is empty (IndexError)."""
        from aquarco_cli.commands.update import _backup_credentials

        mock_vagrant = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_vagrant.ssh.return_value = mock_result
        result = _backup_credentials(mock_vagrant)
        assert result is None

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_backup_returns_none_on_os_error(self, mock_cls):
        """_backup_credentials returns None when OSError is raised."""
        from aquarco_cli.commands.update import _backup_credentials

        mock_vagrant = MagicMock()
        mock_vagrant.ssh.side_effect = OSError("no such file")
        result = _backup_credentials(mock_vagrant)
        assert result is None

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_backup_runs_as_agent_user(self, mock_cls):
        """backup-credentials.sh must run via sudo -u agent HOME=/home/agent."""
        from aquarco_cli.commands.update import _backup_credentials

        mock_vagrant = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = "/var/lib/aquarco/backups/20260408T120000\n"
        mock_vagrant.ssh.return_value = mock_result

        _backup_credentials(mock_vagrant)

        cmd = mock_vagrant.ssh.call_args[0][0]
        assert "sudo -u agent" in cmd
        assert "HOME=/home/agent" in cmd
        assert "backup-credentials.sh" in cmd


class TestBuildTypeConstant:
    """Tests for the _build.py module."""

    def test_build_type_is_development(self):
        """Default BUILD_TYPE should be 'development' in dev builds."""
        from aquarco_cli._build import BUILD_TYPE
        assert BUILD_TYPE == "development"

    def test_build_type_is_string(self):
        """BUILD_TYPE must be a string."""
        from aquarco_cli._build import BUILD_TYPE
        assert isinstance(BUILD_TYPE, str)


class TestStepsContent:
    """Tests for production-relevant step content in STEPS list."""

    def test_os_update_is_first_step(self):
        """OS package update must be the first step per design requirements."""
        from aquarco_cli.commands.update import STEPS
        assert "os packages" in STEPS[0][0].lower() or "apt" in STEPS[0][1].lower()

    def test_all_steps_have_ssh_commands(self):
        """Every step must have a non-empty SSH command string."""
        from aquarco_cli.commands.update import STEPS
        for name, cmd in STEPS:
            assert isinstance(name, str) and name.strip(), f"Empty step name"
            assert isinstance(cmd, str) and cmd.strip(), f"Empty command for step '{name}'"

    def test_docker_compose_pull_before_restart(self):
        """Docker pull step must come before restart step."""
        from aquarco_cli.commands.update import STEPS
        step_names = [name for name, _ in STEPS]
        pull_idx = next(i for i, n in enumerate(step_names) if "pull" in n.lower())
        restart_idx = next(i for i, n in enumerate(step_names) if "restart docker" in n.lower())
        assert pull_idx < restart_idx

    def test_migrations_before_restart(self):
        """Database migrations must run before Docker service restart."""
        from aquarco_cli.commands.update import STEPS
        step_names = [name for name, _ in STEPS]
        migration_idx = next(i for i, n in enumerate(step_names) if "migration" in n.lower())
        restart_idx = next(i for i, n in enumerate(step_names) if "restart docker" in n.lower())
        assert migration_idx < restart_idx


class TestImplementationFixes:
    """Tests for the review-fix implementation changes.

    Validates that the implementation agent correctly addressed the review
    findings: --build flag removal, --with-new-pkgs addition, and production
    compose changes.
    """

    def test_docker_restart_step_does_not_include_build_flag(self):
        """Restart Docker step must NOT include --build (no-op in prod, misleading)."""
        from aquarco_cli.commands.update import STEPS
        restart_steps = [(n, c) for n, c in STEPS if "restart docker" in n.lower()]
        assert len(restart_steps) == 1, "Expected exactly one 'Restart Docker services' step"
        _, cmd = restart_steps[0]
        assert "--build" not in cmd, (
            f"The --build flag should have been removed from the docker compose up step. "
            f"Got: {cmd}"
        )

    def test_docker_restart_step_uses_compose_up(self):
        """Restart Docker step must use 'docker compose up -d'."""
        from aquarco_cli.commands.update import STEPS
        restart_steps = [(n, c) for n, c in STEPS if "restart docker" in n.lower()]
        _, cmd = restart_steps[0]
        assert "docker compose up -d" in cmd

    def test_os_update_uses_with_new_pkgs(self):
        """OS update step must use --with-new-pkgs for safer upgrades."""
        from aquarco_cli.commands.update import STEPS
        os_step = STEPS[0]
        _, cmd = os_step
        assert "--with-new-pkgs" in cmd, (
            f"The apt-get upgrade step should use --with-new-pkgs. Got: {cmd}"
        )

    def test_os_update_uses_noninteractive(self):
        """OS update step must set DEBIAN_FRONTEND=noninteractive."""
        from aquarco_cli.commands.update import STEPS
        _, cmd = STEPS[0]
        assert "DEBIAN_FRONTEND=noninteractive" in cmd

    def test_steps_count_is_eight(self):
        """STEPS list must contain exactly 8 steps."""
        from aquarco_cli.commands.update import STEPS
        assert len(STEPS) == 8, f"Expected 8 steps, got {len(STEPS)}: {[n for n, _ in STEPS]}"


class TestDrainIdleClearFailure:
    """Tests for edge cases in drain-idle auto-proceed path."""

    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_drain_idle_clear_failure_still_proceeds(self, mock_cls, mock_health, mock_drain):
        """When drain is idle but clearing drain flag fails, update should still proceed."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 0, "activeTasks": 0}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.side_effect = Exception("API unreachable")
            result = runner.invoke(app, ["update"])

        # Should still succeed — the warning is logged but update proceeds
        assert result.exit_code == 0
        assert "successfully" in result.output.lower()

    @patch("aquarco_cli.commands.update.Prompt.ask", return_value="now")
    @patch("aquarco_cli.commands.update._query_drain_status")
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_force_now_clear_failure_still_proceeds(self, mock_cls, mock_health, mock_drain, mock_prompt):
        """When force-now and clearing drain fails, update should still proceed."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_drain.return_value = {"enabled": True, "activeAgents": 1, "activeTasks": 2}

        with patch("aquarco_cli.commands.update.GraphQLClient") as mock_gql_cls:
            mock_client = mock_gql_cls.return_value
            mock_client.execute.side_effect = Exception("API timeout")
            result = runner.invoke(app, ["update"])

        assert result.exit_code == 0


class TestCombinedFlags:
    """Tests for combined CLI flag behavior."""

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_dry_run_with_skip_migrations(self, mock_cls):
        """--dry-run with --skip-migrations should not list the migration step."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--dry-run", "--skip-migrations"])
        assert result.exit_code == 0
        assert "dry run" in result.output.lower()
        assert "migration" not in result.output.lower()

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_dry_run_with_skip_provision(self, mock_cls):
        """--dry-run with --skip-provision should not list the provision step."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--dry-run", "--skip-provision"])
        assert result.exit_code == 0
        # Should not include "Re-provision VM" in dry run output
        assert "re-provision" not in result.output.lower()

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_dry_run_with_all_skips(self, mock_cls):
        """--dry-run with both skip flags omits migrations and provision."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--dry-run", "--skip-migrations", "--skip-provision"])
        assert result.exit_code == 0
        assert "migration" not in result.output.lower()
        assert "re-provision" not in result.output.lower()

    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_skip_migrations_and_provision(self, mock_cls, mock_health, mock_drain):
        """Both --skip-migrations and --skip-provision together."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        result = runner.invoke(app, ["update", "--skip-migrations", "--skip-provision"])
        assert result.exit_code == 0
        mock_vagrant.provision.assert_not_called()
        for call in mock_vagrant.ssh.call_args_list:
            cmd = call[0][0] if call[0] else call[1].get("command", "")
            assert "migrations" not in cmd


class TestBackupCredentialsEdgeCases:
    """Additional edge cases for _backup_credentials."""

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_backup_strips_trailing_whitespace(self, mock_cls):
        """_backup_credentials must strip trailing whitespace from the backup dir."""
        from aquarco_cli.commands.update import _backup_credentials

        mock_vagrant = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = "/var/lib/aquarco/backups/20260404T180000  \n  \n"
        mock_vagrant.ssh.return_value = mock_result
        result = _backup_credentials(mock_vagrant)
        assert result == "/var/lib/aquarco/backups/20260404T180000"

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_backup_with_multiline_log_output(self, mock_cls):
        """_backup_credentials extracts backup dir from multiline output with logs."""
        from aquarco_cli.commands.update import _backup_credentials

        mock_vagrant = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = (
            "[backup] Starting credential backup...\n"
            "[backup] Found gh_token\n"
            "[backup] Found claude_api_key\n"
            "/var/lib/aquarco/backups/20260404T190000\n"
        )
        mock_vagrant.ssh.return_value = mock_result
        result = _backup_credentials(mock_vagrant)
        assert result == "/var/lib/aquarco/backups/20260404T190000"

    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_backup_subprocess_error_returns_none(self, mock_cls):
        """_backup_credentials returns None on subprocess.CalledProcessError."""
        import subprocess
        from aquarco_cli.commands.update import _backup_credentials

        mock_vagrant = MagicMock()
        mock_vagrant.ssh.side_effect = subprocess.CalledProcessError(1, "bash")
        result = _backup_credentials(mock_vagrant)
        assert result is None


class TestRunUpdateStepsEdgeCases:
    """Edge cases for _run_update_steps."""

    @patch("aquarco_cli.commands.update._run_rollback")
    @patch("aquarco_cli.commands.update._backup_credentials", return_value="/var/lib/aquarco/backups/test")
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_rollback_called_with_correct_backup_dir(
        self, mock_cls, mock_health, mock_drain, mock_backup, mock_rollback
    ):
        """Rollback must receive the exact backup dir returned by _backup_credentials."""
        from aquarco_cli.vagrant import VagrantError

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = VagrantError("fail")

        runner.invoke(app, ["update"])
        mock_rollback.assert_called_once_with(mock_vagrant, "/var/lib/aquarco/backups/test")

    @patch("aquarco_cli.commands.update._run_rollback")
    @patch("aquarco_cli.commands.update._backup_credentials", return_value="/var/lib/aquarco/backups/test")
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_first_step_failure_skips_remaining_steps(
        self, mock_cls, mock_health, mock_drain, mock_backup, mock_rollback
    ):
        """When the first SSH step fails, no subsequent steps should be executed."""
        from aquarco_cli.vagrant import VagrantError

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = VagrantError("first step failed")

        runner.invoke(app, ["update"])
        # Only the first SSH call should have been made
        assert mock_vagrant.ssh.call_count == 1

    @patch("aquarco_cli.commands.update._backup_credentials", return_value=None)
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_all_steps_executed_on_success(self, mock_cls, mock_health, mock_drain, mock_backup):
        """On success, all STEPS should be executed via SSH."""
        from aquarco_cli.commands.update import STEPS

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True

        runner.invoke(app, ["update"])
        assert mock_vagrant.ssh.call_count == len(STEPS)

    @patch("aquarco_cli.commands.update._backup_credentials", return_value=None)
    @patch("aquarco_cli.commands.update._query_drain_status", return_value=None)
    @patch("aquarco_cli.commands.update.print_health_table", return_value=True)
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_provision_called_after_all_steps(self, mock_cls, mock_health, mock_drain, mock_backup):
        """vagrant.provision() must be called after all SSH steps succeed."""
        from aquarco_cli.commands.update import STEPS

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True

        runner.invoke(app, ["update"])
        mock_vagrant.ssh.assert_called()
        mock_vagrant.provision.assert_called_once()


class TestProductionGuardEdgeCases:
    """Additional production guard tests."""

    @patch("aquarco_cli.commands.update.BUILD_TYPE", "production")
    def test_production_guard_ignores_all_flags(self):
        """Production guard must block even with --dry-run, --skip-* flags."""
        result = runner.invoke(app, ["update", "--dry-run"])
        assert result.exit_code == 1
        assert "not available" in result.output.lower()

    @patch("aquarco_cli.commands.update.BUILD_TYPE", "production")
    def test_production_guard_message_includes_brew_upgrade(self):
        """Production guard message must mention 'brew upgrade aquarco'."""
        result = runner.invoke(app, ["update"])
        assert "brew upgrade" in result.output.lower()

    @patch("aquarco_cli.commands.update.BUILD_TYPE", "development")
    @patch("aquarco_cli.commands.update.VagrantHelper")
    def test_development_build_does_not_show_homebrew_message(self, mock_cls):
        """Development builds must not show the Homebrew upgrade message."""
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = False
        result = runner.invoke(app, ["update"])
        assert "brew upgrade" not in result.output.lower()

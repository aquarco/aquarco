"""Tests for the update command."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from typer.testing import CliRunner

from aquarco_cli.main import app

runner = CliRunner()


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
    def test_update_ssh_step_failure_continues(self, mock_cls, mock_health, mock_drain):
        """SSH step failure should continue to next step."""
        from aquarco_cli.vagrant import VagrantError
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.ssh.side_effect = VagrantError("step failed")
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
        assert "continuing" in result.output.lower()

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
    def test_update_provision_failure_warns(self, mock_cls, mock_health, mock_drain):
        from aquarco_cli.vagrant import VagrantError
        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True
        mock_vagrant.provision.side_effect = VagrantError("provision failed")
        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0
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
    def test_lock_venv_runs_even_if_upgrade_fails(self, mock_cls, mock_health, mock_drain):
        """If pip install fails, lock venv should still execute (fail-safe)."""
        from aquarco_cli.vagrant import VagrantError

        mock_vagrant = mock_cls.return_value
        mock_vagrant.is_running.return_value = True

        # Make only the pip install step fail
        def ssh_side_effect(cmd, **kwargs):
            if "pip install" in cmd:
                raise VagrantError("pip install failed")

        mock_vagrant.ssh.side_effect = ssh_side_effect

        result = runner.invoke(app, ["update"])
        assert result.exit_code == 0

        ssh_cmds = [
            call[0][0] if call[0] else call[1].get("command", "")
            for call in mock_vagrant.ssh.call_args_list
        ]
        lock_cmds = [c for c in ssh_cmds if "a-w" in c and ".venv/lib/" in c]
        assert len(lock_cmds) == 1, "Lock venv must still be called after pip install failure"


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

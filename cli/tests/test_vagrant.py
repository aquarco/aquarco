"""Tests for the Vagrant helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aquarco_cli.vagrant import COMPOSE_DIR, LOAD_SECRETS, LOAD_SUPERVISOR_SECRETS, VagrantError, VagrantHelper, get_compose_prefix


class TestVagrantConstants:
    """Tests for centralized VM/Docker constants."""

    def test_compose_dir_is_docker_path(self):
        assert COMPOSE_DIR == "/home/agent/aquarco/docker"

    def test_load_secrets_sources_docker_secrets(self):
        assert "docker-secrets.env" in LOAD_SECRETS
        assert "set -a" in LOAD_SECRETS

    def test_load_supervisor_secrets_sources_secrets_env(self):
        assert "secrets.env" in LOAD_SUPERVISOR_SECRETS
        assert "set -a" in LOAD_SUPERVISOR_SECRETS
        # Should NOT be docker-secrets.env (supervisor uses host-side secrets)
        assert "docker-secrets.env" not in LOAD_SUPERVISOR_SECRETS

    def test_load_secrets_and_supervisor_secrets_are_different(self):
        """LOAD_SECRETS and LOAD_SUPERVISOR_SECRETS must point to different env files."""
        assert LOAD_SECRETS != LOAD_SUPERVISOR_SECRETS


class TestVagrantHelper:
    def setup_method(self):
        self.helper = VagrantHelper(vagrant_dir=Path("/fake/vagrant"))

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_status_returns_running(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="1234567890,default,state,running\n",
            stderr="",
        )
        assert self.helper.status() == "running"

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_status_returns_poweroff(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="1234567890,default,state,poweroff\n",
            stderr="",
        )
        assert self.helper.status() == "poweroff"

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_is_running_true(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="1234567890,default,state,running\n",
            stderr="",
        )
        assert self.helper.is_running() is True

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_is_running_false_on_error(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="error",
        )
        assert self.helper.is_running() is False

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_ssh_calls_vagrant_ssh(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="output", stderr="",
        )
        self.helper.ssh("echo hello")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "ssh" in args
        assert "-c" in args
        # Command passed directly (no shlex.quote) to allow shell operators
        assert "echo hello" in args

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_ssh_raises_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="command failed",
        )
        with pytest.raises(VagrantError, match="failed"):
            self.helper.ssh("bad command")

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_up_with_provision(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
        )
        self.helper.up(provision=True)
        args = mock_run.call_args[0][0]
        assert "up" in args
        assert "--provision" in args

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_up_without_provision(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        self.helper.up(provision=False)
        args = mock_run.call_args[0][0]
        assert "up" in args
        assert "--provision" not in args

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_halt(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        self.helper.halt()
        args = mock_run.call_args[0][0]
        assert "halt" in args

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_provision(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        self.helper.provision()
        args = mock_run.call_args[0][0]
        assert "provision" in args

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_status_unknown_when_no_state_line(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="no state here\n", stderr="",
        )
        assert self.helper.status() == "unknown"


class TestVagrantHelperWithVmName:
    """Test that vm_name is inserted correctly into commands."""

    def setup_method(self):
        self.helper = VagrantHelper(vagrant_dir=Path("/fake/vagrant"), vm_name="myvm")

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_vm_name_inserted_after_subcommand(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1234,myvm,state,running\n", stderr="",
        )
        self.helper.status()
        args = mock_run.call_args[0][0]
        # vm_name should be right after the subcommand verb
        assert args == ["vagrant", "status", "myvm", "--machine-readable"]

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_ssh_with_vm_name(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr="",
        )
        self.helper.ssh("echo hello")
        args = mock_run.call_args[0][0]
        assert args == ["vagrant", "ssh", "myvm", "-c", "echo hello"]

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_up_with_vm_name(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        self.helper.up(provision=True)
        args = mock_run.call_args[0][0]
        assert args == ["vagrant", "up", "myvm", "--provision"]


class TestVagrantHelperCwd:
    """Test that vagrant commands use the correct working directory."""

    @patch("aquarco_cli.vagrant.subprocess.run")
    def test_run_uses_vagrant_dir_as_cwd(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1234,default,state,running\n", stderr="",
        )
        helper = VagrantHelper(vagrant_dir=Path("/my/vagrant/dir"))
        helper.status()
        kwargs = mock_run.call_args[1]
        assert kwargs["cwd"] == "/my/vagrant/dir"


class TestGetComposePrefix:
    """Tests for get_compose_prefix() environment detection."""

    def _make_vagrant(self, ssh_stdout: str = "development") -> MagicMock:
        v = MagicMock(spec=VagrantHelper)
        v.ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=ssh_stdout, stderr="",
        )
        return v

    def test_returns_prod_compose_for_production(self):
        vagrant = self._make_vagrant(ssh_stdout="production")
        result = get_compose_prefix(vagrant)
        assert result == "docker compose -f compose.prod.yml"

    def test_returns_default_compose_for_development(self):
        vagrant = self._make_vagrant(ssh_stdout="development")
        result = get_compose_prefix(vagrant)
        assert result == "docker compose"

    def test_reads_env_file_via_ssh(self):
        vagrant = self._make_vagrant(ssh_stdout="production")
        get_compose_prefix(vagrant)
        vagrant.ssh.assert_called_once()
        cmd = vagrant.ssh.call_args[0][0]
        assert "/etc/aquarco/env" in cmd

    def test_strips_whitespace_from_env_value(self):
        vagrant = self._make_vagrant(ssh_stdout="  production\n")
        result = get_compose_prefix(vagrant)
        assert result == "docker compose -f compose.prod.yml"

    def test_defaults_to_development_on_ssh_exception(self):
        vagrant = MagicMock(spec=VagrantHelper)
        vagrant.ssh.side_effect = VagrantError("ssh connection failed")
        result = get_compose_prefix(vagrant)
        assert result == "docker compose"

    def test_defaults_to_development_on_generic_exception(self):
        vagrant = MagicMock(spec=VagrantHelper)
        vagrant.ssh.side_effect = OSError("connection refused")
        result = get_compose_prefix(vagrant)
        assert result == "docker compose"

    def test_defaults_to_development_when_stdout_is_none(self):
        vagrant = MagicMock(spec=VagrantHelper)
        vagrant.ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=None, stderr="",
        )
        result = get_compose_prefix(vagrant)
        assert result == "docker compose"

    def test_unknown_env_value_defaults_to_dev(self):
        """An unexpected env value (e.g. 'staging') should use default compose."""
        vagrant = self._make_vagrant(ssh_stdout="staging")
        result = get_compose_prefix(vagrant)
        assert result == "docker compose"

    def test_empty_string_defaults_to_dev(self):
        vagrant = self._make_vagrant(ssh_stdout="")
        result = get_compose_prefix(vagrant)
        assert result == "docker compose"

    def test_uses_sudo_cat(self):
        """get_compose_prefix should use 'sudo cat' to read the env file."""
        vagrant = self._make_vagrant(ssh_stdout="development")
        get_compose_prefix(vagrant)
        cmd = vagrant.ssh.call_args[0][0]
        assert "sudo cat" in cmd

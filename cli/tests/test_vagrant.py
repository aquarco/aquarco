"""Tests for the Vagrant helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from aquarco_cli.vagrant import VagrantError, VagrantHelper


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


class TestProductionDockerMode:
    """Test that AQUARCO_DOCKER_MODE is injected in production builds."""

    @patch("aquarco_cli.vagrant.subprocess.run")
    @patch("aquarco_cli.vagrant.BUILD_TYPE", "production")
    def test_docker_mode_set_in_production(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="1234,default,state,running\n", stderr="",
        )
        helper = VagrantHelper(vagrant_dir=Path("/fake/vagrant"))
        helper.status()
        env = mock_run.call_args[1]["env"]
        assert env["AQUARCO_DOCKER_MODE"] == "production"

    @patch("aquarco_cli.vagrant.subprocess.run")
    @patch("aquarco_cli.vagrant.BUILD_TYPE", "development")
    def test_docker_mode_not_set_in_development(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="1234,default,state,running\n", stderr="",
        )
        helper = VagrantHelper(vagrant_dir=Path("/fake/vagrant"))
        helper.status()
        env = mock_run.call_args[1]["env"]
        assert "AQUARCO_DOCKER_MODE" not in env

    @patch.dict("os.environ", {"AQUARCO_DOCKER_MODE": "custom"})
    @patch("aquarco_cli.vagrant.subprocess.run")
    @patch("aquarco_cli.vagrant.BUILD_TYPE", "production")
    def test_docker_mode_not_overridden_if_already_set(self, mock_run):
        """setdefault() should not override an existing AQUARCO_DOCKER_MODE."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="1234,default,state,running\n", stderr="",
        )
        helper = VagrantHelper(vagrant_dir=Path("/fake/vagrant"))
        helper.status()
        env = mock_run.call_args[1]["env"]
        assert env["AQUARCO_DOCKER_MODE"] == "custom"


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

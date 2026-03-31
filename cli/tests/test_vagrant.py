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

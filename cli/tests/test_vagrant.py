"""Tests for the Vagrant helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from aquarco_cli.vagrant import (
    COMPOSE_DIR,
    COMPOSE_ENV_FLAGS,
    LOAD_SECRETS,
    LOAD_SUPERVISOR_SECRETS,
    VagrantError,
    VagrantHelper,
    get_compose_prefix,
    get_postgres_version_mismatch,
)


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


class TestGetPostgresVersionMismatch:
    """Tests for get_postgres_version_mismatch()."""

    def setup_method(self):
        self.helper = VagrantHelper(vagrant_dir=Path("/fake/vagrant"))

    def _make_ssh_side_effect(self, pg_version_stdout: str, conf_version_stdout: str):
        """Return a side_effect callable that returns pg_version on first call,
        conf_version on second call."""
        responses = iter([
            subprocess.CompletedProcess(args=[], returncode=0, stdout=pg_version_stdout, stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout=conf_version_stdout, stderr=""),
        ])
        return lambda *a, **kw: next(responses)

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_when_versions_match(self, mock_ssh):
        mock_ssh.side_effect = self._make_ssh_side_effect("16\n", "16\n")
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_returns_tuple_on_mismatch(self, mock_ssh):
        mock_ssh.side_effect = self._make_ssh_side_effect("16\n", "18\n")
        result = get_postgres_version_mismatch(self.helper)
        assert result == ("16", "18")

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_when_pg_version_empty(self, mock_ssh):
        """Empty PG_VERSION means volume doesn't exist yet — no mismatch."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_when_pg_version_non_digit(self, mock_ssh):
        """Non-digit PG_VERSION (e.g. garbage) should be treated as unknown."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not-a-number\n", stderr="",
        )
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_when_conf_version_empty(self, mock_ssh):
        mock_ssh.side_effect = self._make_ssh_side_effect("16\n", "\n")
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_when_conf_version_non_digit(self, mock_ssh):
        mock_ssh.side_effect = self._make_ssh_side_effect("16\n", "abc\n")
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_strips_alpine_suffix_from_conf_version(self, mock_ssh):
        """'18-alpine' should be treated as version '18'."""
        mock_ssh.side_effect = self._make_ssh_side_effect("18\n", "18-alpine\n")
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_strips_alpine_suffix_mismatch(self, mock_ssh):
        mock_ssh.side_effect = self._make_ssh_side_effect("16\n", "18-alpine\n")
        result = get_postgres_version_mismatch(self.helper)
        assert result == ("16", "18")

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_on_ssh_exception(self, mock_ssh):
        """SSH failures are non-fatal — return None."""
        mock_ssh.side_effect = VagrantError("connection refused")
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_on_generic_exception(self, mock_ssh):
        mock_ssh.side_effect = OSError("network down")
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_when_stdout_is_none(self, mock_ssh):
        """Handle None stdout gracefully."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=None, stderr="",
        )
        assert get_postgres_version_mismatch(self.helper) is None


class TestGetPostgresVersionMismatchShellCommand:
    """Tests for the shell command structure in get_postgres_version_mismatch().

    These tests validate the corrected shell fallback (if/then/else instead of ||)
    and the sudo docker pattern used in the PG_VERSION read.
    """

    def setup_method(self):
        self.helper = VagrantHelper(vagrant_dir=Path("/fake/vagrant"))

    @patch.object(VagrantHelper, "ssh")
    def test_first_ssh_uses_sudo_docker(self, mock_ssh):
        """PG_VERSION read must use 'sudo docker run' (not bare 'docker')."""
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
        ]
        get_postgres_version_mismatch(self.helper)
        first_call_cmd = mock_ssh.call_args_list[0][0][0]
        assert "sudo docker run" in first_call_cmd

    @patch.object(VagrantHelper, "ssh")
    def test_second_ssh_uses_if_not_pipe_or(self, mock_ssh):
        """Config version shell must use 'if ... then ... else' (not '||' fallback).

        The original code used '||' which checked 'cut' exit status (always 0),
        making the compose.yml fallback unreachable. The fix uses 'if ... -n'.
        """
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
        ]
        get_postgres_version_mismatch(self.helper)
        second_call_cmd = mock_ssh.call_args_list[1][0][0]
        assert "if " in second_call_cmd
        assert '[ -n' in second_call_cmd

    @patch.object(VagrantHelper, "ssh")
    def test_pg_version_read_mounts_volume_readonly(self, mock_ssh):
        """PG_VERSION read uses ':ro' mount for safety."""
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
        ]
        get_postgres_version_mismatch(self.helper)
        first_call_cmd = mock_ssh.call_args_list[0][0][0]
        assert ":ro" in first_call_cmd

    @patch.object(VagrantHelper, "ssh")
    def test_conf_version_checks_versions_env_first(self, mock_ssh):
        """Config version command must check versions.env before compose.yml."""
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
        ]
        get_postgres_version_mismatch(self.helper)
        second_call_cmd = mock_ssh.call_args_list[1][0][0]
        assert "versions.env" in second_call_cmd
        assert "compose.yml" in second_call_cmd

    @patch.object(VagrantHelper, "ssh")
    def test_returns_none_when_pg_version_has_trailing_newlines(self, mock_ssh):
        """PG_VERSION with extra whitespace/newlines should still work."""
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n\n\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
        ]
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_conf_version_with_dash_suffix_other_than_alpine(self, mock_ssh):
        """Suffixes like '18-bookworm' should also be stripped to '18'."""
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="18\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="18-bookworm\n", stderr=""),
        ]
        assert get_postgres_version_mismatch(self.helper) is None

    @patch.object(VagrantHelper, "ssh")
    def test_pg_version_read_uses_find_for_both_layouts(self, mock_ssh):
        """PG_VERSION read must use `find` so it locates the file under both:
          - legacy layout: /pgdata/PG_VERSION (pg ≤ 16, data-dir mount)
          - pg18 layout:   /pgdata/<MAJOR>/docker/PG_VERSION (versioned subdir)
        """
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
        ]
        get_postgres_version_mismatch(self.helper)
        first_call_cmd = mock_ssh.call_args_list[0][0][0]
        assert "find /pgdata" in first_call_cmd
        assert "-name PG_VERSION" in first_call_cmd
        # maxdepth 3 is enough to cover /pgdata/<MAJOR>/docker/PG_VERSION
        assert "-maxdepth 3" in first_call_cmd

    @patch.object(VagrantHelper, "ssh")
    def test_mismatch_detected_with_legacy_layout_data(self, mock_ssh):
        """Simulate existing pg16 data at volume root (legacy layout) being
        read against a pg18-configured image. The check must still detect
        the mismatch so `aquarco update` can block the unsafe upgrade.
        """
        # The shell command returns '16' (from /pgdata/PG_VERSION, legacy).
        # The configured version is '18-alpine' (pg18 image in compose).
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="16\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="18-alpine\n", stderr=""),
        ]
        result = get_postgres_version_mismatch(self.helper)
        assert result == ("16", "18")

    @patch.object(VagrantHelper, "ssh")
    def test_mismatch_detected_with_pg18_versioned_layout(self, mock_ssh):
        """Simulate pg18 data at /pgdata/18/docker/PG_VERSION against a pg20
        configured image. The `find` traversal must locate the versioned
        PG_VERSION and return the correct major for comparison.
        """
        mock_ssh.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="18\n", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="20-alpine\n", stderr=""),
        ]
        result = get_postgres_version_mismatch(self.helper)
        assert result == ("18", "20")


class TestGetComposePrefix:
    """Tests for get_compose_prefix().

    The prefix no longer includes ``sudo`` because the agent user is now in
    the docker group (provision.sh runs ``usermod -aG docker agent``). The
    prefix appends ``COMPOSE_ENV_FLAGS`` to pass secrets and version pins via
    ``--env-file`` so compose reads them directly instead of relying on
    inherited shell environment.
    """

    def setup_method(self):
        self.helper = VagrantHelper(vagrant_dir=Path("/fake/vagrant"))

    @patch.object(VagrantHelper, "ssh")
    def test_production_env(self, mock_ssh):
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="production\n", stderr="",
        )
        assert get_compose_prefix(self.helper) == (
            f"docker compose -f compose.prod.yml {COMPOSE_ENV_FLAGS}"
        )

    @patch.object(VagrantHelper, "ssh")
    def test_development_env(self, mock_ssh):
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="development\n", stderr="",
        )
        assert get_compose_prefix(self.helper) == f"docker compose {COMPOSE_ENV_FLAGS}"

    @patch.object(VagrantHelper, "ssh")
    def test_fallback_on_ssh_error(self, mock_ssh):
        mock_ssh.side_effect = VagrantError("connection failed")
        assert get_compose_prefix(self.helper) == f"docker compose {COMPOSE_ENV_FLAGS}"

    @patch.object(VagrantHelper, "ssh")
    def test_fallback_on_generic_exception(self, mock_ssh):
        """Non-VagrantError exceptions also fall back to development."""
        mock_ssh.side_effect = OSError("network down")
        assert get_compose_prefix(self.helper) == f"docker compose {COMPOSE_ENV_FLAGS}"

    @patch.object(VagrantHelper, "ssh")
    def test_multiline_stdout_uses_full_strip(self, mock_ssh):
        """If /etc/aquarco/env contains trailing content, strip handles it."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="production\n\n", stderr="",
        )
        assert get_compose_prefix(self.helper) == (
            f"docker compose -f compose.prod.yml {COMPOSE_ENV_FLAGS}"
        )

    @patch.object(VagrantHelper, "ssh")
    def test_empty_env_defaults_to_development(self, mock_ssh):
        """Empty response (file missing, echo fallback not reached) → development."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr="",
        )
        assert get_compose_prefix(self.helper) == f"docker compose {COMPOSE_ENV_FLAGS}"

    @patch.object(VagrantHelper, "ssh")
    def test_unknown_env_defaults_to_development(self, mock_ssh):
        """Unrecognized env value (e.g., 'staging') → development prefix."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="staging\n", stderr="",
        )
        assert get_compose_prefix(self.helper) == f"docker compose {COMPOSE_ENV_FLAGS}"

    @patch.object(VagrantHelper, "ssh")
    def test_stdout_none_defaults_to_development(self, mock_ssh):
        """None stdout falls back to development."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=None, stderr="",
        )
        # stdout=None → (None or "").strip() = "" → not "production" → dev
        assert get_compose_prefix(self.helper) == f"docker compose {COMPOSE_ENV_FLAGS}"

    @patch.object(VagrantHelper, "ssh")
    def test_ssh_command_uses_sudo_cat(self, mock_ssh):
        """get_compose_prefix must read /etc/aquarco/env via sudo cat."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="development\n", stderr="",
        )
        get_compose_prefix(self.helper)
        cmd = mock_ssh.call_args[0][0]
        assert "sudo cat" in cmd
        assert "/etc/aquarco/env" in cmd

    @patch.object(VagrantHelper, "ssh")
    def test_prefix_does_not_include_sudo_docker(self, mock_ssh):
        """Agent user is in the docker group; docker runs without sudo."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="production\n", stderr="",
        )
        result = get_compose_prefix(self.helper)
        assert result.startswith("docker compose")
        assert "sudo docker" not in result

    @patch.object(VagrantHelper, "ssh")
    def test_prefix_includes_env_file_flags(self, mock_ssh):
        """Both prod and dev prefixes must append --env-file flags."""
        mock_ssh.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="development\n", stderr="",
        )
        result = get_compose_prefix(self.helper)
        assert "--env-file /etc/aquarco/docker-secrets.env" in result
        assert "--env-file /home/agent/aquarco/docker/versions.env" in result


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

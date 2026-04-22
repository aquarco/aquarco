"""Tests for the restore command."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from aquarco_cli.main import app
from aquarco_cli.vagrant import VagrantError

runner = CliRunner()


def _make_vagrant(is_running: bool = True) -> MagicMock:
    v = MagicMock()
    v.is_running.return_value = is_running
    return v


def _make_backup_dir(root: Path, name: str = "20240101T120000", *, sql: str = "-- dump", creds: bool = True) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if sql is not None:
        (d / "aquarco.sql").write_text(sql)
    if creds:
        (d / "github-token").write_text("gh-token")
        (d / "credentials.json").write_text('{"token": "claude"}')
    return d


class TestRestoreVmNotRunning:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_exits_when_vm_not_running(self, mock_cls, tmp_path):
        mock_cls.return_value = _make_vagrant(is_running=False)
        result = runner.invoke(app, ["restore", "--from-file", str(tmp_path)])
        assert result.exit_code == 1
        assert "not running" in result.output.lower()

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_no_ssh_calls_when_vm_not_running(self, mock_cls, tmp_path):
        vagrant = _make_vagrant(is_running=False)
        mock_cls.return_value = vagrant
        runner.invoke(app, ["restore", "--from-file", str(tmp_path)])
        vagrant.ssh.assert_not_called()


class TestRestoreLatestBackup:
    @patch("aquarco_cli.commands.restore.DEFAULT_BACKUP_ROOT")
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_uses_latest_backup_by_default(self, mock_cls, mock_root, tmp_path):
        mock_root.__str__ = lambda _: str(tmp_path)
        # patch the module-level DEFAULT_BACKUP_ROOT used in _latest_backup
        with patch("aquarco_cli.commands.restore.DEFAULT_BACKUP_ROOT", tmp_path):
            _make_backup_dir(tmp_path, "20240101T100000")
            _make_backup_dir(tmp_path, "20240101T120000")  # latest

            vagrant = _make_vagrant()
            mock_cls.return_value = vagrant

            result = runner.invoke(app, ["restore", "--no-creds"])

            assert result.exit_code == 0
            cmds = [c.kwargs.get("input", "") or "" for c in vagrant.ssh.call_args_list]
            assert any("-- dump" in c for c in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_error_when_no_backups(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant
        with patch("aquarco_cli.commands.restore.DEFAULT_BACKUP_ROOT", tmp_path):
            result = runner.invoke(app, ["restore"])
        assert result.exit_code == 1
        assert "no backups" in result.output.lower()


class TestRestoreFromFile:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_from_file_uses_specified_dir(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 0
        cmds = [c.kwargs.get("input", "") or "" for c in vagrant.ssh.call_args_list]
        assert any("-- dump" in c for c in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_from_file_nonexistent_dir_exits(self, mock_cls, tmp_path):
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(tmp_path / "nonexistent")])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestRestoreDatabase:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_pipes_sql_to_psql(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path, sql="INSERT INTO foo VALUES (1);")
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 0
        cmds_inputs = [(c.args[0], c.kwargs.get("input", "")) for c in vagrant.ssh.call_args_list]
        assert any("psql" in cmd and "INSERT INTO foo VALUES (1);" in inp for cmd, inp in cmds_inputs)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_uses_docker_compose_exec(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        # The compose prefix now includes --env-file flags between
        # 'docker compose' and 'exec', so assert on the components separately.
        assert any(
            "docker compose" in cmd and "exec -T postgres" in cmd
            for cmd in cmds
        )

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_runs_as_agent_user(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert any("sudo -u agent" in cmd and "psql" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_missing_sql_file_skips_db_restore(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path, sql=None)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert "skipping" in result.output.lower()
        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert not any("psql" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_failure_exits_nonzero(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        vagrant.ssh.side_effect = VagrantError("psql failed")
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        assert result.exit_code == 1


class TestRestoreCredentials:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_credentials_written_to_vm(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        assert result.exit_code == 0
        cmds_inputs = [(c.args[0], c.kwargs.get("input", "")) for c in vagrant.ssh.call_args_list]
        assert any("github-token" in cmd and "gh-token" in inp for cmd, inp in cmds_inputs)
        assert any("credentials.json" in cmd and '"token": "claude"' in inp for cmd, inp in cmds_inputs)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_credentials_restore_runs_as_agent_user(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert all("sudo -u agent" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_credentials_restore_sets_permissions(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert all("chmod 600" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_missing_credential_file_skipped(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path, creds=False)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        assert "skipping" in result.output.lower()
        vagrant.ssh.assert_not_called()


class TestLatestBackupFunction:
    """Tests for the latest_backup helper in restore.py."""

    def test_returns_most_recent_sorted(self, tmp_path):
        from aquarco_cli.commands.restore import latest_backup
        (tmp_path / "20260101T120000").mkdir()
        (tmp_path / "20260315T080000").mkdir()
        (tmp_path / "20260201T090000").mkdir()
        result = latest_backup(tmp_path)
        assert result == tmp_path / "20260315T080000"

    def test_returns_none_when_no_dirs(self, tmp_path):
        from aquarco_cli.commands.restore import latest_backup
        # Create a file (not a directory) — should be ignored
        (tmp_path / "not-a-dir.txt").touch()
        result = latest_backup(tmp_path)
        assert result is None

    def test_returns_none_when_root_missing(self, tmp_path):
        from aquarco_cli.commands.restore import latest_backup
        result = latest_backup(tmp_path / "nonexistent")
        assert result is None


class TestRestoreComposeCommand:
    """Verify Docker commands in restore use the new 'agent + --env-file' contract.

    After the provision.sh change that adds agent to the docker group, the compose
    prefix no longer prepends ``sudo docker``. Instead, compose is invoked as the
    agent user (via the outer ``sudo -u agent``) and secrets are passed via
    ``--env-file`` so they survive the environment reset performed by sudo.
    """

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_uses_docker_compose(self, mock_cls, tmp_path):
        """psql command must invoke 'docker compose' (agent is in the docker group)."""
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        psql_cmds = [c for c in cmds if "psql" in c]
        assert len(psql_cmds) >= 1
        for cmd in psql_cmds:
            assert "docker compose" in cmd, (
                f"Expected 'docker compose' in restore command. Got: {cmd}"
            )
            # Inner 'sudo docker' was intentionally dropped — agent is in docker group now.
            assert "sudo docker" not in cmd, (
                f"Did not expect inner 'sudo docker' in restore command. Got: {cmd}"
            )

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_db_restore_command_runs_as_agent_with_env_files(self, mock_cls, tmp_path):
        """The restore SSH command wraps 'docker compose --env-file ...' inside 'sudo -u agent bash -c'."""
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        psql_cmds = [c for c in cmds if "psql" in c]
        for cmd in psql_cmds:
            assert "sudo -u agent" in cmd
            assert "docker compose" in cmd
            # Secrets and version pins must be passed via --env-file to survive sudo's env reset.
            assert "--env-file /etc/aquarco/docker-secrets.env" in cmd
            assert "--env-file /home/agent/aquarco/docker/versions.env" in cmd

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_migrations_use_docker_compose(self, mock_cls, tmp_path):
        """Migration step in restore must use 'docker compose' (no inner sudo)."""
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        migration_cmds = [c for c in cmds if "migrations" in c]
        for cmd in migration_cmds:
            assert "docker compose" in cmd, (
                f"Expected 'docker compose' in migration command. Got: {cmd}"
            )
            assert "sudo docker" not in cmd, (
                f"Did not expect inner 'sudo docker' in migration command. Got: {cmd}"
            )


class TestRestorePreamble:
    """Verify the SQL preamble that fixes 'restore broken on fresh VM'.

    Commit 78b0c40 introduced two critical preamble statements that must be
    piped into psql *before* the dump contents, and a later follow-up added
    a third (yoyo tracking-table drop):

      1. ``DROP SCHEMA IF EXISTS aquarco CASCADE`` — wipes migration-seeded
         state on a fresh VM so CREATE TABLE/SCHEMA statements in the dump
         don't collide with already-existing objects.
      2. ``DROP TABLE IF EXISTS ... _yoyo_migration / _yoyo_log /
         _yoyo_version / yoyo_lock`` — clears yoyo's migration-tracking
         tables from the ``public`` schema so the backup restores them
         cleanly. Without this, stale migration history from the VM's
         earlier ``init`` run causes ``run_migrations`` to skip migrations
         that the restored schema still needs.
      3. ``SET session_replication_role = 'replica'`` — disables FK trigger
         enforcement for the duration of the psql session so the circular
         ``stages ↔ tasks`` FK dependency doesn't block COPY statements
         (pg_dump emits data via COPY before re-adding FKs).

    These tests guard against a regression where the preamble is dropped or
    reordered.
    """

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_preamble_drops_aquarco_schema_cascade(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path, sql="-- dump contents")
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        # Locate the psql restore call (not the repository-reset call).
        psql_inputs = [
            c.kwargs.get("input", "")
            for c in vagrant.ssh.call_args_list
            if "psql" in c.args[0] and "-- dump contents" in (c.kwargs.get("input") or "")
        ]
        assert len(psql_inputs) == 1
        assert "DROP SCHEMA IF EXISTS aquarco CASCADE" in psql_inputs[0]

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_preamble_sets_session_replication_role_replica(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path, sql="-- dump contents")
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        psql_inputs = [
            c.kwargs.get("input", "")
            for c in vagrant.ssh.call_args_list
            if "psql" in c.args[0] and "-- dump contents" in (c.kwargs.get("input") or "")
        ]
        assert len(psql_inputs) == 1
        assert "SET session_replication_role = 'replica'" in psql_inputs[0]

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_preamble_drops_yoyo_tracking_tables(self, mock_cls, tmp_path):
        """All four yoyo tracking tables must be dropped with CASCADE so the
        backup can recreate them from a clean slate. Missing any one of these
        lets stale migration history survive the restore and silently cause
        ``run_migrations`` to skip pending migrations."""
        backup_dir = _make_backup_dir(tmp_path, sql="-- dump contents")
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        psql_inputs = [
            c.kwargs.get("input", "")
            for c in vagrant.ssh.call_args_list
            if "psql" in c.args[0] and "-- dump contents" in (c.kwargs.get("input") or "")
        ]
        assert len(psql_inputs) == 1
        body = psql_inputs[0]
        # One DROP TABLE statement covering all four yoyo tables.
        assert "DROP TABLE IF EXISTS" in body
        for table in (
            "public._yoyo_migration",
            "public._yoyo_log",
            "public._yoyo_version",
            "public.yoyo_lock",
        ):
            assert table in body, (
                f"Expected yoyo table {table!r} to be dropped in preamble. "
                f"Got: {body[:400]}"
            )
        assert "CASCADE" in body

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_preamble_yoyo_drop_precedes_dump_contents(self, mock_cls, tmp_path):
        """The yoyo drop must execute *before* the dump body so the restored
        CREATE TABLE statements for the yoyo tables don't collide with the
        pre-existing ones left behind by ``init``."""
        backup_dir = _make_backup_dir(tmp_path, sql="-- DUMP_BODY_MARKER")
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        psql_inputs = [
            c.kwargs.get("input", "")
            for c in vagrant.ssh.call_args_list
            if "psql" in c.args[0] and "-- DUMP_BODY_MARKER" in (c.kwargs.get("input") or "")
        ]
        assert len(psql_inputs) == 1
        body = psql_inputs[0]
        yoyo_idx = body.find("_yoyo_migration")
        dump_idx = body.find("-- DUMP_BODY_MARKER")
        assert 0 <= yoyo_idx < dump_idx, (
            "Yoyo table drop must appear before the dump body."
        )

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_preamble_precedes_dump_contents(self, mock_cls, tmp_path):
        """Preamble must appear *before* the dump so the session is set up
        before any COPY/CREATE statement runs."""
        backup_dir = _make_backup_dir(tmp_path, sql="-- DUMP_BODY_MARKER")
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        psql_inputs = [
            c.kwargs.get("input", "")
            for c in vagrant.ssh.call_args_list
            if "psql" in c.args[0] and "-- DUMP_BODY_MARKER" in (c.kwargs.get("input") or "")
        ]
        assert len(psql_inputs) == 1
        body = psql_inputs[0]
        drop_idx = body.find("DROP SCHEMA IF EXISTS aquarco CASCADE")
        role_idx = body.find("SET session_replication_role")
        dump_idx = body.find("-- DUMP_BODY_MARKER")
        assert 0 <= drop_idx < dump_idx
        assert 0 <= role_idx < dump_idx

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_repo_reset_sql_uses_pending_status(self, mock_cls, tmp_path):
        """After the dump restore, repositories.clone_status is reset to
        'pending' so the clone worker re-verifies them on the new VM."""
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        inputs = [c.kwargs.get("input", "") or "" for c in vagrant.ssh.call_args_list]
        reset_sqls = [i for i in inputs if "clone_status" in i]
        assert len(reset_sqls) == 1
        assert "UPDATE aquarco.repositories" in reset_sqls[0]
        assert "'pending'" in reset_sqls[0]
        # Should only reset ready/error repos, not ones already pending.
        assert "IN ('ready', 'error')" in reset_sqls[0]

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_repo_reset_failure_is_non_fatal(self, mock_cls, tmp_path):
        """If the repo reset step fails, the overall restore still succeeds
        (the clone worker recovers stale statuses on its own)."""
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()

        # First ssh = compose prefix probe, 2nd = psql dump, 3rd = compose prefix probe,
        # 4th = repo reset (raises). Subsequent calls succeed (migrations etc.).
        call_count = {"n": 0}

        def _ssh(cmd, *args, **kwargs):
            call_count["n"] += 1
            if "clone_status" in (kwargs.get("input") or ""):
                raise VagrantError("reset failed")
            m = MagicMock()
            m.stdout = "development\n"
            return m

        vagrant.ssh.side_effect = _ssh
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])
        # Non-fatal: restore still reports completion.
        assert "could not reset clone statuses" in result.output.lower()


class TestRestoreSelectiveFlags:
    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_no_db_skips_database(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-db"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert not any("psql" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_no_creds_skips_credentials(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        runner.invoke(app, ["restore", "--from-file", str(backup_dir), "--no-creds"])

        cmds = [c.args[0] for c in vagrant.ssh.call_args_list]
        assert not any("github-token" in cmd or "credentials.json" in cmd for cmd in cmds)

    @patch("aquarco_cli.commands.restore.VagrantHelper")
    def test_success_message_shown(self, mock_cls, tmp_path):
        backup_dir = _make_backup_dir(tmp_path)
        vagrant = _make_vagrant()
        mock_cls.return_value = vagrant

        result = runner.invoke(app, ["restore", "--from-file", str(backup_dir)])

        assert result.exit_code == 0
        assert "complete" in result.output.lower()

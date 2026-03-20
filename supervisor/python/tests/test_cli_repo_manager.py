"""Unit tests for cli/repo_manager.py — Docker Compose repo management."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.repo_manager import (
    _FRONTEND_PORT_BASE,
    _API_PORT_BASE,
    _POSTGRES_PORT_BASE,
    _allocate_ports,
    _compose_file,
    _env_file,
)


# ---------------------------------------------------------------------------
# _compose_file / _env_file path helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_compose_file_path(self, tmp_path: Path) -> None:
        result = _compose_file(tmp_path)
        assert result == tmp_path / "docker-compose.yml"

    def test_env_file_path(self, tmp_path: Path) -> None:
        result = _env_file(tmp_path)
        assert result == tmp_path / ".env"

    def test_compose_file_parent_is_clone_dir(self, tmp_path: Path) -> None:
        result = _compose_file(tmp_path)
        assert result.parent == tmp_path

    def test_env_file_parent_is_clone_dir(self, tmp_path: Path) -> None:
        result = _env_file(tmp_path)
        assert result.parent == tmp_path


# ---------------------------------------------------------------------------
# _allocate_ports — auto-allocation (no existing .env files)
# ---------------------------------------------------------------------------


class TestAllocatePortsAutoNoExisting:
    def test_first_repo_gets_slot_1(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()

        ports = _allocate_ports("new-repo", repos_root, config_file=None)

        assert ports["frontend"] == _FRONTEND_PORT_BASE + 1
        assert ports["api"] == _API_PORT_BASE + 1
        assert ports["postgres"] == _POSTGRES_PORT_BASE + 1

    def test_returns_all_three_port_keys(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()

        ports = _allocate_ports("any-repo", repos_root, config_file=None)

        assert set(ports.keys()) == {"frontend", "api", "postgres"}

    def test_nonexistent_repos_root_treated_as_empty(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "does-not-exist"
        # Directory doesn't exist — glob yields nothing

        ports = _allocate_ports("my-repo", repos_root, config_file=None)

        assert ports["frontend"] == _FRONTEND_PORT_BASE + 1


# ---------------------------------------------------------------------------
# _allocate_ports — auto-allocation with existing .env files
# ---------------------------------------------------------------------------


class TestAllocatePortsAutoWithExisting:
    def _create_env(self, repos_root: Path, repo_name: str, frontend_port: int) -> None:
        repo_dir = repos_root / repo_name
        repo_dir.mkdir(parents=True)
        (repo_dir / ".env").write_text(f"FRONTEND_PORT={frontend_port}\n")

    def test_next_slot_above_existing(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()
        # Repo already occupies slot 3 (frontend port = base + 3)
        self._create_env(repos_root, "existing-repo", _FRONTEND_PORT_BASE + 3)

        ports = _allocate_ports("new-repo", repos_root, config_file=None)

        assert ports["frontend"] == _FRONTEND_PORT_BASE + 4
        assert ports["api"] == _API_PORT_BASE + 4
        assert ports["postgres"] == _POSTGRES_PORT_BASE + 4

    def test_multiple_existing_repos_uses_highest(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()
        self._create_env(repos_root, "repo-a", _FRONTEND_PORT_BASE + 2)
        self._create_env(repos_root, "repo-b", _FRONTEND_PORT_BASE + 5)
        self._create_env(repos_root, "repo-c", _FRONTEND_PORT_BASE + 1)

        ports = _allocate_ports("new-repo", repos_root, config_file=None)

        assert ports["frontend"] == _FRONTEND_PORT_BASE + 6

    def test_env_file_with_invalid_port_skipped(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()
        bad_repo = repos_root / "bad-repo"
        bad_repo.mkdir()
        (bad_repo / ".env").write_text("FRONTEND_PORT=not-a-number\n")

        ports = _allocate_ports("new-repo", repos_root, config_file=None)

        # Falls back to slot 1
        assert ports["frontend"] == _FRONTEND_PORT_BASE + 1

    def test_env_file_without_frontend_port_line_ignored(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()
        other_repo = repos_root / "other"
        other_repo.mkdir()
        (other_repo / ".env").write_text("API_PORT=4001\nPOSTGRES_PORT=5433\n")

        ports = _allocate_ports("new-repo", repos_root, config_file=None)

        assert ports["frontend"] == _FRONTEND_PORT_BASE + 1


# ---------------------------------------------------------------------------
# _allocate_ports — config file override
# ---------------------------------------------------------------------------


class TestAllocatePortsFromConfig:
    def test_explicit_ports_from_config_take_priority(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()
        # Create an existing .env so auto-alloc would give a different slot
        existing = repos_root / "existing"
        existing.mkdir()
        (existing / ".env").write_text(f"FRONTEND_PORT={_FRONTEND_PORT_BASE + 9}\n")

        mock_cfg = MagicMock()
        mock_repo_cfg = {
            "ports": {"frontend": 8001, "api": 9001, "postgres": 5500}
        }

        with patch(
            "aquarco_supervisor.cli.repo_manager.load_config",
            return_value=mock_cfg,
        ), patch(
            "aquarco_supervisor.cli.repo_manager.get_repository_config",
            return_value=mock_repo_cfg,
        ):
            ports = _allocate_ports("my-repo", repos_root, config_file="/fake/config.yaml")

        assert ports == {"frontend": 8001, "api": 9001, "postgres": 5500}

    def test_config_without_ports_falls_back_to_auto(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()

        mock_cfg = MagicMock()
        mock_repo_cfg = {"ports": None}  # no ports configured

        with patch(
            "aquarco_supervisor.cli.repo_manager.load_config",
            return_value=mock_cfg,
        ), patch(
            "aquarco_supervisor.cli.repo_manager.get_repository_config",
            return_value=mock_repo_cfg,
        ):
            ports = _allocate_ports("my-repo", repos_root, config_file="/fake/config.yaml")

        # Falls back to auto-alloc slot 1
        assert ports["frontend"] == _FRONTEND_PORT_BASE + 1

    def test_repo_not_in_config_falls_back_to_auto(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()

        mock_cfg = MagicMock()

        with patch(
            "aquarco_supervisor.cli.repo_manager.load_config",
            return_value=mock_cfg,
        ), patch(
            "aquarco_supervisor.cli.repo_manager.get_repository_config",
            return_value=None,
        ):
            ports = _allocate_ports("unknown-repo", repos_root, config_file="/fake/config.yaml")

        assert ports["frontend"] == _FRONTEND_PORT_BASE + 1

    def test_config_load_exception_falls_back_to_auto(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()

        with patch(
            "aquarco_supervisor.cli.repo_manager.load_config",
            side_effect=Exception("file not found"),
        ):
            ports = _allocate_ports("my-repo", repos_root, config_file="/fake/config.yaml")

        assert ports["frontend"] == _FRONTEND_PORT_BASE + 1

    def test_none_config_file_skips_config_lookup(self, tmp_path: Path) -> None:
        repos_root = tmp_path / "repos"
        repos_root.mkdir()

        with patch(
            "aquarco_supervisor.cli.repo_manager.load_config"
        ) as mock_load:
            ports = _allocate_ports("my-repo", repos_root, config_file=None)

        mock_load.assert_not_called()
        assert "frontend" in ports


# ---------------------------------------------------------------------------
# cmd_setup — template substitution (integration-style, no docker)
# ---------------------------------------------------------------------------


class TestCmdSetupTemplateSubstitution:
    """Test the template substitution logic without invoking docker."""

    def test_setup_substitutes_all_tokens(self, tmp_path: Path) -> None:
        """Templates are copied/substituted correctly."""
        clone_dir = tmp_path / "my-repo"
        clone_dir.mkdir()

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Create stub templates
        (templates_dir / "docker-compose.repo.yml.tmpl").write_text(
            "version: '3'\nservices:\n  web:\n    image: nginx\n"
        )
        (templates_dir / "repo.env.tmpl").write_text(
            "REPO_NAME=__REPO_NAME__\n"
            "FRONTEND_PORT=__FRONTEND_PORT__\n"
            "API_PORT=__API_PORT__\n"
            "POSTGRES_PORT=__POSTGRES_PORT__\n"
        )

        ports = {"frontend": 3010, "api": 4010, "postgres": 5442}

        from aquarco_supervisor.cli.repo_manager import cmd_setup
        from typer.testing import CliRunner
        from aquarco_supervisor.cli.repo_manager import repo_app

        runner = CliRunner()
        result = runner.invoke(
            repo_app,
            [
                "setup",
                "my-repo",
                str(clone_dir),
                json.dumps(ports),
                "--templates-dir",
                str(templates_dir),
            ],
        )

        assert result.exit_code == 0, result.output

        compose_text = (clone_dir / "docker-compose.yml").read_text()
        assert "nginx" in compose_text  # verbatim copy

        env_text = (clone_dir / ".env").read_text()
        assert "REPO_NAME=my-repo" in env_text
        assert "FRONTEND_PORT=3010" in env_text
        assert "API_PORT=4010" in env_text
        assert "POSTGRES_PORT=5442" in env_text

    def test_setup_fails_if_clone_dir_missing(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner
        from aquarco_supervisor.cli.repo_manager import repo_app

        runner = CliRunner()
        result = runner.invoke(
            repo_app,
            [
                "setup",
                "some-repo",
                str(tmp_path / "nonexistent"),
                json.dumps({"frontend": 3001, "api": 4001, "postgres": 5433}),
            ],
        )

        assert result.exit_code != 0

    def test_setup_fails_on_invalid_ports_json(self, tmp_path: Path) -> None:
        clone_dir = tmp_path / "repo"
        clone_dir.mkdir()

        from typer.testing import CliRunner
        from aquarco_supervisor.cli.repo_manager import repo_app

        runner = CliRunner()
        result = runner.invoke(
            repo_app,
            [
                "setup",
                "some-repo",
                str(clone_dir),
                "not-json",
            ],
        )

        assert result.exit_code != 0

    def test_setup_fails_if_template_missing(self, tmp_path: Path) -> None:
        clone_dir = tmp_path / "repo"
        clone_dir.mkdir()
        empty_templates = tmp_path / "templates"
        empty_templates.mkdir()
        # No template files created

        from typer.testing import CliRunner
        from aquarco_supervisor.cli.repo_manager import repo_app

        runner = CliRunner()
        result = runner.invoke(
            repo_app,
            [
                "setup",
                "some-repo",
                str(clone_dir),
                json.dumps({"frontend": 3001, "api": 4001, "postgres": 5433}),
                "--templates-dir",
                str(empty_templates),
            ],
        )

        assert result.exit_code != 0

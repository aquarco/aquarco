"""Tests for the supervisor config CLI commands (update / export)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click.exceptions
import pytest

from aquarco_supervisor.cli.config import _export, _update


def _make_mock_cfg(agents_dir: str = "/tmp/config/agents/definitions", pipelines_file: str | None = "/tmp/pipelines.yaml") -> MagicMock:
    """Build a mock config object matching the shape load_config returns."""
    cfg = MagicMock()
    cfg.spec.database.url = "postgresql://test:test@localhost/test"
    cfg.spec.agents_dir = agents_dir
    cfg.spec.pipelines_file = pipelines_file
    return cfg


class TestUpdate:
    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.sync_pipeline_definitions_to_db", new_callable=AsyncMock, return_value=2)
    @patch("aquarco_supervisor.cli.config.sync_all_agent_definitions_to_db", new_callable=AsyncMock, return_value=5)
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_update_calls_sync_functions(self, mock_load: MagicMock, mock_db_cls: MagicMock, mock_sync_agents: AsyncMock, mock_sync_pipelines: AsyncMock) -> None:
        mock_load.return_value = _make_mock_cfg()
        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db

        await _update("/fake/config.yaml")

        mock_load.assert_called_once_with("/fake/config.yaml")
        mock_db.connect.assert_awaited_once()
        mock_sync_agents.assert_awaited_once()
        mock_sync_pipelines.assert_awaited_once()
        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_update_config_error_exits(self, mock_load: MagicMock, mock_db_cls: MagicMock) -> None:
        from aquarco_supervisor.exceptions import ConfigValidationError

        mock_load.side_effect = ConfigValidationError("bad config")

        with pytest.raises(click.exceptions.Exit) as exc_info:
            await _update("/fake/config.yaml")
        assert exc_info.value.exit_code == 1
        mock_db_cls.return_value.connect.assert_not_called()

    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.sync_all_agent_definitions_to_db", new_callable=AsyncMock, return_value=3)
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_update_no_pipelines_file(self, mock_load: MagicMock, mock_db_cls: MagicMock, mock_sync_agents: AsyncMock) -> None:
        mock_load.return_value = _make_mock_cfg(pipelines_file=None)
        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db

        await _update("/fake/config.yaml")

        mock_sync_agents.assert_awaited_once()
        mock_db.close.assert_awaited_once()


class TestExport:
    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.export_pipeline_definitions_to_file", new_callable=AsyncMock, return_value=2)
    @patch("aquarco_supervisor.cli.config.export_agent_definitions_to_files", new_callable=AsyncMock, return_value=4)
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_export_calls_export_functions(self, mock_load: MagicMock, mock_db_cls: MagicMock, mock_export_agents: AsyncMock, mock_export_pipelines: AsyncMock) -> None:
        mock_load.return_value = _make_mock_cfg()
        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db

        await _export("/fake/config.yaml")

        mock_load.assert_called_once_with("/fake/config.yaml")
        mock_db.connect.assert_awaited_once()
        mock_export_agents.assert_awaited_once()
        mock_export_pipelines.assert_awaited_once()
        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_export_config_error_exits(self, mock_load: MagicMock, mock_db_cls: MagicMock) -> None:
        from aquarco_supervisor.exceptions import ConfigValidationError

        mock_load.side_effect = ConfigValidationError("bad config")

        with pytest.raises(click.exceptions.Exit) as exc_info:
            await _export("/fake/config.yaml")
        assert exc_info.value.exit_code == 1
        mock_db_cls.return_value.connect.assert_not_called()

    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.export_agent_definitions_to_files", new_callable=AsyncMock, return_value=2)
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_export_no_pipelines_file(self, mock_load: MagicMock, mock_db_cls: MagicMock, mock_export_agents: AsyncMock) -> None:
        mock_load.return_value = _make_mock_cfg(pipelines_file=None)
        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db

        await _export("/fake/config.yaml")

        mock_export_agents.assert_awaited_once()
        mock_db.close.assert_awaited_once()


class TestUpdateCleanup:
    """Verify db.close() is always called, even on error."""

    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.sync_all_agent_definitions_to_db", new_callable=AsyncMock)
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_update_closes_db_on_sync_error(self, mock_load: MagicMock, mock_db_cls: MagicMock, mock_sync_agents: AsyncMock) -> None:
        mock_load.return_value = _make_mock_cfg()
        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db
        mock_sync_agents.side_effect = RuntimeError("sync failed")

        with pytest.raises(RuntimeError, match="sync failed"):
            await _update("/fake/config.yaml")

        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.export_agent_definitions_to_files", new_callable=AsyncMock)
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_export_closes_db_on_error(self, mock_load: MagicMock, mock_db_cls: MagicMock, mock_export_agents: AsyncMock) -> None:
        mock_load.return_value = _make_mock_cfg()
        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db
        mock_export_agents.side_effect = RuntimeError("export failed")

        with pytest.raises(RuntimeError, match="export failed"):
            await _export("/fake/config.yaml")

        mock_db.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("aquarco_supervisor.cli.config.sync_all_agent_definitions_to_db", new_callable=AsyncMock, return_value=5)
    @patch("aquarco_supervisor.cli.config.Database")
    @patch("aquarco_supervisor.cli.config.load_config")
    async def test_update_passes_schema_paths(self, mock_load: MagicMock, mock_db_cls: MagicMock, mock_sync_agents: AsyncMock) -> None:
        mock_load.return_value = _make_mock_cfg()
        mock_db = AsyncMock()
        mock_db_cls.return_value = mock_db

        with patch("aquarco_supervisor.cli.config.Path") as mock_path_cls:
            # Make schema files "exist"
            mock_system_schema = MagicMock()
            mock_system_schema.exists.return_value = True
            mock_pipeline_schema = MagicMock()
            mock_pipeline_schema.exists.return_value = True

            mock_agents_dir = MagicMock()
            mock_path_cls.return_value = mock_agents_dir
            mock_agents_dir.parent.parent.__truediv__ = MagicMock(return_value=MagicMock())
            mock_schema_dir = mock_agents_dir.parent.parent / "schemas"
            mock_schema_dir.__truediv__ = MagicMock(side_effect=[mock_system_schema, mock_pipeline_schema])

            await _update("/fake/config.yaml")

        mock_sync_agents.assert_awaited_once()
        mock_db.close.assert_awaited_once()

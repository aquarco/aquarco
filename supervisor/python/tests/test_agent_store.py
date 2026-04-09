"""Tests for agent_store.py — agent definition CRUD.

Covers:
- store_agent_definitions with legacy k8s-style documents (lines 118-125)
- store_agent_definitions with flat frontmatter documents
- Skipping documents with empty name
- _agent_name helper in load_and_store_agent_definitions (line 234)
- export_agent_definitions_to_files schema validation failure (lines 335-343)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.agent_store import (
    store_agent_definitions,
    export_agent_definitions_to_files,
    read_agent_definitions_from_db,
)
from aquarco_supervisor.database import Database


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    db.execute = AsyncMock()
    return db


# -----------------------------------------------------------------------
# store_agent_definitions — format detection
# -----------------------------------------------------------------------


class TestStoreAgentDefinitions:
    @pytest.mark.asyncio
    async def test_k8s_style_document(self, mock_db):
        """Legacy k8s-style documents (with 'metadata' key) should be parsed correctly."""
        definitions = [
            {
                "metadata": {
                    "name": "planner-agent",
                    "version": "1.0.0",
                    "description": "Plans things",
                    "labels": {"role": "planner"},
                },
                "spec": {
                    "model": "opus",
                    "categories": ["plan"],
                },
            }
        ]
        count = await store_agent_definitions(
            mock_db, definitions, source="default", agent_group="system",
        )
        assert count == 1
        # Should have called execute for deactivation + upsert
        assert mock_db.execute.await_count >= 2

    @pytest.mark.asyncio
    async def test_flat_frontmatter_document(self, mock_db):
        """Flat frontmatter documents (no 'metadata' key) should be parsed correctly."""
        definitions = [
            {
                "name": "review-agent",
                "version": "2.0.0",
                "description": "Reviews code",
                "model": "sonnet",
                "categories": ["review"],
            }
        ]
        count = await store_agent_definitions(
            mock_db, definitions, source="default", agent_group="pipeline",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_skips_empty_name(self, mock_db):
        """Documents with empty/missing name should be skipped."""
        definitions = [
            {"name": "", "version": "1.0.0"},
            {"version": "1.0.0"},  # no name key at all
        ]
        count = await store_agent_definitions(
            mock_db, definitions, source="default", agent_group="pipeline",
        )
        assert count == 0
        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_agent_group_raises(self, mock_db):
        """Invalid agent_group should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid agent_group"):
            await store_agent_definitions(
                mock_db, [], source="default", agent_group="invalid",
            )

    @pytest.mark.asyncio
    async def test_mixed_formats(self, mock_db):
        """Mix of k8s and flat format documents should both be processed."""
        definitions = [
            {
                "metadata": {"name": "agent-a", "version": "1.0.0"},
                "spec": {"model": "opus"},
            },
            {
                "name": "agent-b",
                "version": "1.0.0",
                "model": "sonnet",
            },
        ]
        count = await store_agent_definitions(
            mock_db, definitions, source="default", agent_group="pipeline",
        )
        assert count == 2


# -----------------------------------------------------------------------
# export_agent_definitions_to_files — schema validation failure
# -----------------------------------------------------------------------


class TestExportAgentDefinitions:
    @pytest.mark.asyncio
    async def test_schema_validation_failure_skips_agent(self, mock_db, tmp_path):
        """Agents that fail schema validation should be skipped without crashing."""
        import jsonschema

        mock_db.fetch = AsyncMock(return_value=[
            {
                "name": "bad-agent",
                "version": "1.0.0",
                "description": "Fails validation",
                "labels": {},
                "spec": {"model": "opus"},
                "prompt_body": "# Bad agent\n",
            }
        ])

        # Patch read_agent_definitions_from_db to return a k8s-style doc
        with patch(
            "aquarco_supervisor.agent_store.read_agent_definitions_from_db",
            new_callable=AsyncMock,
            return_value=[
                {
                    "metadata": {
                        "name": "bad-agent",
                        "version": "1.0.0",
                        "description": "Fails validation",
                    },
                    "spec": {"model": "opus", "promptInline": "# Bad\n"},
                }
            ],
        ):
            # Use a schema that the doc won't satisfy
            with patch(
                "aquarco_supervisor.agent_store.validate_agent_definition",
                side_effect=jsonschema.ValidationError("missing required field"),
            ):
                count = await export_agent_definitions_to_files(
                    mock_db, tmp_path, schema={"type": "object"},
                )
                # Agent should be skipped
                assert count == 0
                # No file should be written
                assert list(tmp_path.iterdir()) == []

    @pytest.mark.asyncio
    async def test_export_without_schema_writes_all(self, mock_db, tmp_path):
        """Without schema validation, all agents should be exported."""
        with patch(
            "aquarco_supervisor.agent_store.read_agent_definitions_from_db",
            new_callable=AsyncMock,
            return_value=[
                {
                    "metadata": {
                        "name": "good-agent",
                        "version": "1.0.0",
                        "description": "Works fine",
                    },
                    "spec": {
                        "model": "sonnet",
                        "promptInline": "# Good agent\nDoes good things.\n",
                    },
                }
            ],
        ):
            count = await export_agent_definitions_to_files(
                mock_db, tmp_path, schema=None,
            )
            assert count == 1
            exported = tmp_path / "good-agent.md"
            assert exported.exists()
            content = exported.read_text()
            assert "name: good-agent" in content
            assert "# Good agent" in content

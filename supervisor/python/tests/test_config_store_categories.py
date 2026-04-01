"""Tests for config_store pipeline categories storage."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.config_store import store_pipeline_definitions
from aquarco_supervisor.database import Database


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    db.execute = AsyncMock(return_value=None)
    return db


@pytest.mark.asyncio
async def test_store_pipeline_with_categories(mock_db: AsyncMock) -> None:
    """store_pipeline_definitions stores categories JSONB in the pipeline_definitions table."""
    pipelines = [
        {
            "name": "feature-pipeline",
            "version": "1.0.0",
            "trigger": {"labels": ["feature"]},
            "stages": [
                {"name": "analysis", "category": "analyze", "required": True},
            ],
            "categories": {
                "analyze": {"type": "object", "required": ["risks"]},
                "design": {"type": "object"},
            },
        }
    ]

    count = await store_pipeline_definitions(mock_db, pipelines)
    assert count == 1

    # Verify the INSERT call includes categories
    insert_call = mock_db.execute.call_args_list[1]  # Second call is the INSERT
    params = insert_call[0][1]
    assert "categories" in params
    categories_json = json.loads(params["categories"])
    assert categories_json["analyze"]["type"] == "object"
    assert "risks" in categories_json["analyze"]["required"]
    assert categories_json["design"]["type"] == "object"


@pytest.mark.asyncio
async def test_store_pipeline_without_categories(mock_db: AsyncMock) -> None:
    """Pipeline without categories stores empty dict."""
    pipelines = [
        {
            "name": "simple-pipeline",
            "version": "1.0.0",
            "trigger": {"labels": ["bug"]},
            "stages": [{"name": "fix", "category": "implement"}],
        }
    ]

    count = await store_pipeline_definitions(mock_db, pipelines)
    assert count == 1

    insert_call = mock_db.execute.call_args_list[1]
    params = insert_call[0][1]
    categories_json = json.loads(params["categories"])
    assert categories_json == {}


@pytest.mark.asyncio
async def test_store_pipeline_with_conditions_in_stages(mock_db: AsyncMock) -> None:
    """Stages with structured conditions are stored in the stages JSON."""
    pipelines = [
        {
            "name": "conditional-pipeline",
            "version": "1.0.0",
            "trigger": {"labels": ["feature"]},
            "stages": [
                {
                    "name": "review",
                    "category": "review",
                    "conditions": [
                        {"simple": "severity == blocking", "no": "fix", "maxRepeats": 3},
                    ],
                },
            ],
            "categories": {},
        }
    ]

    count = await store_pipeline_definitions(mock_db, pipelines)
    assert count == 1

    insert_call = mock_db.execute.call_args_list[1]
    params = insert_call[0][1]
    stages_json = json.loads(params["stages"])
    assert len(stages_json) == 1
    assert stages_json[0]["conditions"][0]["simple"] == "severity == blocking"
    assert stages_json[0]["conditions"][0]["no"] == "fix"
    assert stages_json[0]["conditions"][0]["maxRepeats"] == 3


@pytest.mark.asyncio
async def test_store_multiple_pipelines(mock_db: AsyncMock) -> None:
    """Multiple pipelines each get stored."""
    pipelines = [
        {
            "name": "pipeline-a",
            "version": "1.0.0",
            "trigger": {"labels": ["a"]},
            "stages": [],
            "categories": {"cat_a": {"type": "object"}},
        },
        {
            "name": "pipeline-b",
            "version": "2.0.0",
            "trigger": {"labels": ["b"]},
            "stages": [],
            "categories": {"cat_b": {"type": "string"}},
        },
    ]

    count = await store_pipeline_definitions(mock_db, pipelines)
    assert count == 2


@pytest.mark.asyncio
async def test_store_pipeline_skips_unnamed(mock_db: AsyncMock) -> None:
    """Pipelines without a name are skipped."""
    pipelines: list[dict[str, Any]] = [
        {
            "version": "1.0.0",
            "trigger": {"labels": ["test"]},
            "stages": [],
        }
    ]

    count = await store_pipeline_definitions(mock_db, pipelines)
    assert count == 0
    # Only the skipped pipeline => no execute calls
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_store_deactivates_previous_versions(mock_db: AsyncMock) -> None:
    """Old versions are deactivated before upserting."""
    pipelines = [
        {
            "name": "test-pipeline",
            "version": "2.0.0",
            "trigger": {},
            "stages": [],
            "categories": {},
        }
    ]

    await store_pipeline_definitions(mock_db, pipelines)

    # First call: UPDATE to deactivate
    deactivate_call = mock_db.execute.call_args_list[0]
    sql = deactivate_call[0][0]
    assert "UPDATE pipeline_definitions" in sql
    assert "is_active = false" in sql
    params = deactivate_call[0][1]
    assert params["name"] == "test-pipeline"
    assert params["version"] == "2.0.0"

    # Second call: INSERT/UPSERT
    insert_call = mock_db.execute.call_args_list[1]
    sql = insert_call[0][0]
    assert "INSERT INTO pipeline_definitions" in sql
    assert "categories" in sql

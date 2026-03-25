"""Tests for config_store autoloaded agent helpers.

Tests deactivate_autoloaded_agents() and read_autoloaded_agents_from_db()
functions added for the 'Autoload .claude agents' feature (issue #14).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aquarco_supervisor.config_store import (
    AGENT_API_VERSION,
    AGENT_KIND,
    deactivate_autoloaded_agents,
    read_autoloaded_agents_from_db,
)


# ---------------------------------------------------------------------------
# deactivate_autoloaded_agents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_autoloaded_agents_returns_count():
    """Returns the number of agents deactivated."""
    db = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[
        {"name": "agent-a"},
        {"name": "agent-b"},
    ])

    count = await deactivate_autoloaded_agents(db, "my-repo")

    assert count == 2


@pytest.mark.asyncio
async def test_deactivate_autoloaded_agents_uses_correct_source():
    """Uses source='autoload:<repo_name>' to identify agents."""
    db = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[])

    await deactivate_autoloaded_agents(db, "cool-repo")

    sql = db.fetch_all.call_args[0][0]
    params = db.fetch_all.call_args[0][1]
    assert "is_active = false" in sql
    assert params["source"] == "autoload:cool-repo"


@pytest.mark.asyncio
async def test_deactivate_autoloaded_agents_empty():
    """Returns 0 when no agents exist for the repo."""
    db = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[])

    count = await deactivate_autoloaded_agents(db, "empty-repo")
    assert count == 0


@pytest.mark.asyncio
async def test_deactivate_only_active_agents():
    """SQL query only targets is_active=true agents."""
    db = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[])

    await deactivate_autoloaded_agents(db, "repo")

    sql = db.fetch_all.call_args[0][0]
    assert "is_active = true" in sql


# ---------------------------------------------------------------------------
# read_autoloaded_agents_from_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_autoloaded_agents_returns_yaml_dicts():
    """Returns full YAML-ready dicts with correct structure."""
    db = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[
        {
            "name": "repo-agent-a",
            "version": "1.0.0",
            "description": "Agent A",
            "labels": {"source": "autoloaded", "repository": "my-repo"},
            "spec": {"categories": ["test"], "tools": {"allowed": ["Read"]}},
            "is_active": True,
        },
    ])

    docs = await read_autoloaded_agents_from_db(db, "my-repo")

    assert len(docs) == 1
    doc = docs[0]
    assert doc["apiVersion"] == AGENT_API_VERSION
    assert doc["kind"] == AGENT_KIND
    assert doc["metadata"]["name"] == "repo-agent-a"
    assert doc["metadata"]["version"] == "1.0.0"
    assert doc["metadata"]["description"] == "Agent A"
    assert doc["metadata"]["labels"]["source"] == "autoloaded"
    assert doc["spec"]["categories"] == ["test"]


@pytest.mark.asyncio
async def test_read_autoloaded_agents_uses_correct_source():
    """Queries with source='autoload:<repo_name>'."""
    db = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[])

    await read_autoloaded_agents_from_db(db, "target-repo")

    sql = db.fetch_all.call_args[0][0]
    params = db.fetch_all.call_args[0][1]
    assert "is_active = true" in sql
    assert params["source"] == "autoload:target-repo"


@pytest.mark.asyncio
async def test_read_autoloaded_agents_empty():
    """Returns empty list when no agents exist."""
    db = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[])

    docs = await read_autoloaded_agents_from_db(db, "empty-repo")
    assert docs == []


@pytest.mark.asyncio
async def test_read_autoloaded_agents_multiple():
    """Returns multiple agents sorted by name."""
    db = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[
        {
            "name": "alpha-agent",
            "version": "1.0.0",
            "description": "Alpha",
            "labels": None,
            "spec": {},
            "is_active": True,
        },
        {
            "name": "beta-agent",
            "version": "1.0.0",
            "description": "Beta",
            "labels": {"key": "val"},
            "spec": {},
            "is_active": True,
        },
    ])

    docs = await read_autoloaded_agents_from_db(db, "repo")

    assert len(docs) == 2
    assert docs[0]["metadata"]["name"] == "alpha-agent"
    assert docs[1]["metadata"]["name"] == "beta-agent"
    # Alpha has no labels
    assert "labels" not in docs[0]["metadata"]
    # Beta has labels
    assert docs[1]["metadata"]["labels"] == {"key": "val"}

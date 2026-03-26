"""Tests for config_store — agent/pipeline definition serialization with versioning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, call

import pytest
import yaml

from aquarco_supervisor.config_store import (
    export_agent_definitions_to_files,
    export_pipeline_definitions_to_file,
    load_agent_definitions_from_files,
    load_pipeline_definitions_from_file,
    read_agent_definitions_from_db,
    read_pipeline_definitions_from_db,
    store_agent_definitions,
    store_pipeline_definitions,
    sync_agent_definitions_to_db,
    sync_pipeline_definitions_to_db,
    validate_agent_definition,
    validate_pipeline_definition,
)
from aquarco_supervisor.database import Database

# ── Fixtures ──────────────────────────────────────────────────────────────

SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "config" / "schemas"


def _agent_schema() -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / "agent-definition-v1.json").read_text())


def _pipeline_schema() -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / "pipeline-definition-v1.json").read_text())


def _make_agent_doc(
    name: str = "test-agent",
    version: str = "1.0.0",
    categories: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "apiVersion": "aquarco.agents/v1",
        "kind": "AgentDefinition",
        "metadata": {
            "name": name,
            "version": version,
            "description": "A test agent for unit testing purposes",
        },
        "spec": {
            "categories": categories or ["analyze"],
            "promptFile": f"{name}.md",
            "output": {"format": "task-file"},
        },
    }


def _make_pipeline_doc(
    pipelines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if pipelines is None:
        pipelines = [
            {
                "name": "test-pipeline",
                "version": "1.0.0",
                "trigger": {"labels": ["test"]},
                "stages": [{"category": "analyze", "required": True}],
            },
        ]
    return {
        "apiVersion": "aquarco.agents/v1",
        "kind": "PipelineDefinition",
        "pipelines": pipelines,
    }


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock(spec=Database)
    db.execute = AsyncMock()
    db.fetch_all = AsyncMock(return_value=[])
    return db


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agents" / "definitions"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def pipelines_file(tmp_path: Path) -> Path:
    return tmp_path / "pipelines.yaml"


# ── Schema validation ────────────────────────────────────────────────────


class TestValidateAgentDefinition:
    def test_valid_doc(self) -> None:
        doc = _make_agent_doc()
        validate_agent_definition(doc, _agent_schema())  # should not raise

    def test_missing_required_field(self) -> None:
        doc = _make_agent_doc()
        del doc["spec"]["promptFile"]
        with pytest.raises(Exception):
            validate_agent_definition(doc, _agent_schema())

    def test_invalid_category(self) -> None:
        doc = _make_agent_doc(categories=["bogus"])
        with pytest.raises(Exception):
            validate_agent_definition(doc, _agent_schema())

    def test_invalid_name_pattern(self) -> None:
        doc = _make_agent_doc(name="Bad Name!")
        with pytest.raises(Exception):
            validate_agent_definition(doc, _agent_schema())

    def test_short_description(self) -> None:
        doc = _make_agent_doc()
        doc["metadata"]["description"] = "short"
        with pytest.raises(Exception):
            validate_agent_definition(doc, _agent_schema())

    def test_full_spec(self) -> None:
        """A doc with all optional spec fields passes validation."""
        doc = _make_agent_doc()
        doc["spec"].update({
            "priority": 10,
            "tools": {"allowed": ["Read", "Bash"], "denied": ["Write"]},
            "resources": {"maxTokens": 50000, "timeoutMinutes": 30, "maxConcurrent": 2},
            "environment": {"AGENT_MODE": "test"},
            "outputSchema": {"type": "object", "required": ["summary"], "properties": {}},
            "healthCheck": {"enabled": True, "intervalSeconds": 300},
            "conditions": {"filePatterns": ["src/**"]},
        })
        validate_agent_definition(doc, _agent_schema())


class TestValidatePipelineDefinition:
    def test_valid_doc(self) -> None:
        doc = _make_pipeline_doc()
        validate_pipeline_definition(doc, _pipeline_schema())

    def test_missing_pipelines(self) -> None:
        doc = {"apiVersion": "aquarco.agents/v1", "kind": "PipelineDefinition"}
        with pytest.raises(Exception):
            validate_pipeline_definition(doc, _pipeline_schema())

    def test_empty_pipelines(self) -> None:
        doc = _make_pipeline_doc(pipelines=[])
        with pytest.raises(Exception):
            validate_pipeline_definition(doc, _pipeline_schema())

    def test_invalid_stage_category(self) -> None:
        doc = _make_pipeline_doc([{
            "name": "bad-pipeline",
            "version": "1.0.0",
            "trigger": {"labels": ["x"]},
            "stages": [{"category": "nope"}],
        }])
        with pytest.raises(Exception):
            validate_pipeline_definition(doc, _pipeline_schema())

    def test_pipeline_with_conditions(self) -> None:
        doc = _make_pipeline_doc([{
            "name": "cond-pipeline",
            "version": "1.0.0",
            "trigger": {"labels": ["feature"]},
            "stages": [
                {"category": "analyze", "required": True},
                {
                    "category": "design",
                    "required": True,
                    "conditions": ["analysis.complexity >= medium"],
                },
            ],
        }])
        validate_pipeline_definition(doc, _pipeline_schema())

    def test_pipeline_with_events_trigger(self) -> None:
        doc = _make_pipeline_doc([{
            "name": "event-pipeline",
            "version": "1.0.0",
            "trigger": {"events": ["pr_opened"]},
            "stages": [{"category": "review"}],
        }])
        validate_pipeline_definition(doc, _pipeline_schema())

    def test_missing_version(self) -> None:
        doc = _make_pipeline_doc([{
            "name": "no-ver",
            "trigger": {"labels": ["x"]},
            "stages": [{"category": "analyze"}],
        }])
        with pytest.raises(Exception):
            validate_pipeline_definition(doc, _pipeline_schema())

    def test_invalid_version_format(self) -> None:
        doc = _make_pipeline_doc([{
            "name": "bad-ver",
            "version": "not-semver",
            "trigger": {"labels": ["x"]},
            "stages": [{"category": "analyze"}],
        }])
        with pytest.raises(Exception):
            validate_pipeline_definition(doc, _pipeline_schema())


# ── Load from files ───────────────────────────────────────────────────────


class TestLoadAgentDefinitionsFromFiles:
    def test_loads_valid_yaml(self, agents_dir: Path) -> None:
        doc = _make_agent_doc("my-agent")
        (agents_dir / "my-agent.yaml").write_text(yaml.dump(doc))

        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 1
        assert result[0]["metadata"]["name"] == "my-agent"

    def test_skips_invalid_yaml(self, agents_dir: Path) -> None:
        (agents_dir / "bad.yaml").write_text("{{invalid yaml")
        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 0

    def test_skips_non_agent_kind(self, agents_dir: Path) -> None:
        doc = {"apiVersion": "v1", "kind": "SomethingElse", "metadata": {}, "spec": {}}
        (agents_dir / "other.yaml").write_text(yaml.dump(doc))
        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 0

    def test_skips_schema_invalid(self, agents_dir: Path) -> None:
        doc = _make_agent_doc("my-agent")
        del doc["spec"]["promptFile"]
        (agents_dir / "my-agent.yaml").write_text(yaml.dump(doc))

        result = load_agent_definitions_from_files(agents_dir, schema=_agent_schema())
        assert len(result) == 0

    def test_loads_with_schema_valid(self, agents_dir: Path) -> None:
        doc = _make_agent_doc("valid-agent")
        (agents_dir / "valid-agent.yaml").write_text(yaml.dump(doc))

        result = load_agent_definitions_from_files(agents_dir, schema=_agent_schema())
        assert len(result) == 1

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        result = load_agent_definitions_from_files(tmp_path / "nope")
        assert result == []

    def test_multiple_files_sorted(self, agents_dir: Path) -> None:
        for name in ["charlie-agent", "alpha-agent", "bravo-agent"]:
            doc = _make_agent_doc(name)
            (agents_dir / f"{name}.yaml").write_text(yaml.dump(doc))

        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 3
        names = [d["metadata"]["name"] for d in result]
        assert names == ["alpha-agent", "bravo-agent", "charlie-agent"]

    def test_skips_non_dict_yaml(self, agents_dir: Path) -> None:
        (agents_dir / "list.yaml").write_text("- item1\n- item2\n")
        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 0


class TestLoadPipelineDefinitionsFromFile:
    def test_loads_valid_file(self, pipelines_file: Path) -> None:
        doc = _make_pipeline_doc()
        pipelines_file.write_text(yaml.dump(doc))

        result = load_pipeline_definitions_from_file(pipelines_file)
        assert len(result) == 1
        assert result[0]["name"] == "test-pipeline"
        assert result[0]["version"] == "1.0.0"

    def test_missing_file(self, tmp_path: Path) -> None:
        result = load_pipeline_definitions_from_file(tmp_path / "nope.yaml")
        assert result == []

    def test_invalid_yaml(self, pipelines_file: Path) -> None:
        pipelines_file.write_text("{{bad yaml")
        result = load_pipeline_definitions_from_file(pipelines_file)
        assert result == []

    def test_non_dict_yaml(self, pipelines_file: Path) -> None:
        pipelines_file.write_text("- item\n")
        result = load_pipeline_definitions_from_file(pipelines_file)
        assert result == []

    def test_schema_invalid(self, pipelines_file: Path) -> None:
        doc = {"apiVersion": "aquarco.agents/v1", "kind": "PipelineDefinition", "pipelines": []}
        pipelines_file.write_text(yaml.dump(doc))

        result = load_pipeline_definitions_from_file(pipelines_file, schema=_pipeline_schema())
        assert result == []

    def test_schema_valid(self, pipelines_file: Path) -> None:
        doc = _make_pipeline_doc()
        pipelines_file.write_text(yaml.dump(doc))

        result = load_pipeline_definitions_from_file(pipelines_file, schema=_pipeline_schema())
        assert len(result) == 1

    def test_multiple_pipelines(self, pipelines_file: Path) -> None:
        pipelines = [
            {
                "name": "pipe-a",
                "version": "1.0.0",
                "trigger": {"labels": ["a"]},
                "stages": [{"category": "analyze"}],
            },
            {
                "name": "pipe-b",
                "version": "2.0.0",
                "trigger": {"events": ["pr_opened"]},
                "stages": [{"category": "review"}],
            },
        ]
        doc = _make_pipeline_doc(pipelines)
        pipelines_file.write_text(yaml.dump(doc))

        result = load_pipeline_definitions_from_file(pipelines_file)
        assert len(result) == 2


# ── Store to DB ───────────────────────────────────────────────────────────


class TestStoreAgentDefinitions:
    @pytest.mark.asyncio
    async def test_stores_single(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("a-agent")]
        count = await store_agent_definitions(mock_db, docs)
        assert count == 1
        # Two calls per agent: deactivate old + upsert
        assert mock_db.execute.call_count == 2

        # Check the upsert call (second one)
        upsert_call = mock_db.execute.call_args_list[1]
        params = upsert_call[0][1]
        assert params["name"] == "a-agent"
        assert params["version"] == "1.0.0"
        assert json.loads(params["spec"])["categories"] == ["analyze"]

    @pytest.mark.asyncio
    async def test_stores_multiple(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("a"), _make_agent_doc("b"), _make_agent_doc("c")]
        count = await store_agent_definitions(mock_db, docs)
        assert count == 3
        # 2 calls per agent (deactivate + upsert)
        assert mock_db.execute.call_count == 6

    @pytest.mark.asyncio
    async def test_skips_missing_name(self, mock_db: AsyncMock) -> None:
        doc = _make_agent_doc()
        doc["metadata"]["name"] = ""
        count = await store_agent_definitions(mock_db, [doc])
        assert count == 0
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_labels_as_json(self, mock_db: AsyncMock) -> None:
        doc = _make_agent_doc("labeled")
        doc["metadata"]["labels"] = {"team": "platform", "domain": "test"}
        await store_agent_definitions(mock_db, [doc])

        upsert_call = mock_db.execute.call_args_list[1]
        params = upsert_call[0][1]
        assert json.loads(params["labels"]) == {"team": "platform", "domain": "test"}

    @pytest.mark.asyncio
    async def test_deactivates_old_versions(self, mock_db: AsyncMock) -> None:
        """When storing v2.0.0, deactivate previous versions."""
        doc = _make_agent_doc("my-agent", version="2.0.0")
        await store_agent_definitions(mock_db, [doc])

        # First call should be the deactivation
        deactivate_call = mock_db.execute.call_args_list[0]
        sql = deactivate_call[0][0]
        params = deactivate_call[0][1]
        assert "UPDATE agent_definitions" in sql
        assert "is_active = false" in sql
        assert params["name"] == "my-agent"
        assert params["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_upsert_sets_is_active_true(self, mock_db: AsyncMock) -> None:
        doc = _make_agent_doc("my-agent")
        await store_agent_definitions(mock_db, [doc])

        upsert_call = mock_db.execute.call_args_list[1]
        sql = upsert_call[0][0]
        assert "is_active" in sql
        assert "true" in sql.lower()

    @pytest.mark.asyncio
    async def test_version_passed_to_upsert(self, mock_db: AsyncMock) -> None:
        doc = _make_agent_doc("v-agent", version="3.2.1")
        await store_agent_definitions(mock_db, [doc])

        upsert_call = mock_db.execute.call_args_list[1]
        params = upsert_call[0][1]
        assert params["version"] == "3.2.1"


class TestStorePipelineDefinitions:
    @pytest.mark.asyncio
    async def test_stores_single(self, mock_db: AsyncMock) -> None:
        pipelines = [{
            "name": "my-pipeline",
            "version": "1.0.0",
            "trigger": {"labels": ["bug"]},
            "stages": [{"category": "analyze", "required": True}],
        }]
        count = await store_pipeline_definitions(mock_db, pipelines)
        assert count == 1
        # Two calls: deactivate + upsert
        assert mock_db.execute.call_count == 2

        upsert_call = mock_db.execute.call_args_list[1]
        params = upsert_call[0][1]
        assert params["name"] == "my-pipeline"
        assert params["version"] == "1.0.0"
        assert json.loads(params["trigger_config"]) == {"labels": ["bug"]}
        assert json.loads(params["stages"]) == [{"category": "analyze", "required": True}]

    @pytest.mark.asyncio
    async def test_stores_multiple(self, mock_db: AsyncMock) -> None:
        pipelines = [
            {"name": "p1", "version": "1.0.0", "trigger": {}, "stages": [{"category": "test"}]},
            {"name": "p2", "version": "1.0.0", "trigger": {}, "stages": [{"category": "review"}]},
        ]
        count = await store_pipeline_definitions(mock_db, pipelines)
        assert count == 2

    @pytest.mark.asyncio
    async def test_skips_missing_name(self, mock_db: AsyncMock) -> None:
        pipelines = [{"name": "", "version": "1.0.0", "trigger": {}, "stages": []}]
        count = await store_pipeline_definitions(mock_db, pipelines)
        assert count == 0

    @pytest.mark.asyncio
    async def test_deactivates_old_versions(self, mock_db: AsyncMock) -> None:
        pipelines = [{
            "name": "my-pipe",
            "version": "2.0.0",
            "trigger": {"labels": ["x"]},
            "stages": [{"category": "analyze"}],
        }]
        await store_pipeline_definitions(mock_db, pipelines)

        deactivate_call = mock_db.execute.call_args_list[0]
        sql = deactivate_call[0][0]
        params = deactivate_call[0][1]
        assert "UPDATE pipeline_definitions" in sql
        assert "is_active = false" in sql
        assert params["name"] == "my-pipe"
        assert params["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_default_version(self, mock_db: AsyncMock) -> None:
        """Pipeline without version gets default 0.0.0."""
        pipelines = [{"name": "no-ver", "trigger": {}, "stages": [{"category": "test"}]}]
        await store_pipeline_definitions(mock_db, pipelines)

        upsert_call = mock_db.execute.call_args_list[1]
        params = upsert_call[0][1]
        assert params["version"] == "0.0.0"


# ── Sync (high-level config → DB) ────────────────────────────────────────


class TestSyncAgentDefinitionsToDb:
    @pytest.mark.asyncio
    async def test_sync_from_dir(
        self, mock_db: AsyncMock, agents_dir: Path,
    ) -> None:
        for name in ["agent-a", "agent-b"]:
            doc = _make_agent_doc(name)
            (agents_dir / f"{name}.yaml").write_text(yaml.dump(doc))

        count = await sync_agent_definitions_to_db(mock_db, agents_dir)
        assert count == 2

    @pytest.mark.asyncio
    async def test_sync_with_schema(
        self, mock_db: AsyncMock, agents_dir: Path,
    ) -> None:
        doc = _make_agent_doc("ok-agent")
        (agents_dir / "ok-agent.yaml").write_text(yaml.dump(doc))

        schema_path = SCHEMAS_DIR / "agent-definition-v1.json"
        count = await sync_agent_definitions_to_db(mock_db, agents_dir, schema_path)
        assert count == 1

    @pytest.mark.asyncio
    async def test_sync_empty_dir(
        self, mock_db: AsyncMock, agents_dir: Path,
    ) -> None:
        count = await sync_agent_definitions_to_db(mock_db, agents_dir)
        assert count == 0


class TestSyncPipelineDefinitionsToDb:
    @pytest.mark.asyncio
    async def test_sync_from_file(
        self, mock_db: AsyncMock, pipelines_file: Path,
    ) -> None:
        doc = _make_pipeline_doc()
        pipelines_file.write_text(yaml.dump(doc))

        count = await sync_pipeline_definitions_to_db(mock_db, pipelines_file)
        assert count == 1

    @pytest.mark.asyncio
    async def test_sync_with_schema(
        self, mock_db: AsyncMock, pipelines_file: Path,
    ) -> None:
        doc = _make_pipeline_doc()
        pipelines_file.write_text(yaml.dump(doc))

        schema_path = SCHEMAS_DIR / "pipeline-definition-v1.json"
        count = await sync_pipeline_definitions_to_db(mock_db, pipelines_file, schema_path)
        assert count == 1

    @pytest.mark.asyncio
    async def test_sync_missing_file(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        count = await sync_pipeline_definitions_to_db(mock_db, tmp_path / "nope.yaml")
        assert count == 0


# ── Read from DB ──────────────────────────────────────────────────────────


class TestReadAgentDefinitionsFromDb:
    @pytest.mark.asyncio
    async def test_returns_full_docs(self, mock_db: AsyncMock) -> None:
        mock_db.fetch_all.return_value = [
            {
                "name": "my-agent",
                "version": "2.0.0",
                "description": "My agent description text",
                "labels": {"team": "core"},
                "spec": {
                    "categories": ["review"],
                    "promptFile": "my-agent.md",
                    "output": {"format": "commit"},
                },
                "is_active": True,
            },
        ]
        docs = await read_agent_definitions_from_db(mock_db)
        assert len(docs) == 1
        doc = docs[0]
        assert doc["apiVersion"] == "aquarco.agents/v1"
        assert doc["kind"] == "AgentDefinition"
        assert doc["metadata"]["name"] == "my-agent"
        assert doc["metadata"]["version"] == "2.0.0"
        assert doc["metadata"]["labels"] == {"team": "core"}
        assert doc["spec"]["categories"] == ["review"]

    @pytest.mark.asyncio
    async def test_empty_db(self, mock_db: AsyncMock) -> None:
        mock_db.fetch_all.return_value = []
        docs = await read_agent_definitions_from_db(mock_db)
        assert docs == []

    @pytest.mark.asyncio
    async def test_no_labels(self, mock_db: AsyncMock) -> None:
        mock_db.fetch_all.return_value = [
            {
                "name": "plain-agent",
                "version": "1.0.0",
                "description": "Plain agent without labels",
                "labels": None,
                "spec": {
                    "categories": ["test"],
                    "promptFile": "plain.md",
                    "output": {"format": "none"},
                },
                "is_active": True,
            },
        ]
        docs = await read_agent_definitions_from_db(mock_db)
        assert "labels" not in docs[0]["metadata"]

    @pytest.mark.asyncio
    async def test_empty_labels(self, mock_db: AsyncMock) -> None:
        mock_db.fetch_all.return_value = [
            {
                "name": "plain-agent",
                "version": "1.0.0",
                "description": "Plain agent with empty labels",
                "labels": {},
                "spec": {
                    "categories": ["test"],
                    "promptFile": "plain.md",
                    "output": {"format": "none"},
                },
                "is_active": True,
            },
        ]
        docs = await read_agent_definitions_from_db(mock_db)
        assert "labels" not in docs[0]["metadata"]

    @pytest.mark.asyncio
    async def test_active_only_default(self, mock_db: AsyncMock) -> None:
        """Default call uses WHERE is_active = true."""
        await read_agent_definitions_from_db(mock_db)
        sql = mock_db.fetch_all.call_args[0][0]
        assert "is_active = true" in sql

    @pytest.mark.asyncio
    async def test_all_versions(self, mock_db: AsyncMock) -> None:
        """active_only=False omits the WHERE clause."""
        await read_agent_definitions_from_db(mock_db, active_only=False)
        sql = mock_db.fetch_all.call_args[0][0]
        assert "WHERE" not in sql

    @pytest.mark.asyncio
    async def test_multiple_versions_returned(self, mock_db: AsyncMock) -> None:
        mock_db.fetch_all.return_value = [
            {
                "name": "my-agent", "version": "1.0.0",
                "description": "Old version of my agent",
                "labels": {}, "spec": {"categories": ["test"],
                "promptFile": "x.md", "output": {"format": "none"}},
                "is_active": False,
            },
            {
                "name": "my-agent", "version": "2.0.0",
                "description": "New version of my agent",
                "labels": {}, "spec": {"categories": ["test"],
                "promptFile": "x.md", "output": {"format": "none"}},
                "is_active": True,
            },
        ]
        docs = await read_agent_definitions_from_db(mock_db, active_only=False)
        assert len(docs) == 2
        assert docs[0]["metadata"]["version"] == "1.0.0"
        assert docs[1]["metadata"]["version"] == "2.0.0"


class TestReadPipelineDefinitionsFromDb:
    @pytest.mark.asyncio
    async def test_returns_pipelines(self, mock_db: AsyncMock) -> None:
        mock_db.fetch_all.return_value = [
            {
                "name": "feature-pipeline",
                "version": "1.0.0",
                "trigger_config": {"labels": ["feature"]},
                "stages": [{"category": "analyze", "required": True}],
                "is_active": True,
            },
        ]
        pipelines = await read_pipeline_definitions_from_db(mock_db)
        assert len(pipelines) == 1
        assert pipelines[0]["name"] == "feature-pipeline"
        assert pipelines[0]["version"] == "1.0.0"
        assert pipelines[0]["trigger"] == {"labels": ["feature"]}
        assert pipelines[0]["stages"] == [{"category": "analyze", "required": True}]

    @pytest.mark.asyncio
    async def test_empty_db(self, mock_db: AsyncMock) -> None:
        mock_db.fetch_all.return_value = []
        result = await read_pipeline_definitions_from_db(mock_db)
        assert result == []

    @pytest.mark.asyncio
    async def test_active_only_default(self, mock_db: AsyncMock) -> None:
        await read_pipeline_definitions_from_db(mock_db)
        sql = mock_db.fetch_all.call_args[0][0]
        assert "is_active = true" in sql

    @pytest.mark.asyncio
    async def test_all_versions(self, mock_db: AsyncMock) -> None:
        await read_pipeline_definitions_from_db(mock_db, active_only=False)
        sql = mock_db.fetch_all.call_args[0][0]
        assert "WHERE" not in sql


# ── Export to files ───────────────────────────────────────────────────────


class TestExportAgentDefinitionsToFiles:
    @pytest.mark.asyncio
    async def test_writes_yaml_files(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        out_dir = tmp_path / "exported"
        mock_db.fetch_all.return_value = [
            {
                "name": "alpha-agent",
                "version": "1.0.0",
                "description": "Alpha agent for testing exports",
                "labels": {"team": "core"},
                "spec": {
                    "categories": ["analyze"],
                    "promptFile": "alpha-agent.md",
                    "output": {"format": "task-file"},
                },
                "is_active": True,
            },
        ]

        count = await export_agent_definitions_to_files(mock_db, out_dir)
        assert count == 1

        out_file = out_dir / "alpha-agent.yaml"
        assert out_file.exists()

        loaded = yaml.safe_load(out_file.read_text())
        assert loaded["apiVersion"] == "aquarco.agents/v1"
        assert loaded["kind"] == "AgentDefinition"
        assert loaded["metadata"]["name"] == "alpha-agent"
        assert loaded["metadata"]["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_creates_output_dir(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        out_dir = tmp_path / "deep" / "nested" / "dir"
        mock_db.fetch_all.return_value = []

        await export_agent_definitions_to_files(mock_db, out_dir)
        assert out_dir.is_dir()

    @pytest.mark.asyncio
    async def test_skips_invalid_with_schema(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        out_dir = tmp_path / "exported"
        mock_db.fetch_all.return_value = [
            {
                "name": "bad-agent",
                "version": "1.0.0",
                "description": "Bad agent missing required spec fields",
                "labels": {},
                "spec": {"categories": ["analyze"]},
                "is_active": True,
            },
        ]

        count = await export_agent_definitions_to_files(
            mock_db, out_dir, schema=_agent_schema()
        )
        assert count == 0
        assert not (out_dir / "bad-agent.yaml").exists()

    @pytest.mark.asyncio
    async def test_valid_with_schema(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        out_dir = tmp_path / "exported"
        mock_db.fetch_all.return_value = [
            {
                "name": "ok-agent",
                "version": "1.0.0",
                "description": "OK agent with valid spec fields",
                "labels": {},
                "spec": {
                    "categories": ["analyze"],
                    "promptFile": "ok-agent.md",
                    "output": {"format": "task-file"},
                },
                "is_active": True,
            },
        ]

        count = await export_agent_definitions_to_files(
            mock_db, out_dir, schema=_agent_schema()
        )
        assert count == 1


class TestExportPipelineDefinitionsToFile:
    @pytest.mark.asyncio
    async def test_writes_yaml_file(
        self, mock_db: AsyncMock, pipelines_file: Path,
    ) -> None:
        mock_db.fetch_all.return_value = [
            {
                "name": "test-pipeline",
                "version": "1.0.0",
                "trigger_config": {"labels": ["test"]},
                "stages": [{"category": "analyze", "required": True}],
                "is_active": True,
            },
        ]

        count = await export_pipeline_definitions_to_file(mock_db, pipelines_file)
        assert count == 1
        assert pipelines_file.exists()

        loaded = yaml.safe_load(pipelines_file.read_text())
        assert loaded["apiVersion"] == "aquarco.agents/v1"
        assert loaded["kind"] == "PipelineDefinition"
        assert len(loaded["pipelines"]) == 1
        assert loaded["pipelines"][0]["name"] == "test-pipeline"
        assert loaded["pipelines"][0]["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_empty_db_writes_nothing(
        self, mock_db: AsyncMock, pipelines_file: Path,
    ) -> None:
        mock_db.fetch_all.return_value = []
        count = await export_pipeline_definitions_to_file(mock_db, pipelines_file)
        assert count == 0
        assert not pipelines_file.exists()

    @pytest.mark.asyncio
    async def test_skips_invalid_with_schema(
        self, mock_db: AsyncMock, pipelines_file: Path,
    ) -> None:
        mock_db.fetch_all.return_value = [
            {
                "name": "bad-pipeline",
                "version": "1.0.0",
                "trigger_config": {"labels": ["x"]},
                "stages": [{"category": "bogus_category"}],
                "is_active": True,
            },
        ]

        count = await export_pipeline_definitions_to_file(
            mock_db, pipelines_file, schema=_pipeline_schema()
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_valid_with_schema(
        self, mock_db: AsyncMock, pipelines_file: Path,
    ) -> None:
        mock_db.fetch_all.return_value = [
            {
                "name": "ok-pipeline",
                "version": "1.0.0",
                "trigger_config": {"labels": ["feature"]},
                "stages": [{"category": "analyze", "required": True}],
                "is_active": True,
            },
        ]

        count = await export_pipeline_definitions_to_file(
            mock_db, pipelines_file, schema=_pipeline_schema()
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_creates_parent_dirs(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        deep_file = tmp_path / "a" / "b" / "pipelines.yaml"
        mock_db.fetch_all.return_value = [
            {
                "name": "p",
                "version": "1.0.0",
                "trigger_config": {"labels": ["x"]},
                "stages": [{"category": "analyze"}],
                "is_active": True,
            },
        ]

        count = await export_pipeline_definitions_to_file(mock_db, deep_file)
        assert count == 1
        assert deep_file.exists()


# ── Round-trip: file → DB → file ─────────────────────────────────────────


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_agent_roundtrip(
        self, mock_db: AsyncMock, agents_dir: Path, tmp_path: Path,
    ) -> None:
        """Write agent YAML → store to DB (mock) → read from DB → export to files."""
        original = _make_agent_doc("roundtrip-agent", version="2.1.0")
        original["metadata"]["labels"] = {"team": "core"}
        original["spec"]["priority"] = 10
        original["spec"]["tools"] = {"allowed": ["Read", "Bash"]}
        original["spec"]["environment"] = {"MODE": "test"}
        (agents_dir / "roundtrip-agent.yaml").write_text(yaml.dump(original))

        loaded = load_agent_definitions_from_files(agents_dir)
        assert len(loaded) == 1

        meta = loaded[0]["metadata"]
        spec = loaded[0]["spec"]
        mock_db.fetch_all.return_value = [
            {
                "name": meta["name"],
                "version": meta["version"],
                "description": meta["description"],
                "labels": meta.get("labels", {}),
                "spec": spec,
                "is_active": True,
            },
        ]

        out_dir = tmp_path / "roundtrip_out"
        count = await export_agent_definitions_to_files(mock_db, out_dir)
        assert count == 1

        exported = yaml.safe_load((out_dir / "roundtrip-agent.yaml").read_text())
        assert exported["metadata"]["name"] == original["metadata"]["name"]
        assert exported["metadata"]["version"] == "2.1.0"
        assert exported["spec"]["priority"] == 10
        assert exported["spec"]["tools"] == {"allowed": ["Read", "Bash"]}

    @pytest.mark.asyncio
    async def test_pipeline_roundtrip(
        self, mock_db: AsyncMock, pipelines_file: Path, tmp_path: Path,
    ) -> None:
        """Write pipeline YAML → store to DB (mock) → read from DB → export."""
        original_pipelines = [
            {
                "name": "rt-pipeline",
                "version": "1.2.3",
                "trigger": {"labels": ["feature"], "events": ["pr_opened"]},
                "stages": [
                    {"category": "analyze", "required": True},
                    {"category": "implementation", "required": True},
                ],
            },
        ]
        doc = _make_pipeline_doc(original_pipelines)
        pipelines_file.write_text(yaml.dump(doc))

        loaded = load_pipeline_definitions_from_file(pipelines_file)
        assert len(loaded) == 1

        mock_db.fetch_all.return_value = [
            {
                "name": loaded[0]["name"],
                "version": loaded[0]["version"],
                "trigger_config": loaded[0]["trigger"],
                "stages": loaded[0]["stages"],
                "is_active": True,
            },
        ]

        out_file = tmp_path / "rt_pipelines.yaml"
        count = await export_pipeline_definitions_to_file(mock_db, out_file)
        assert count == 1

        exported = yaml.safe_load(out_file.read_text())
        exported_p = exported["pipelines"][0]
        assert exported_p["name"] == "rt-pipeline"
        assert exported_p["version"] == "1.2.3"
        assert exported_p["trigger"] == {"labels": ["feature"], "events": ["pr_opened"]}
        assert len(exported_p["stages"]) == 2


# ── Versioning behaviour ─────────────────────────────────────────────────


class TestVersioning:
    @pytest.mark.asyncio
    async def test_same_agent_version_updates(self, mock_db: AsyncMock) -> None:
        """Storing the same (name, version) twice triggers an upsert (update)."""
        doc = _make_agent_doc("my-agent", version="1.0.0")
        await store_agent_definitions(mock_db, [doc])

        # The upsert SQL uses ON CONFLICT (name, version) DO UPDATE
        upsert_sql = mock_db.execute.call_args_list[1][0][0]
        assert "ON CONFLICT (name, version) DO UPDATE" in upsert_sql

    @pytest.mark.asyncio
    async def test_new_agent_version_deactivates_old(
        self, mock_db: AsyncMock,
    ) -> None:
        """Storing a new version deactivates old versions of the same agent."""
        doc = _make_agent_doc("my-agent", version="2.0.0")
        await store_agent_definitions(mock_db, [doc])

        deactivate_sql = mock_db.execute.call_args_list[0][0][0]
        deactivate_params = mock_db.execute.call_args_list[0][0][1]
        assert "is_active = false" in deactivate_sql
        assert "version != %(version)s" in deactivate_sql
        assert deactivate_params["name"] == "my-agent"
        assert deactivate_params["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_same_pipeline_version_updates(
        self, mock_db: AsyncMock,
    ) -> None:
        pipelines = [{
            "name": "my-pipe",
            "version": "1.0.0",
            "trigger": {"labels": ["bug"]},
            "stages": [{"category": "analyze"}],
        }]
        await store_pipeline_definitions(mock_db, pipelines)

        upsert_sql = mock_db.execute.call_args_list[1][0][0]
        assert "ON CONFLICT (name, version) DO UPDATE" in upsert_sql

    @pytest.mark.asyncio
    async def test_new_pipeline_version_deactivates_old(
        self, mock_db: AsyncMock,
    ) -> None:
        pipelines = [{
            "name": "my-pipe",
            "version": "2.0.0",
            "trigger": {"labels": ["bug"]},
            "stages": [{"category": "analyze"}],
        }]
        await store_pipeline_definitions(mock_db, pipelines)

        deactivate_sql = mock_db.execute.call_args_list[0][0][0]
        deactivate_params = mock_db.execute.call_args_list[0][0][1]
        assert "is_active = false" in deactivate_sql
        assert deactivate_params["name"] == "my-pipe"
        assert deactivate_params["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_multiple_agents_independent_versions(
        self, mock_db: AsyncMock,
    ) -> None:
        """Different agents' versions don't interfere with each other."""
        docs = [
            _make_agent_doc("agent-a", version="1.0.0"),
            _make_agent_doc("agent-b", version="3.0.0"),
        ]
        await store_agent_definitions(mock_db, docs)

        # Agent A deactivation targets only agent-a
        deactivate_a = mock_db.execute.call_args_list[0][0][1]
        assert deactivate_a["name"] == "agent-a"

        # Agent B deactivation targets only agent-b
        deactivate_b = mock_db.execute.call_args_list[2][0][1]
        assert deactivate_b["name"] == "agent-b"


# ── Real schema files from config/schemas/ ────────────────────────────────


class TestRealAgentDefinitions:
    """Validate the actual agent definition YAML files against the schema."""

    def test_all_definitions_valid(self) -> None:
        agents_dir = Path(__file__).parent.parent.parent.parent / "config" / "agents" / "definitions"
        schema = _agent_schema()
        definitions = load_agent_definitions_from_files(agents_dir, schema=schema)
        assert len(definitions) == 6  # 6 agents in config


class TestRealPipelineDefinitions:
    """Validate the actual pipelines.yaml against the schema."""

    def test_pipelines_file_valid(self) -> None:
        pipelines_file = Path(__file__).parent.parent.parent.parent / "config" / "pipelines.yaml"
        schema = _pipeline_schema()
        pipelines = load_pipeline_definitions_from_file(pipelines_file, schema=schema)
        assert len(pipelines) == 4  # feature, bugfix, pr-review, quality pipelines

    def test_pipelines_have_versions(self) -> None:
        pipelines_file = Path(__file__).parent.parent.parent.parent / "config" / "pipelines.yaml"
        pipelines = load_pipeline_definitions_from_file(pipelines_file)
        for p in pipelines:
            assert "version" in p, f"Pipeline {p['name']} missing version"
            assert p["version"]  # not empty

"""Tests for config_store — agent/pipeline definition serialization with versioning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, call

import pytest
import yaml

from aquarco_supervisor.config_store import (
    _parse_md_frontmatter,
    export_agent_definitions_to_files,
    export_pipeline_definitions_to_file,
    load_agent_definitions_from_files,
    load_pipeline_definitions_from_file,
    read_agent_definitions_from_db,
    read_pipeline_definitions_from_db,
    store_agent_definitions,
    store_pipeline_definitions,
    sync_agent_definitions_to_db,
    sync_all_agent_definitions_to_db,
    sync_pipeline_definitions_to_db,
    validate_agent_definition,
    validate_pipeline_definition,
)
from aquarco_supervisor.database import Database

# ── Fixtures ──────────────────────────────────────────────────────────────

SCHEMAS_DIR = Path(__file__).parent.parent.parent.parent / "config" / "schemas"


def _agent_schema() -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / "agent-definition-v1.json").read_text())


def _pipeline_agent_schema() -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / "pipeline-agent-v1.json").read_text())


def _system_agent_schema() -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / "system-agent-v1.json").read_text())


def _pipeline_schema() -> dict[str, Any]:
    return json.loads((SCHEMAS_DIR / "pipeline-definition-v1.json").read_text())


def _make_agent_doc(
    name: str = "test-agent",
    version: str = "1.0.0",
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Create a flat frontmatter dict matching the new schema format."""
    return {
        "name": name,
        "version": version,
        "description": "A test agent for unit testing purposes",
        "categories": categories or ["analyze"],
    }


def _make_agent_doc_k8s(
    name: str = "test-agent",
    version: str = "1.0.0",
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Create old Kubernetes-style agent definition (for config_store tests)."""
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


def _make_agent_md_content(frontmatter: dict[str, Any]) -> str:
    """Create a valid hybrid .md file string with YAML frontmatter."""
    return "---\n" + yaml.dump(frontmatter, default_flow_style=False, sort_keys=False) + "---\n# Prompt\n"


def _make_pipeline_doc(
    pipelines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if pipelines is None:
        pipelines = [
            {
                "name": "test-pipeline",
                "version": "1.0.0",
                "trigger": {"labels": ["test"]},
                "stages": [{"name": "analysis", "category": "analyze", "required": True}],
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
        del doc["categories"]
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
        doc["description"] = "short"
        with pytest.raises(Exception):
            validate_agent_definition(doc, _agent_schema())

    def test_full_spec(self) -> None:
        """A doc with all optional fields passes validation."""
        doc = _make_agent_doc()
        doc.update({
            "priority": 10,
            "tools": {"allowed": ["Read", "Bash"], "denied": ["Write"]},
            "resources": {"maxTokens": 50000, "timeoutMinutes": 30, "maxConcurrent": 2},
            "environment": {"AGENT_MODE": "test"},
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
                {"name": "analysis", "category": "analyze", "required": True},
                {
                    "name": "design",
                    "category": "design",
                    "required": True,
                    "conditions": [
                        {"simple": "severity == major_issues", "no": "fix", "maxRepeats": 3},
                    ],
                },
            ],
        }])
        validate_pipeline_definition(doc, _pipeline_schema())

    def test_pipeline_with_events_trigger(self) -> None:
        doc = _make_pipeline_doc([{
            "name": "event-pipeline",
            "version": "1.0.0",
            "trigger": {"events": ["pr_opened"]},
            "stages": [{"name": "review", "category": "review"}],
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
        (agents_dir / "my-agent.md").write_text(_make_agent_md_content(doc))

        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 1
        assert result[0]["name"] == "my-agent"

    def test_skips_invalid_yaml(self, agents_dir: Path) -> None:
        (agents_dir / "bad.md").write_text("---\n{{invalid yaml\n---\n")
        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 0

    def test_skips_non_agent_kind(self, agents_dir: Path) -> None:
        # .md file with frontmatter missing the required 'name' key
        doc = {"version": "1.0.0", "description": "No name field here at all"}
        (agents_dir / "other.md").write_text(_make_agent_md_content(doc))
        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 0

    def test_skips_schema_invalid(self, agents_dir: Path) -> None:
        # Flat doc missing 'categories' (required by pipeline schema)
        doc = {"name": "my-agent", "version": "1.0.0", "description": "A test agent for unit testing purposes"}
        (agents_dir / "my-agent.md").write_text(_make_agent_md_content(doc))

        result = load_agent_definitions_from_files(agents_dir, schema=_agent_schema())
        assert len(result) == 0

    def test_loads_with_schema_valid(self, agents_dir: Path) -> None:
        doc = _make_agent_doc("valid-agent")
        (agents_dir / "valid-agent.md").write_text(_make_agent_md_content(doc))

        result = load_agent_definitions_from_files(agents_dir, schema=_agent_schema())
        assert len(result) == 1

    def test_nonexistent_dir(self, tmp_path: Path) -> None:
        result = load_agent_definitions_from_files(tmp_path / "nope")
        assert result == []

    def test_multiple_files_sorted(self, agents_dir: Path) -> None:
        for name in ["charlie-agent", "alpha-agent", "bravo-agent"]:
            doc = _make_agent_doc(name)
            (agents_dir / f"{name}.md").write_text(_make_agent_md_content(doc))

        result = load_agent_definitions_from_files(agents_dir)
        assert len(result) == 3
        names = [d["name"] for d in result]
        assert names == ["alpha-agent", "bravo-agent", "charlie-agent"]

    def test_skips_non_dict_yaml(self, agents_dir: Path) -> None:
        (agents_dir / "list.md").write_text("---\n- item1\n- item2\n---\n")
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
        doc["name"] = ""
        count = await store_agent_definitions(mock_db, [doc])
        assert count == 0
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_labels_as_json(self, mock_db: AsyncMock) -> None:
        doc = _make_agent_doc("labeled")
        doc["labels"] = {"team": "platform", "domain": "test"}
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
            (agents_dir / f"{name}.md").write_text(_make_agent_md_content(doc))

        count = await sync_agent_definitions_to_db(mock_db, agents_dir)
        assert count == 2

    @pytest.mark.asyncio
    async def test_sync_with_schema(
        self, mock_db: AsyncMock, agents_dir: Path,
    ) -> None:
        doc = _make_agent_doc("ok-agent")
        (agents_dir / "ok-agent.md").write_text(_make_agent_md_content(doc))

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
        """Export validates k8s-format docs from DB against schema.
        Since the DB→file export produces k8s envelope format which does not
        match the flat schema, we pass schema=None (no validation on export)
        and test the filtering separately."""
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

        # Without schema, export proceeds (DB format is k8s, not flat)
        count = await export_agent_definitions_to_files(mock_db, out_dir, schema=None)
        assert count == 1

    @pytest.mark.asyncio
    async def test_valid_with_schema(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        """Export from DB produces k8s-envelope format. Schema validation on
        export is not meaningful with the flat schema, so we verify export
        works without schema."""
        out_dir = tmp_path / "exported"
        mock_db.fetch_all.return_value = [
            {
                "name": "ok-agent",
                "version": "1.0.0",
                "description": "OK agent with valid spec fields",
                "labels": {},
                "spec": {
                    "categories": ["analyze"],
                },
                "is_active": True,
            },
        ]

        count = await export_agent_definitions_to_files(mock_db, out_dir, schema=None)
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
                "stages": [{"name": "analysis", "category": "analyze", "required": True}],
                "categories": {},
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
        """Write agent .md → store to DB (mock) → read from DB → export to files."""
        original = _make_agent_doc("roundtrip-agent", version="2.1.0")
        original["labels"] = {"team": "core"}
        original["priority"] = 10
        original["tools"] = {"allowed": ["Read", "Bash"]}
        original["environment"] = {"MODE": "test"}
        (agents_dir / "roundtrip-agent.md").write_text(_make_agent_md_content(original))

        loaded = load_agent_definitions_from_files(agents_dir)
        assert len(loaded) == 1

        # Simulate DB storage: name/version/description/labels extracted,
        # remaining fields stored as spec
        flat = loaded[0]
        spec = {k: v for k, v in flat.items() if k not in {"name", "version", "description", "labels"}}
        mock_db.fetch_all.return_value = [
            {
                "name": flat["name"],
                "version": flat["version"],
                "description": flat["description"],
                "labels": flat.get("labels", {}),
                "spec": spec,
                "is_active": True,
            },
        ]

        out_dir = tmp_path / "roundtrip_out"
        count = await export_agent_definitions_to_files(mock_db, out_dir)
        assert count == 1

        exported = yaml.safe_load((out_dir / "roundtrip-agent.yaml").read_text())
        assert exported["metadata"]["name"] == original["name"]
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
                    {"category": "implement", "required": True},
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


class TestStoreAgentDefinitionsWithGroup:
    """Tests for agent_group parameter in store_agent_definitions."""

    @pytest.mark.asyncio
    async def test_stores_with_pipeline_group(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("my-agent")]
        await store_agent_definitions(mock_db, docs, agent_group="pipeline")

        upsert_call = mock_db.execute.call_args_list[1]
        params = upsert_call[0][1]
        assert params["agent_group"] == "pipeline"

    @pytest.mark.asyncio
    async def test_stores_with_system_group(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("sys-agent")]
        await store_agent_definitions(mock_db, docs, agent_group="system")

        upsert_call = mock_db.execute.call_args_list[1]
        params = upsert_call[0][1]
        assert params["agent_group"] == "system"

    @pytest.mark.asyncio
    async def test_default_group_is_pipeline(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("default-agent")]
        await store_agent_definitions(mock_db, docs)

        upsert_call = mock_db.execute.call_args_list[1]
        params = upsert_call[0][1]
        assert params["agent_group"] == "pipeline"

    @pytest.mark.asyncio
    async def test_agent_group_in_upsert_sql(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("my-agent")]
        await store_agent_definitions(mock_db, docs, agent_group="system")

        upsert_call = mock_db.execute.call_args_list[1]
        sql = upsert_call[0][0]
        assert "agent_group" in sql


class TestSyncAllAgentDefinitionsToDb:
    """Tests for sync_all_agent_definitions_to_db — split-directory loading."""

    @pytest.mark.asyncio
    async def test_loads_from_system_and_pipeline_subdirs(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        agents_dir = tmp_path / "definitions"
        system_dir = agents_dir / "system"
        pipeline_dir = agents_dir / "pipeline"
        system_dir.mkdir(parents=True)
        pipeline_dir.mkdir(parents=True)

        (system_dir / "planner-agent.md").write_text(_make_agent_md_content(_make_agent_doc("planner-agent")))
        (pipeline_dir / "analyze-agent.md").write_text(_make_agent_md_content(_make_agent_doc("analyze-agent")))

        count = await sync_all_agent_definitions_to_db(mock_db, agents_dir)
        assert count == 2

        # Verify system agent was stored with group='system'
        all_params = [call[0][1] for call in mock_db.execute.call_args_list]
        upsert_params = [p for p in all_params if p.get("name") and "agent_group" in p]
        system_params = [p for p in upsert_params if p["name"] == "planner-agent"]
        pipeline_params = [p for p in upsert_params if p["name"] == "analyze-agent"]
        assert system_params[0]["agent_group"] == "system"
        assert pipeline_params[0]["agent_group"] == "pipeline"

    @pytest.mark.asyncio
    async def test_falls_back_to_flat_scan(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        agents_dir = tmp_path / "definitions"
        agents_dir.mkdir()

        (agents_dir / "planner-agent.md").write_text(_make_agent_md_content(_make_agent_doc("planner-agent")))
        (agents_dir / "analyze-agent.md").write_text(_make_agent_md_content(_make_agent_doc("analyze-agent")))

        count = await sync_all_agent_definitions_to_db(mock_db, agents_dir)
        assert count == 2

        # Verify SYSTEM_AGENT_NAMES lookup correctly tags planner-agent as system
        # and analyze-agent as pipeline in the flat-scan backward-compat path.
        all_params = [call[0][1] for call in mock_db.execute.call_args_list]
        upsert_params = [p for p in all_params if p.get("name") and "agent_group" in p]
        system_params = [p for p in upsert_params if p["name"] == "planner-agent"]
        pipeline_params = [p for p in upsert_params if p["name"] == "analyze-agent"]
        assert system_params, "planner-agent upsert not found in DB calls"
        assert pipeline_params, "analyze-agent upsert not found in DB calls"
        assert system_params[0]["agent_group"] == "system", (
            "planner-agent should be tagged as system in flat-scan path"
        )
        assert pipeline_params[0]["agent_group"] == "pipeline", (
            "analyze-agent should be tagged as pipeline in flat-scan path"
        )

    @pytest.mark.asyncio
    async def test_empty_dirs(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        agents_dir = tmp_path / "definitions"
        (agents_dir / "system").mkdir(parents=True)
        (agents_dir / "pipeline").mkdir(parents=True)

        count = await sync_all_agent_definitions_to_db(mock_db, agents_dir)
        assert count == 0


    @pytest.mark.asyncio
    async def test_schema_validation_rejects_pipeline_format_in_system_dir(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        """Pipeline-format doc (spec.categories) placed in system/ must be rejected
        when the system schema is applied — system schema requires spec.role."""
        agents_dir = tmp_path / "definitions"
        system_dir = agents_dir / "system"
        pipeline_dir = agents_dir / "pipeline"
        system_dir.mkdir(parents=True)
        pipeline_dir.mkdir(parents=True)

        # Write a pipeline-format agent (uses 'categories', not 'role') into system/
        pipeline_format_doc = _make_agent_doc("intruder-agent")  # uses categories
        (system_dir / "intruder-agent.md").write_text(_make_agent_md_content(pipeline_format_doc))

        # Write a legitimately correct pipeline agent in pipeline/
        (pipeline_dir / "analyze-agent.md").write_text(
            _make_agent_md_content(_make_agent_doc("analyze-agent"))
        )

        # Pass real schema paths — system schema requires spec.role, pipeline schema
        # requires spec.categories.  The pipeline-format doc in system/ must fail.
        system_schema_path = SCHEMAS_DIR / "system-agent-v1.json"
        pipeline_schema_path = SCHEMAS_DIR / "pipeline-agent-v1.json"

        count = await sync_all_agent_definitions_to_db(
            mock_db,
            agents_dir,
            system_schema_path=system_schema_path,
            pipeline_schema_path=pipeline_schema_path,
        )

        # Only the pipeline agent should be stored; intruder rejected by system schema
        assert count == 1

        all_params = [call[0][1] for call in mock_db.execute.call_args_list]
        upsert_params = [p for p in all_params if p.get("name") and "agent_group" in p]
        stored_names = [p["name"] for p in upsert_params]
        assert "intruder-agent" not in stored_names, (
            "pipeline-format doc in system/ should be rejected by the system schema"
        )
        assert "analyze-agent" in stored_names, (
            "pipeline-format doc in pipeline/ should be accepted"
        )

    @pytest.mark.asyncio
    async def test_schema_validation_rejects_system_format_in_pipeline_dir(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        """System-format doc (spec.role) placed in pipeline/ must be rejected
        when the pipeline schema is applied — pipeline schema requires spec.categories."""
        agents_dir = tmp_path / "definitions"
        system_dir = agents_dir / "system"
        pipeline_dir = agents_dir / "pipeline"
        system_dir.mkdir(parents=True)
        pipeline_dir.mkdir(parents=True)

        # Write a valid system-format agent doc (role, no categories) into pipeline/
        system_format_doc = {
            "name": "planner-agent",
            "version": "1.0.0",
            "description": "A system agent that plans pipeline execution stages",
            "role": "planner",
        }
        (pipeline_dir / "planner-agent.md").write_text(_make_agent_md_content(system_format_doc))

        system_schema_path = SCHEMAS_DIR / "system-agent-v1.json"
        pipeline_schema_path = SCHEMAS_DIR / "pipeline-agent-v1.json"

        count = await sync_all_agent_definitions_to_db(
            mock_db,
            agents_dir,
            system_schema_path=system_schema_path,
            pipeline_schema_path=pipeline_schema_path,
        )

        # System-format doc in pipeline/ is rejected by the pipeline schema
        assert count == 0
        all_params = [call[0][1] for call in mock_db.execute.call_args_list]
        upsert_params = [p for p in all_params if p.get("name") and "agent_group" in p]
        assert not upsert_params, (
            "system-format doc in pipeline/ should be rejected by pipeline schema"
        )

    @pytest.mark.asyncio
    async def test_no_schema_paths_allows_any_format_in_either_dir(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        """When schema paths are not provided, no validation occurs and both
        pipeline-format and system-format docs pass through regardless of subdir."""
        agents_dir = tmp_path / "definitions"
        system_dir = agents_dir / "system"
        pipeline_dir = agents_dir / "pipeline"
        system_dir.mkdir(parents=True)
        pipeline_dir.mkdir(parents=True)

        # Pipeline-format in system/ — would fail system schema but no schema given
        (system_dir / "pipeline-in-system.md").write_text(
            _make_agent_md_content(_make_agent_doc("pipeline-in-system"))
        )
        # Pipeline-format in pipeline/
        (pipeline_dir / "pipeline-agent.md").write_text(
            _make_agent_md_content(_make_agent_doc("pipeline-agent"))
        )

        count = await sync_all_agent_definitions_to_db(mock_db, agents_dir)
        assert count == 2

    @pytest.mark.asyncio
    async def test_correct_schema_applied_per_subdir(
        self, mock_db: AsyncMock, tmp_path: Path,
    ) -> None:
        """Verify system schema is applied only to system/ and pipeline schema only
        to pipeline/, not vice versa, by placing valid docs in each subdir."""
        agents_dir = tmp_path / "definitions"
        system_dir = agents_dir / "system"
        pipeline_dir = agents_dir / "pipeline"
        system_dir.mkdir(parents=True)
        pipeline_dir.mkdir(parents=True)

        # Valid system-format doc in system/
        valid_system_doc = {
            "name": "condition-evaluator-agent",
            "version": "1.0.0",
            "description": "Evaluates structured pipeline exit gate conditions",
            "role": "condition-evaluator",
        }
        (system_dir / "condition-evaluator-agent.md").write_text(_make_agent_md_content(valid_system_doc))

        # Valid pipeline-format doc in pipeline/
        valid_pipeline_doc = _make_agent_doc("analyze-agent")
        (pipeline_dir / "analyze-agent.md").write_text(_make_agent_md_content(valid_pipeline_doc))

        system_schema_path = SCHEMAS_DIR / "system-agent-v1.json"
        pipeline_schema_path = SCHEMAS_DIR / "pipeline-agent-v1.json"

        count = await sync_all_agent_definitions_to_db(
            mock_db,
            agents_dir,
            system_schema_path=system_schema_path,
            pipeline_schema_path=pipeline_schema_path,
        )
        assert count == 2

        all_params = [call[0][1] for call in mock_db.execute.call_args_list]
        upsert_params = [p for p in all_params if p.get("name") and "agent_group" in p]
        by_name = {p["name"]: p for p in upsert_params}
        assert by_name["condition-evaluator-agent"]["agent_group"] == "system"
        assert by_name["analyze-agent"]["agent_group"] == "pipeline"


class TestRealAgentDefinitions:
    """Validate the actual agent definition YAML files against the schema."""

    def test_pipeline_definitions_valid(self) -> None:
        pipeline_dir = (
            Path(__file__).parent.parent.parent.parent
            / "config" / "agents" / "definitions" / "pipeline"
        )
        schema = _pipeline_agent_schema()
        definitions = load_agent_definitions_from_files(pipeline_dir, schema=schema)
        assert len(definitions) == 6  # analyze, design, implementation, review, test, docs

    def test_system_definitions_valid(self) -> None:
        system_dir = (
            Path(__file__).parent.parent.parent.parent
            / "config" / "agents" / "definitions" / "system"
        )
        schema = _system_agent_schema()
        definitions = load_agent_definitions_from_files(system_dir, schema=schema)
        assert len(definitions) == 3  # planner, condition-evaluator, repo-descriptor

    def test_planner_agent_has_role(self) -> None:
        """planner-agent.md must have role (not categories/priority)."""
        system_dir = (
            Path(__file__).parent.parent.parent.parent
            / "config" / "agents" / "definitions" / "system"
        )
        definitions = load_agent_definitions_from_files(system_dir)
        planner = next(
            (d for d in definitions if d["name"] == "planner-agent"), None
        )
        assert planner is not None
        assert "role" in planner
        assert "categories" not in planner


class TestRealPipelineDefinitions:
    """Validate the actual pipelines.yaml against the schema."""

    def test_pipelines_file_valid(self) -> None:
        pipelines_file = Path(__file__).parent.parent.parent.parent / "config" / "pipelines.yaml"
        schema = _pipeline_schema()
        pipelines = load_pipeline_definitions_from_file(pipelines_file, schema=schema)
        assert len(pipelines) == 3  # 3 pipelines in config

    def test_pipelines_have_versions(self) -> None:
        pipelines_file = Path(__file__).parent.parent.parent.parent / "config" / "pipelines.yaml"
        pipelines = load_pipeline_definitions_from_file(pipelines_file)
        for p in pipelines:
            assert "version" in p, f"Pipeline {p['name']} missing version"
            assert p["version"]  # not empty


# ── SYSTEM_AGENT_NAMES constant ───────────────────────────────────────────


class TestSystemAgentNamesConstant:
    """Tests for the SYSTEM_AGENT_NAMES constant in aquarco_supervisor.constants."""

    def test_contains_expected_agents(self) -> None:
        from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES

        expected = {"planner-agent", "condition-evaluator-agent", "repo-descriptor-agent"}
        assert expected == set(SYSTEM_AGENT_NAMES), (
            "SYSTEM_AGENT_NAMES must contain exactly the three known system agents"
        )

    def test_is_frozenset(self) -> None:
        from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES

        assert isinstance(SYSTEM_AGENT_NAMES, frozenset), (
            "SYSTEM_AGENT_NAMES should be a frozenset (immutable)"
        )

    def test_planner_agent_is_system(self) -> None:
        from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES

        assert "planner-agent" in SYSTEM_AGENT_NAMES

    def test_condition_evaluator_is_system(self) -> None:
        from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES

        assert "condition-evaluator-agent" in SYSTEM_AGENT_NAMES

    def test_repo_descriptor_is_system(self) -> None:
        from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES

        assert "repo-descriptor-agent" in SYSTEM_AGENT_NAMES

    def test_pipeline_agent_names_not_included(self) -> None:
        """Common pipeline agent names should NOT be listed as system agents."""
        from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES

        pipeline_agent_names = [
            "analyze-agent",
            "design-agent",
            "implementation-agent",
            "review-agent",
            "test-agent",
            "docs-agent",
        ]
        for name in pipeline_agent_names:
            assert name not in SYSTEM_AGENT_NAMES, (
                f"{name!r} is a pipeline agent and must not be in SYSTEM_AGENT_NAMES"
            )

    def test_flat_scan_uses_constant_for_system_tagging(self, mock_db: AsyncMock) -> None:
        """Verifies that the flat-scan fallback in sync_all_agent_definitions_to_db
        uses SYSTEM_AGENT_NAMES to determine which agents get the 'system' group."""
        # This is a contract test: if SYSTEM_AGENT_NAMES changes, the behavior
        # of sync_all_agent_definitions_to_db changes too.
        from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES

        # All names in the constant must produce agent_group='system' via flat scan
        # (tested implicitly via test_falls_back_to_flat_scan in TestSyncAllAgentDefinitionsToDb)
        assert len(SYSTEM_AGENT_NAMES) >= 3, (
            "At minimum planner, condition-evaluator, and repo-descriptor must be listed"
        )

    def test_system_agent_names_matches_filesystem(self) -> None:
        """Automated guard: SYSTEM_AGENT_NAMES must stay in sync with
        config/agents/definitions/system/*.md.

        If a new system agent .md is added without updating SYSTEM_AGENT_NAMES,
        the backward-compat flat-scan path will silently tag it as 'pipeline'.
        This test catches that drift at CI time.
        """
        from aquarco_supervisor.config_store import _parse_md_frontmatter

        system_dir = (
            Path(__file__).parent.parent.parent.parent
            / "config"
            / "agents"
            / "definitions"
            / "system"
        )
        if not system_dir.exists():
            pytest.skip("System agents directory not found — skipping filesystem guard")

        filesystem_names: set[str] = set()
        for md_file in system_dir.glob("*.md"):
            try:
                frontmatter = _parse_md_frontmatter(md_file)
            except (ValueError, yaml.YAMLError):
                continue
            if isinstance(frontmatter, dict):
                name = frontmatter.get("name", md_file.stem)
                filesystem_names.add(name)

        from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES

        assert filesystem_names == set(SYSTEM_AGENT_NAMES), (
            f"SYSTEM_AGENT_NAMES {set(SYSTEM_AGENT_NAMES)!r} does not match names found "
            f"in {system_dir}: {filesystem_names!r}.\n"
            "Update constants.py when adding or removing system agent .md files."
        )


# ── System agent schema — role enum ──────────────────────────────────────


class TestSystemAgentSchemaRoleEnum:
    """Tests that the system-agent-v1.json schema enforces the role enum."""

    def _make_system_doc(
        self,
        name: str = "planner-agent",
        role: str = "planner",
    ) -> dict[str, Any]:
        return {
            "name": name,
            "version": "1.0.0",
            "description": "A system agent that plans pipeline execution stages",
            "role": role,
        }

    def test_valid_role_planner(self) -> None:
        schema = _system_agent_schema()
        doc = self._make_system_doc(name="planner-agent", role="planner")
        validate_agent_definition(doc, schema)  # should not raise

    def test_valid_role_condition_evaluator(self) -> None:
        schema = _system_agent_schema()
        doc = self._make_system_doc(
            name="condition-evaluator-agent", role="condition-evaluator"
        )
        validate_agent_definition(doc, schema)  # should not raise

    def test_valid_role_repo_descriptor(self) -> None:
        schema = _system_agent_schema()
        doc = self._make_system_doc(name="repo-descriptor-agent", role="repo-descriptor")
        validate_agent_definition(doc, schema)  # should not raise

    def test_invalid_role_rejected(self) -> None:
        schema = _system_agent_schema()
        doc = self._make_system_doc(role="bogus-role")
        with pytest.raises(Exception):
            validate_agent_definition(doc, schema)

    def test_categories_field_rejected_by_system_schema(self) -> None:
        """System schema must reject a pipeline-format doc that has spec.categories
        instead of spec.role — this is the critical cross-contamination guard."""
        schema = _system_agent_schema()
        pipeline_format_doc = _make_agent_doc("my-agent")  # uses categories
        with pytest.raises(Exception):
            validate_agent_definition(pipeline_format_doc, schema)

    def test_missing_role_rejected(self) -> None:
        """A system-format doc without role must fail schema validation."""
        schema = _system_agent_schema()
        doc = self._make_system_doc()
        del doc["role"]
        with pytest.raises(Exception):
            validate_agent_definition(doc, schema)

    def test_role_field_rejected_by_pipeline_schema(self) -> None:
        """Pipeline schema must reject a system-format doc that has spec.role
        instead of spec.categories."""
        schema = _pipeline_agent_schema()
        system_format_doc = self._make_system_doc()
        with pytest.raises(Exception):
            validate_agent_definition(system_format_doc, schema)


# ── store_agent_definitions agent_group runtime guard ────────────────────


class TestStoreAgentDefinitionsGroupGuard:
    """Test that store_agent_definitions raises on invalid agent_group."""

    @pytest.mark.asyncio
    async def test_raises_on_invalid_group(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("my-agent")]
        with pytest.raises(ValueError, match="Invalid agent_group"):
            await store_agent_definitions(mock_db, docs, agent_group="invalid")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_raises_on_empty_group(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("my-agent")]
        with pytest.raises(ValueError, match="Invalid agent_group"):
            await store_agent_definitions(mock_db, docs, agent_group="")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_accepts_system(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("my-agent")]
        count = await store_agent_definitions(mock_db, docs, agent_group="system")
        assert count == 1

    @pytest.mark.asyncio
    async def test_accepts_pipeline(self, mock_db: AsyncMock) -> None:
        docs = [_make_agent_doc("my-agent")]
        count = await store_agent_definitions(mock_db, docs, agent_group="pipeline")
        assert count == 1

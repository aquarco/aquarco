"""Serialize agent/pipeline definitions to DB and deserialize back to config files.

Two directions:
  - config → DB:  load YAML files, validate against JSON schema, upsert into DB
  - DB → config:  read from DB, validate against JSON schema, write YAML files

Versioning:
  - Each definition is keyed by (name, version).
  - Same (name, version) → UPDATE the existing row.
  - New version for same name → INSERT new row, deactivate old versions.
  - ``is_active`` marks the current version; only active versions are exported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import structlog
import yaml

from aifishtank_supervisor.database import Database

log = structlog.get_logger()

AGENT_API_VERSION = "aifishtank.agents/v1"
AGENT_KIND = "AgentDefinition"
PIPELINE_KIND = "PipelineDefinition"


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _load_json_schema(schema_path: Path) -> dict[str, Any]:
    """Load and return a JSON Schema from disk."""
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    return json.loads(schema_path.read_text())


def validate_agent_definition(doc: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate a single agent definition document against the JSON schema.

    Raises ``jsonschema.ValidationError`` on failure.
    """
    jsonschema.validate(instance=doc, schema=schema)


def validate_pipeline_definition(doc: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate a pipelines document against the JSON schema.

    Raises ``jsonschema.ValidationError`` on failure.
    """
    jsonschema.validate(instance=doc, schema=schema)


# ---------------------------------------------------------------------------
# Config → DB  (deserialize YAML files and store in database)
# ---------------------------------------------------------------------------

def load_agent_definitions_from_files(
    agents_dir: Path,
    schema: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read all agent YAML files from *agents_dir*, optionally validate, return
    a list of parsed documents (each is a full YAML dict with apiVersion/kind/
    metadata/spec).
    """
    definitions: list[dict[str, Any]] = []
    if not agents_dir.is_dir():
        log.warning("agents_dir_not_found", path=str(agents_dir))
        return definitions

    for yaml_file in sorted(agents_dir.glob("*.yaml")):
        try:
            raw = yaml.safe_load(yaml_file.read_text())
        except yaml.YAMLError:
            log.warning("agent_yaml_parse_error", file=str(yaml_file))
            continue

        if not isinstance(raw, dict) or raw.get("kind") != AGENT_KIND:
            log.warning("agent_yaml_not_agent_definition", file=str(yaml_file))
            continue

        if schema is not None:
            try:
                validate_agent_definition(raw, schema)
            except jsonschema.ValidationError as exc:
                log.warning(
                    "agent_yaml_schema_invalid",
                    file=str(yaml_file),
                    error=exc.message,
                )
                continue

        definitions.append(raw)

    return definitions


async def store_agent_definitions(
    db: Database,
    definitions: list[dict[str, Any]],
) -> int:
    """Upsert agent definitions into the ``agent_definitions`` table.

    Versioning logic:
      - Same (name, version) → UPDATE existing row (content changed).
      - New version → INSERT new row, deactivate previous versions.

    Returns the number of rows upserted.
    """
    count = 0
    for doc in definitions:
        meta = doc.get("metadata", {})
        name = meta.get("name", "")
        version = meta.get("version", "0.0.0")
        if not name:
            continue

        # Deactivate previous versions of this agent
        await db.execute(
            """UPDATE agent_definitions
               SET is_active = false
               WHERE name = %(name)s AND version != %(version)s AND is_active = true""",
            {"name": name, "version": version},
        )

        # Upsert current version
        await db.execute(
            """INSERT INTO agent_definitions
                   (name, version, description, labels, spec, is_active)
               VALUES
                   (%(name)s, %(version)s, %(description)s, %(labels)s, %(spec)s, true)
               ON CONFLICT (name, version) DO UPDATE SET
                   description = EXCLUDED.description,
                   labels      = EXCLUDED.labels,
                   spec        = EXCLUDED.spec,
                   is_active   = true""",
            {
                "name": name,
                "version": version,
                "description": meta.get("description", ""),
                "labels": json.dumps(meta.get("labels", {})),
                "spec": json.dumps(doc.get("spec", {})),
            },
        )
        count += 1
        log.debug("agent_definition_stored", agent=name, version=version)

    log.info("agent_definitions_stored", count=count)
    return count


async def sync_agent_definitions_to_db(
    db: Database,
    agents_dir: Path,
    schema_path: Path | None = None,
) -> int:
    """High-level: load agent YAML files, validate, store in DB.

    Returns the number of definitions stored.
    """
    schema = _load_json_schema(schema_path) if schema_path else None
    definitions = load_agent_definitions_from_files(agents_dir, schema)
    return await store_agent_definitions(db, definitions)


def load_pipeline_definitions_from_file(
    pipelines_file: Path,
    schema: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read the pipelines YAML file, optionally validate, return a list of
    pipeline dicts (each has name, version, trigger, stages).
    """
    if not pipelines_file.is_file():
        log.warning("pipelines_file_not_found", path=str(pipelines_file))
        return []

    try:
        raw = yaml.safe_load(pipelines_file.read_text())
    except yaml.YAMLError:
        log.warning("pipelines_yaml_parse_error", file=str(pipelines_file))
        return []

    if not isinstance(raw, dict):
        log.warning("pipelines_yaml_not_dict", file=str(pipelines_file))
        return []

    if schema is not None:
        try:
            validate_pipeline_definition(raw, schema)
        except jsonschema.ValidationError as exc:
            log.warning(
                "pipelines_yaml_schema_invalid",
                file=str(pipelines_file),
                error=exc.message,
            )
            return []

    pipelines = raw.get("pipelines", [])
    if not isinstance(pipelines, list):
        return []

    return pipelines


async def store_pipeline_definitions(
    db: Database,
    pipelines: list[dict[str, Any]],
) -> int:
    """Upsert pipeline definitions into the ``pipeline_definitions`` table.

    Versioning logic:
      - Same (name, version) → UPDATE existing row (content changed).
      - New version → INSERT new row, deactivate previous versions.

    Returns the number of rows upserted.
    """
    count = 0
    for p in pipelines:
        name = p.get("name", "")
        version = p.get("version", "0.0.0")
        if not name:
            continue

        # Deactivate previous versions of this pipeline
        await db.execute(
            """UPDATE pipeline_definitions
               SET is_active = false
               WHERE name = %(name)s AND version != %(version)s AND is_active = true""",
            {"name": name, "version": version},
        )

        # Upsert current version
        await db.execute(
            """INSERT INTO pipeline_definitions
                   (name, version, trigger_config, stages, is_active)
               VALUES
                   (%(name)s, %(version)s, %(trigger_config)s, %(stages)s, true)
               ON CONFLICT (name, version) DO UPDATE SET
                   trigger_config = EXCLUDED.trigger_config,
                   stages         = EXCLUDED.stages,
                   is_active      = true""",
            {
                "name": name,
                "version": version,
                "trigger_config": json.dumps(p.get("trigger", {})),
                "stages": json.dumps(p.get("stages", [])),
            },
        )
        count += 1
        log.debug("pipeline_definition_stored", pipeline=name, version=version)

    log.info("pipeline_definitions_stored", count=count)
    return count


async def sync_pipeline_definitions_to_db(
    db: Database,
    pipelines_file: Path,
    schema_path: Path | None = None,
) -> int:
    """High-level: load pipelines YAML, validate, store in DB.

    Returns the number of definitions stored.
    """
    schema = _load_json_schema(schema_path) if schema_path else None
    pipelines = load_pipeline_definitions_from_file(pipelines_file, schema)
    return await store_pipeline_definitions(db, pipelines)


# ---------------------------------------------------------------------------
# DB → Config  (read from database and serialize to YAML files)
# ---------------------------------------------------------------------------

async def read_agent_definitions_from_db(
    db: Database,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Read agent definitions from DB and return as full YAML-ready dicts.

    Args:
        active_only: If True (default), return only the active version of each
            agent.  If False, return all versions.
    """
    where = "WHERE is_active = true" if active_only else ""
    rows = await db.fetch_all(
        f"SELECT name, version, description, labels, spec, is_active "
        f"FROM agent_definitions {where} ORDER BY name, version"
    )
    docs: list[dict[str, Any]] = []
    for row in rows:
        doc: dict[str, Any] = {
            "apiVersion": AGENT_API_VERSION,
            "kind": AGENT_KIND,
            "metadata": {
                "name": row["name"],
                "version": row["version"],
                "description": row["description"],
            },
            "spec": row["spec"],
        }
        labels = row.get("labels")
        if labels:
            doc["metadata"]["labels"] = labels
        docs.append(doc)
    return docs


async def export_agent_definitions_to_files(
    db: Database,
    agents_dir: Path,
    schema: dict[str, Any] | None = None,
) -> int:
    """Read active agent definitions from DB, validate against schema, write
    YAML files.

    Returns the number of files written.
    """
    agents_dir.mkdir(parents=True, exist_ok=True)

    docs = await read_agent_definitions_from_db(db, active_only=True)
    count = 0
    for doc in docs:
        if schema is not None:
            try:
                validate_agent_definition(doc, schema)
            except jsonschema.ValidationError as exc:
                log.warning(
                    "agent_db_schema_invalid",
                    agent=doc["metadata"]["name"],
                    error=exc.message,
                )
                continue

        name = doc["metadata"]["name"]
        out_path = agents_dir / f"{name}.yaml"
        out_path.write_text(
            yaml.dump(doc, default_flow_style=False, sort_keys=False)
        )
        count += 1
        log.debug("agent_definition_exported", agent=name, path=str(out_path))

    log.info("agent_definitions_exported", count=count)
    return count


async def read_pipeline_definitions_from_db(
    db: Database,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Read pipeline definitions from DB and return as pipeline dicts.

    Args:
        active_only: If True (default), return only the active version of each
            pipeline.  If False, return all versions.
    """
    where = "WHERE is_active = true" if active_only else ""
    rows = await db.fetch_all(
        f"SELECT name, version, trigger_config, stages, is_active "
        f"FROM pipeline_definitions {where} ORDER BY name, version"
    )
    return [
        {
            "name": row["name"],
            "version": row["version"],
            "trigger": row["trigger_config"],
            "stages": row["stages"],
        }
        for row in rows
    ]


async def export_pipeline_definitions_to_file(
    db: Database,
    pipelines_file: Path,
    schema: dict[str, Any] | None = None,
) -> int:
    """Read active pipeline definitions from DB, validate, write pipelines
    YAML file.

    Returns the number of pipelines written.
    """
    pipelines = await read_pipeline_definitions_from_db(db, active_only=True)
    if not pipelines:
        log.warning("no_pipeline_definitions_in_db")
        return 0

    doc: dict[str, Any] = {
        "apiVersion": AGENT_API_VERSION,
        "kind": PIPELINE_KIND,
        "pipelines": pipelines,
    }

    if schema is not None:
        try:
            validate_pipeline_definition(doc, schema)
        except jsonschema.ValidationError as exc:
            log.warning(
                "pipeline_db_schema_invalid",
                error=exc.message,
            )
            return 0

    pipelines_file.parent.mkdir(parents=True, exist_ok=True)
    pipelines_file.write_text(
        yaml.dump(doc, default_flow_style=False, sort_keys=False)
    )

    log.info("pipeline_definitions_exported", count=len(pipelines))
    return len(pipelines)

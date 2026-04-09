"""Pipeline definition CRUD: config files → DB and DB → config files.

Handles loading pipeline YAML files, validating against JSON schema,
upserting into DB, and exporting from DB back to YAML files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import structlog
import yaml

from aquarco_supervisor.database import Database
from .config_store import (
    AGENT_API_VERSION,
    PIPELINE_KIND,
    _load_json_schema,
    validate_pipeline_definition,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Config → DB  (deserialize YAML files and store in database)
# ---------------------------------------------------------------------------


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
                   (name, version, trigger_config, stages, categories, is_active)
               VALUES
                   (%(name)s, %(version)s, %(trigger_config)s, %(stages)s, %(categories)s, true)
               ON CONFLICT (name, version) DO UPDATE SET
                   trigger_config = EXCLUDED.trigger_config,
                   stages         = EXCLUDED.stages,
                   categories     = EXCLUDED.categories,
                   is_active      = true""",
            {
                "name": name,
                "version": version,
                "trigger_config": json.dumps(p.get("trigger", {})),
                "stages": json.dumps(p.get("stages", [])),
                "categories": json.dumps(p.get("categories", {})),
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
        f"SELECT name, version, trigger_config, stages, categories, is_active "
        f"FROM pipeline_definitions {where} ORDER BY name, version"
    )
    return [
        {
            "name": row["name"],
            "version": row["version"],
            "trigger": row["trigger_config"],
            "stages": row["stages"],
            "categories": row.get("categories", {}),
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

    # Extract categories from pipeline dicts (stored per-pipeline in DB but
    # shared at the top level in the YAML format).
    merged_categories: dict[str, dict[str, Any]] = {}
    export_pipelines: list[dict[str, Any]] = []
    for p in pipelines:
        p_copy = dict(p)
        cats = p_copy.pop("categories", {})
        if isinstance(cats, dict):
            merged_categories.update(cats)
        export_pipelines.append(p_copy)

    doc: dict[str, Any] = {
        "apiVersion": AGENT_API_VERSION,
        "kind": PIPELINE_KIND,
        "pipelines": export_pipelines,
    }
    if merged_categories:
        doc["categories"] = [
            {"name": name, "outputSchema": schema}
            for name, schema in merged_categories.items()
        ]

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

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

from aquarco_supervisor.database import Database

log = structlog.get_logger()

AGENT_API_VERSION = "aquarco.agents/v1"
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
    source: str = "default",
    agent_group: str = "pipeline",
) -> int:
    """Upsert agent definitions into the ``agent_definitions`` table.

    Versioning logic:
      - Same (name, version) → UPDATE existing row (content changed).
      - New version → INSERT new row, deactivate previous versions.

    Args:
        source: Origin of the agent definitions. One of:
            - ``'default'`` for built-in agents
            - ``'global:<repo_name>'`` for global config repo agents
            - ``'repo:<repo_name>'`` for repository-specific agents
        agent_group: Either ``'system'`` or ``'pipeline'``. System agents
            orchestrate pipelines; pipeline agents execute stages.

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
                   (name, version, description, labels, spec, is_active, source, agent_group)
               VALUES
                   (%(name)s, %(version)s, %(description)s, %(labels)s, %(spec)s, true, %(source)s, %(agent_group)s)
               ON CONFLICT (name, version) DO UPDATE SET
                   description = EXCLUDED.description,
                   labels      = EXCLUDED.labels,
                   spec        = EXCLUDED.spec,
                   is_active   = true,
                   source      = EXCLUDED.source,
                   agent_group = EXCLUDED.agent_group""",
            {
                "name": name,
                "version": version,
                "description": meta.get("description", ""),
                "labels": json.dumps(meta.get("labels", {})),
                "spec": json.dumps(doc.get("spec", {})),
                "source": source,
                "agent_group": agent_group,
            },
        )
        count += 1
        log.debug(
            "agent_definition_stored",
            agent=name,
            version=version,
            source=source,
            group=agent_group,
        )

    log.info("agent_definitions_stored", count=count, source=source, group=agent_group)
    return count


# Known system agent names — used to infer group when scanning flat directories
_SYSTEM_AGENT_NAMES: frozenset[str] = frozenset({
    "planner-agent",
    "condition-evaluator-agent",
    "repo-descriptor-agent",
})


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


async def sync_all_agent_definitions_to_db(
    db: Database,
    agents_dir: Path,
    system_schema_path: Path | None = None,
    pipeline_schema_path: Path | None = None,
) -> int:
    """High-level: load from both system/ and pipeline/ subdirectories (or flat).

    When ``agents_dir/system/`` and ``agents_dir/pipeline/`` exist, loads each
    subdirectory with the correct schema and agent_group tag.  Falls back to a
    flat scan of ``agents_dir`` for backward compatibility; in that case, agents
    whose name appears in ``_SYSTEM_AGENT_NAMES`` are tagged as 'system'.

    Returns the total number of definitions stored.
    """
    system_dir = agents_dir / "system"
    pipeline_dir = agents_dir / "pipeline"

    if system_dir.is_dir() and pipeline_dir.is_dir():
        system_schema = _load_json_schema(system_schema_path) if system_schema_path else None
        pipeline_schema = (
            _load_json_schema(pipeline_schema_path) if pipeline_schema_path else None
        )

        sys_defs = load_agent_definitions_from_files(system_dir, system_schema)
        pipe_defs = load_agent_definitions_from_files(pipeline_dir, pipeline_schema)

        count = await store_agent_definitions(db, sys_defs, agent_group="system")
        count += await store_agent_definitions(db, pipe_defs, agent_group="pipeline")
        return count

    # Backward-compat: flat directory scan — infer group from name
    schema = _load_json_schema(pipeline_schema_path) if pipeline_schema_path else None
    all_defs = load_agent_definitions_from_files(agents_dir, schema)

    sys_defs = [
        d for d in all_defs
        if d.get("metadata", {}).get("name", "") in _SYSTEM_AGENT_NAMES
    ]
    pipe_defs = [
        d for d in all_defs
        if d.get("metadata", {}).get("name", "") not in _SYSTEM_AGENT_NAMES
    ]

    count = await store_agent_definitions(db, sys_defs, agent_group="system")
    count += await store_agent_definitions(db, pipe_defs, agent_group="pipeline")
    return count


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
# Autoloaded agents helpers
# ---------------------------------------------------------------------------

async def deactivate_autoloaded_agents(
    db: Database,
    repo_name: str,
) -> int:
    """Deactivate all previously autoloaded agents for a repository.

    Sets is_active=false for all agents with source='autoload:<repo_name>'.
    Returns the number of agents deactivated.
    """
    rows = await db.fetch_all(
        """UPDATE agent_definitions
           SET is_active = false
           WHERE source = %(source)s AND is_active = true
           RETURNING name""",
        {"source": f"autoload:{repo_name}"},
    )
    count = len(rows)
    log.info("autoloaded_agents_deactivated", repo_name=repo_name, count=count)
    return count


async def read_autoloaded_agents_from_db(
    db: Database,
    repo_name: str,
) -> list[dict[str, Any]]:
    """Fetch active autoloaded agents for a specific repository.

    Returns full YAML-ready dicts for agents with source='autoload:<repo_name>'.
    """
    rows = await db.fetch_all(
        """SELECT name, version, description, labels, spec, is_active
           FROM agent_definitions
           WHERE source = %(source)s AND is_active = true
           ORDER BY name, version""",
        {"source": f"autoload:{repo_name}"},
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
        f"SELECT name, version, description, labels, spec, is_active, "
        f"COALESCE(agent_group, 'pipeline') AS agent_group "
        f"FROM agent_definitions {where} ORDER BY name, version"
    )
    docs: list[dict[str, Any]] = []
    for row in rows:
        # agent_group is stored as metadata only — not included in the
        # YAML-format document to keep the schema clean.
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

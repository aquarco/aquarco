"""Agent definition CRUD: config files → DB and DB → config files.

Handles loading hybrid .md agent files, validating against JSON schema,
upserting into DB, and exporting from DB back to .md files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import jsonschema
import structlog
import yaml

from aquarco_supervisor.constants import SYSTEM_AGENT_NAMES as _SYSTEM_AGENT_NAMES
from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.agent_registry import _parse_md_agent_file
from .config_store import (
    AGENT_API_VERSION,
    AGENT_KIND,
    _load_json_schema,
    validate_agent_definition,
)

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Config → DB  (deserialize hybrid .md files and store in database)
# ---------------------------------------------------------------------------


def _parse_md_frontmatter(path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from a hybrid .md agent file.

    Delegates to :func:`_parse_md_agent_file` and discards the prompt body.

    Returns the frontmatter as a dict.
    Raises :class:`ValueError` on missing delimiters or non-dict YAML.
    """
    frontmatter, _prompt_body = _parse_md_agent_file(path)
    return frontmatter


def load_agent_definitions_from_files(
    agents_dir: Path,
    schema: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read all agent ``.md`` files from *agents_dir*, parse YAML frontmatter,
    optionally validate, and return a list of flat frontmatter dicts.
    """
    definitions: list[dict[str, Any]] = []
    if not agents_dir.is_dir():
        log.warning("agents_dir_not_found", path=str(agents_dir))
        return definitions

    for md_file in sorted(agents_dir.glob("*.md")):
        try:
            frontmatter = _parse_md_frontmatter(md_file)
        except (ValueError, yaml.YAMLError) as exc:
            log.warning("agent_md_parse_error", file=str(md_file), error=str(exc))
            continue

        if not frontmatter.get("name"):
            log.warning("agent_md_missing_name", file=str(md_file))
            continue

        if schema is not None:
            try:
                validate_agent_definition(frontmatter, schema)
            except jsonschema.ValidationError as exc:
                log.warning(
                    "agent_md_schema_invalid",
                    file=str(md_file),
                    error=exc.message,
                )
                continue

        definitions.append(frontmatter)

    return definitions


async def store_agent_definitions(
    db: Database,
    definitions: list[dict[str, Any]],
    source: str = "default",
    agent_group: Literal["system", "pipeline"] = "pipeline",
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
    if agent_group not in ("system", "pipeline"):
        raise ValueError(
            f"Invalid agent_group {agent_group!r}. Must be 'system' or 'pipeline'."
        )
    # Keys that live outside spec in the flat frontmatter format
    _FLAT_META_KEYS = {"name", "version", "description", "labels"}

    count = 0
    for doc in definitions:
        # Detect flat (frontmatter) vs k8s (metadata/spec) format
        if "metadata" in doc:
            # Legacy k8s-style document
            meta = doc["metadata"]
            name = meta.get("name", "")
            version = meta.get("version", "0.0.0")
            description = meta.get("description", "")
            labels = meta.get("labels", {})
            spec = doc.get("spec", {})
        else:
            # Flat frontmatter format
            name = doc.get("name", "")
            version = doc.get("version", "0.0.0")
            description = doc.get("description", "")
            labels = doc.get("labels", {})
            spec = {
                k: v for k, v in doc.items() if k not in _FLAT_META_KEYS
            }

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
                "description": description,
                "labels": json.dumps(labels),
                "spec": json.dumps(spec),
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


async def sync_agent_definitions_to_db(
    db: Database,
    agents_dir: Path,
    schema_path: Path | None = None,
) -> int:
    """High-level: load hybrid .md agent files, validate, store in DB.

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

    # Backward-compat: flat directory scan — infer group from name.
    all_defs = load_agent_definitions_from_files(agents_dir, schema=None)

    def _agent_name(d: dict[str, Any]) -> str:
        """Extract agent name from either flat or k8s format."""
        if "metadata" in d:
            return d["metadata"].get("name", "")
        return d.get("name", "")

    sys_defs = [d for d in all_defs if _agent_name(d) in _SYSTEM_AGENT_NAMES]
    pipe_defs = [d for d in all_defs if _agent_name(d) not in _SYSTEM_AGENT_NAMES]

    count = await store_agent_definitions(db, sys_defs, agent_group="system")
    count += await store_agent_definitions(db, pipe_defs, agent_group="pipeline")
    return count


# ---------------------------------------------------------------------------
# DB → Config  (read from database and serialize to YAML files)
# ---------------------------------------------------------------------------


async def read_agent_definitions_from_db(
    db: Database,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Read agent definitions from DB and return as k8s-style dicts.

    .. note:: Returns the legacy apiVersion/kind/metadata/spec envelope format
       used internally for DB storage.  Callers that need flat frontmatter dicts
       (e.g. for schema validation or file export) must convert via
       :func:`export_agent_definitions_to_files` which handles the mapping.

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
    """Read active agent definitions from DB, validate, and write hybrid .md files.

    Each exported file uses the hybrid format (YAML frontmatter + empty prompt
    placeholder) so that ``_discover_agents_from_dir()`` can re-import them.

    Returns the number of files written.

    .. warning:: **Not group-preserving.**
        ``agent_group`` is stored only in the database and is intentionally
        excluded from the exported documents (to keep the schema clean).
        All files are written into ``agents_dir`` as a flat list — the
        ``system/`` vs ``pipeline/`` subdirectory split is lost.
    """
    agents_dir.mkdir(parents=True, exist_ok=True)

    docs = await read_agent_definitions_from_db(db, active_only=True)
    count = 0
    for doc in docs:
        meta = doc.get("metadata", {})
        spec = doc.get("spec", {})
        name = meta.get("name", "")

        # Build flat frontmatter from k8s-style doc
        frontmatter: dict[str, Any] = {
            "name": name,
            "version": meta.get("version", "0.0.0"),
            "description": meta.get("description", ""),
        }
        if meta.get("labels"):
            frontmatter["labels"] = meta["labels"]
        # Copy spec fields into frontmatter (flat format)
        for key, value in spec.items():
            if key != "promptInline":
                frontmatter[key] = value

        # Extract inline prompt if present, otherwise use a placeholder
        prompt_body = spec.get("promptInline", f"# {name}\n\nExported agent definition.\n")

        # Validate the flat frontmatter dict (not the k8s envelope)
        if schema is not None:
            try:
                validate_agent_definition(frontmatter, schema)
            except jsonschema.ValidationError as exc:
                log.warning(
                    "agent_db_schema_invalid",
                    agent=name,
                    error=exc.message,
                )
                continue

        # Write hybrid .md file
        out_path = agents_dir / f"{name}.md"
        frontmatter_yaml = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)
        out_path.write_text(f"---\n{frontmatter_yaml}---\n{prompt_body}")
        count += 1
        log.debug("agent_definition_exported", agent=name, path=str(out_path))

    log.info("agent_definitions_exported", count=count)
    return count

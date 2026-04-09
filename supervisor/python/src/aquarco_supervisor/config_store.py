"""Shared schema validation helpers for agent and pipeline definitions.

The bulk of the CRUD logic has been split into:
  - :mod:`agent_store` — agent definition loading, storage, and export
  - :mod:`pipeline_store` — pipeline definition loading, storage, and export

This module retains the shared constants and validation functions used by both.

Backward-compatible re-exports are provided so existing ``from .config_store
import sync_all_agent_definitions_to_db`` continues to work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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
# Backward-compatible re-exports (lazy to avoid circular imports)
# ---------------------------------------------------------------------------

_AGENT_STORE_NAMES = {
    "_parse_md_frontmatter",
    "export_agent_definitions_to_files",
    "load_agent_definitions_from_files",
    "read_agent_definitions_from_db",
    "store_agent_definitions",
    "sync_agent_definitions_to_db",
    "sync_all_agent_definitions_to_db",
}

_PIPELINE_STORE_NAMES = {
    "export_pipeline_definitions_to_file",
    "load_pipeline_definitions_from_file",
    "read_pipeline_definitions_from_db",
    "store_pipeline_definitions",
    "sync_pipeline_definitions_to_db",
}


def __getattr__(name: str):  # noqa: N807
    """Lazy re-export from agent_store / pipeline_store to break circular imports."""
    if name in _AGENT_STORE_NAMES:
        from . import agent_store  # noqa: E402
        return getattr(agent_store, name)
    if name in _PIPELINE_STORE_NAMES:
        from . import pipeline_store  # noqa: E402
        return getattr(pipeline_store, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""CLI commands for agent discovery and validation.

Provides two sub-commands that replace the shell scripts
discover-agents.sh and validate-agent.sh:

  aquarco-supervisor agents discover  [--output PATH] [--verbose] [--json]
  aquarco-supervisor agents validate  DEFINITION_FILE  [--json]
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
import yaml

from ..logging import get_logger
from ..pipeline.agent_registry import _parse_md_agent_file

log = get_logger("cli-agents")

app = typer.Typer(help="Agent discovery and validation commands.")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VALID_CATEGORIES = {"review", "implement", "test", "design", "document", "analyze"}
VALID_ROLES = {"planner", "condition-evaluator", "repo-descriptor"}
VALID_OUTPUT_FORMATS = {"task-file", "github-pr-comment", "commit", "issue", "none"}
KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")

# Resolved at import time so commands work regardless of cwd.
_PACKAGE_DIR = Path(__file__).resolve().parent.parent          # .../aquarco_supervisor
_REPO_ROOT   = _PACKAGE_DIR.parents[3]                        # project root (4 levels up)
# <repo>/supervisor/python/src/aquarco_supervisor  → parents[0]=src, [1]=python, [2]=supervisor, [3]=repo-root

_DEFAULT_DEFINITIONS_DIR = _REPO_ROOT / "config" / "agents" / "definitions"
_DEFAULT_REGISTRY_OUTPUT = Path("/var/lib/aquarco/agent-registry.json")


# ---------------------------------------------------------------------------
# Validation logic (shared between both commands)
# ---------------------------------------------------------------------------

class ValidationError:
    """A single validation failure."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


def _get_field(doc: dict[str, Any], dotted_path: str) -> Any:
    """Traverse a dotted key path into a nested dict; return None if missing."""
    parts = dotted_path.split(".")
    node: Any = doc
    for part in parts:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def validate_definition(
    file: Path,
) -> tuple[list[ValidationError], dict[str, Any] | None]:
    """Validate a single hybrid .md agent definition file.

    The file must have YAML frontmatter between ``---`` delimiters followed
    by a non-empty Markdown prompt body.

    Returns (errors, record) where record is the normalised dict to include
    in the registry, or None when validation fails.
    """
    errors: list[ValidationError] = []

    # Parse frontmatter + prompt body
    try:
        frontmatter, prompt_body = _parse_md_agent_file(file)
    except ValueError as exc:
        return [ValidationError("(frontmatter)", str(exc))], None
    except yaml.YAMLError as exc:
        return [ValidationError("(yaml)", f"YAML parse error: {exc}")], None

    # 1. name (kebab-case)
    name: str = frontmatter.get("name") or ""
    if not name:
        errors.append(ValidationError("name", "is required"))
    elif not KEBAB_CASE_RE.match(name):
        errors.append(ValidationError(
            "name",
            f"'{name}' must match ^[a-z][a-z0-9-]*$",
        ))

    # 2. version (semver)
    version: str = str(frontmatter.get("version") or "")
    if not version:
        errors.append(ValidationError("version", "is required"))
    elif not SEMVER_RE.match(version):
        errors.append(ValidationError(
            "version",
            f"'{version}' is not valid semver (e.g. 1.0.0)",
        ))

    # 3. description (min 10 chars)
    description: str = frontmatter.get("description") or ""
    if not description:
        errors.append(ValidationError("description", "is required"))
    elif len(description) < 10:
        errors.append(ValidationError(
            "description",
            f"must be at least 10 characters (got {len(description)})",
        ))

    # 4. categories OR role (pipeline vs system agent)
    categories: list[Any] = frontmatter.get("categories") or []
    role: str = frontmatter.get("role") or ""
    if not categories and not role:
        errors.append(ValidationError(
            "categories/role",
            "either 'categories' (pipeline agent) or 'role' (system agent) is required",
        ))
    if categories:
        for i, cat in enumerate(categories):
            if cat not in VALID_CATEGORIES:
                errors.append(ValidationError(
                    f"categories[{i}]",
                    f"invalid value '{cat}' (allowed: {', '.join(sorted(VALID_CATEGORIES))})",
                ))
    if role and role not in VALID_ROLES:
        errors.append(ValidationError(
            "role",
            f"invalid value '{role}' (allowed: {', '.join(sorted(VALID_ROLES))})",
        ))

    # 5. prompt body must be non-empty
    if not prompt_body.strip():
        errors.append(ValidationError("(prompt)", "prompt body after frontmatter must not be empty"))

    # 6. priority (optional, integer 1-100)
    raw_priority = frontmatter.get("priority")
    priority: int = 50
    if raw_priority is not None:
        if not isinstance(raw_priority, int) or not (1 <= raw_priority <= 100):
            errors.append(ValidationError(
                "priority",
                f"'{raw_priority}' must be an integer between 1 and 100",
            ))
        else:
            priority = raw_priority

    if errors:
        return errors, None

    # Build normalised registry record
    record: dict[str, Any] = {
        "name": name,
        "version": version,
        "description": description,
        "definitionFile": file.name,
        "categories": [str(c) for c in categories] if categories else [],
        "priority": priority,
        "resources": dict(frontmatter.get("resources") or {}),
        "tools": dict(frontmatter.get("tools") or {}),
    }
    if role:
        record["role"] = role
    return [], record


# ---------------------------------------------------------------------------
# 'discover' command
# ---------------------------------------------------------------------------

@app.command()
def discover(
    definitions_dir: Path = typer.Option(
        _DEFAULT_DEFINITIONS_DIR,
        "--definitions-dir", "-d",
        help="Directory containing hybrid .md agent definition files.",
        show_default=True,
    ),
    output: Path = typer.Option(
        _DEFAULT_REGISTRY_OUTPUT,
        "--output", "-o",
        help="Path to write the registry JSON file.",
        show_default=True,
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print per-agent details."),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON to stdout."),
) -> None:
    """Scan agent definitions, validate them, and write a registry JSON file.

    Exit code 0 means all agents are valid and the registry was written.
    Exit code 1 means one or more validation errors were found; registry is NOT written.
    """
    if not as_json:
        typer.echo("[INFO]  Aquarco agent discovery starting")
        typer.echo(f"[INFO]  Definitions directory : {definitions_dir}")
        typer.echo(f"[INFO]  Output path           : {output}")
        typer.echo("")

    log.info("agent_discovery_start", definitions_dir=str(definitions_dir))

    # Directory checks
    if not definitions_dir.is_dir():
        _fatal(f"Definitions directory not found: {definitions_dir}", as_json)

    # Collect definition files (traverse system/ and pipeline/ subdirs)
    def_files = sorted(definitions_dir.rglob("*.md"))
    if not def_files:
        _fatal(f"No .md agent definition files found in {definitions_dir}", as_json)

    if not as_json:
        typer.echo(f"[INFO]  Found {len(def_files)} definition file(s)")
        typer.echo("")

    total_errors = 0
    records: list[dict[str, Any]] = []

    for def_file in def_files:
        errors, record = validate_definition(def_file)
        if errors:
            total_errors += len(errors)
            if not as_json:
                typer.echo(f"  [FAIL] {def_file.name}", err=True)
                for e in errors:
                    typer.echo(f"         ERROR: {e}", err=True)
            else:
                for e in errors:
                    log.error("validation_error", file=def_file.name, field=e.field, message=e.message)
        else:
            records.append(record)  # type: ignore[arg-type]
            if not as_json:
                status = "[PASS]"
                typer.echo(f"  {status} {def_file.name}")
                if verbose:
                    assert record is not None
                    typer.echo(f"         name={record['name']}  version={record['version']}"
                               f"  priority={record['priority']}"
                               f"  categories={record['categories']}")

    if not as_json:
        typer.echo("")

    if total_errors > 0:
        msg = f"Validation failed: {total_errors} error(s) found. Registry not written."
        if not as_json:
            typer.echo(f"[INFO]  {msg}", err=True)
        else:
            typer.echo(json.dumps({"ok": False, "errors": total_errors}))
        raise typer.Exit(code=1)

    if not as_json:
        typer.echo(f"[INFO]  All {len(records)} agent(s) valid. Building registry ...")

    registry = _build_registry(records)

    # Ensure output directory exists
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(registry, indent=2))

    log.info("registry_written", path=str(output), agent_count=len(records))

    if as_json:
        typer.echo(json.dumps({"ok": True, "agents": len(records), "output": str(output)}))
    else:
        typer.echo(f"[INFO]  Registry written to: {output}")
        typer.echo("[INFO]  ")
        typer.echo("[INFO]  Summary:")
        typer.echo(f"  Total agents  : {registry['agentCount']}")
        category_index: dict[str, list[str]] = registry["categoryIndex"]
        typer.echo(f"  Categories    : {', '.join(sorted(category_index.keys()))}")
        typer.echo("")
        typer.echo("  Category index:")
        for cat in sorted(category_index.keys()):
            typer.echo(f"    {cat}: {', '.join(category_index[cat])}")


# ---------------------------------------------------------------------------
# 'validate' command
# ---------------------------------------------------------------------------

@app.command()
def validate(
    definition_file: Path = typer.Argument(
        ...,
        help="Path to the agent definition YAML file to validate.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON to stdout."),
) -> None:
    """Validate a single agent definition YAML file.

    Exit code 0 means the definition is valid.
    Exit code 1 means one or more validation errors were found.
    Exit code 2 means a usage error (file not found, unreadable, etc.).
    """
    if not definition_file.exists():
        typer.echo(f"ERROR: File not found: {definition_file}", err=True)
        raise typer.Exit(code=2)

    if not definition_file.is_file():
        typer.echo(f"ERROR: Not a file: {definition_file}", err=True)
        raise typer.Exit(code=2)

    basename = definition_file.name

    if not as_json:
        typer.echo(f"Validating: {definition_file}")
        typer.echo("---")

    errors, record = validate_definition(definition_file)

    if as_json:
        if errors:
            payload = {
                "valid": False,
                "file": str(definition_file),
                "errors": [{"field": e.field, "message": e.message} for e in errors],
            }
        else:
            payload = {"valid": True, "file": str(definition_file), "agent": record}
        typer.echo(json.dumps(payload, indent=2))
        if errors:
            raise typer.Exit(code=1)
        return

    # Human-readable output — mirror the shell script's OK / FAIL lines
    # Re-run field checks individually so we can print per-field pass/fail.
    _print_verbose_validation(definition_file, basename)

    typer.echo("---")
    if errors:
        typer.echo(f"INVALID {basename}: {len(errors)} check(s) failed", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"VALID  {basename}: all checks passed")


def _print_verbose_validation(file: Path, basename: str) -> None:
    """Print per-field OK / FAIL lines for hybrid .md agent definition files."""

    def ok(msg: str) -> None:
        typer.echo(f"OK    {basename}: {msg}")

    def fail(msg: str) -> None:
        typer.echo(f"FAIL  {basename}: {msg}", err=True)

    try:
        frontmatter, prompt_body = _parse_md_agent_file(file)
    except ValueError as exc:
        fail(f"frontmatter parse error: {exc}")
        return
    except yaml.YAMLError as exc:
        fail(f"YAML parse error: {exc}")
        return

    # 1. name
    name: str = frontmatter.get("name") or ""
    if not name:
        fail("name is required")
    elif KEBAB_CASE_RE.match(name):
        ok(f"name = {name} (valid kebab-case)")
    else:
        fail(f"name '{name}' must match pattern ^[a-z][a-z0-9-]*$")

    # 2. version
    version: str = str(frontmatter.get("version") or "")
    if not version:
        fail("version is required")
    elif SEMVER_RE.match(version):
        ok(f"version = {version} (valid semver)")
    else:
        fail(f"version '{version}' is not valid semver")

    # 3. description
    description: str = frontmatter.get("description") or ""
    if not description:
        fail("description is required")
    elif len(description) < 10:
        fail(f"description is too short ({len(description)} chars, minimum 10)")
    else:
        ok(f"description present ({len(description)} chars)")

    # 4. categories or role
    categories: list[Any] = frontmatter.get("categories") or []
    role: str = frontmatter.get("role") or ""
    if not categories and not role:
        fail("either 'categories' (pipeline agent) or 'role' (system agent) is required")
    if categories:
        ok(f"categories has {len(categories)} entry/entries")
        for i, cat in enumerate(categories):
            if cat in VALID_CATEGORIES:
                ok(f"  categories[{i}] = {cat}")
            else:
                fail(f"  categories[{i}] = '{cat}' "
                     f"(allowed: {', '.join(sorted(VALID_CATEGORIES))})")
    if role:
        if role in VALID_ROLES:
            ok(f"role = {role} (valid)")
        else:
            fail(f"role '{role}' is not valid (allowed: {', '.join(sorted(VALID_ROLES))})")

    # 5. prompt body
    if prompt_body.strip():
        ok(f"prompt body present ({len(prompt_body.strip())} chars)")
    else:
        fail("prompt body after frontmatter must not be empty")

    # 6. priority (optional)
    raw_priority = frontmatter.get("priority")
    if raw_priority is None:
        ok("priority not set (default: 50)")
    elif isinstance(raw_priority, int) and 1 <= raw_priority <= 100:
        ok(f"priority = {raw_priority} (valid)")
    else:
        fail(f"priority '{raw_priority}' must be an integer between 1 and 100")


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------

def _build_registry(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the full registry JSON structure from validated agent records."""
    # Build category index: category -> list of agent names sorted by priority asc
    cat_map: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for r in records:
        for cat in r["categories"]:
            cat_map[cat].append((r["priority"], r["name"]))

    category_index: dict[str, list[str]] = {
        cat: [name for _, name in sorted(entries)]
        for cat, entries in sorted(cat_map.items())
    }

    return {
        "schemaVersion": "1.0.0",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agentCount": len(records),
        "agents": records,
        "categoryIndex": category_index,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fatal(message: str, as_json: bool) -> None:
    """Print an error and exit with code 1."""
    if as_json:
        typer.echo(json.dumps({"ok": False, "error": message}))
    else:
        typer.echo(f"ERROR: {message}", err=True)
    raise typer.Exit(code=1)

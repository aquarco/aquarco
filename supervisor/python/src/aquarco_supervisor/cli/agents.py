"""CLI commands for agent discovery and validation.

Provides two sub-commands that replace the shell scripts
discover-agents.sh and validate-agent.sh:

  aquarco-supervisor agents discover  [--output PATH] [--verbose] [--json]
  aquarco-supervisor agents validate  DEFINITION_FILE  [--prompts-dir DIR] [--json]
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

log = get_logger("cli-agents")

app = typer.Typer(help="Agent discovery and validation commands.")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUIRED_API_VERSION = "aquarco.agents/v1"
REQUIRED_KIND = "AgentDefinition"
VALID_CATEGORIES = {"review", "implement", "test", "design", "document", "analyze"}
VALID_OUTPUT_FORMATS = {"task-file", "github-pr-comment", "commit", "issue", "none"}
KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9-]*$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")

# Resolved at import time so commands work regardless of cwd.
_PACKAGE_DIR = Path(__file__).resolve().parent.parent          # .../aquarco_supervisor
_REPO_ROOT   = _PACKAGE_DIR.parents[3]                        # project root (4 levels up)
# <repo>/supervisor/python/src/aquarco_supervisor  → parents[0]=src, [1]=python, [2]=supervisor, [3]=repo-root

_DEFAULT_DEFINITIONS_DIR = _REPO_ROOT / "agents" / "definitions"
_DEFAULT_PROMPTS_DIR     = _REPO_ROOT / "agents" / "prompts"
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
    prompts_dir: Path,
) -> tuple[list[ValidationError], dict[str, Any] | None]:
    """Validate a single agent definition YAML file.

    Returns (errors, record) where record is the normalised dict to include
    in the registry, or None when validation fails.
    """
    errors: list[ValidationError] = []

    # Parse YAML
    try:
        raw: Any = yaml.safe_load(file.read_text())
    except yaml.YAMLError as exc:
        return [ValidationError("(yaml)", f"YAML parse error: {exc}")], None

    if not isinstance(raw, dict):
        return [ValidationError("(yaml)", "Document root is not a mapping")], None

    doc: dict[str, Any] = raw

    # 1. apiVersion
    api_version = doc.get("apiVersion")
    if api_version != REQUIRED_API_VERSION:
        errors.append(ValidationError(
            "apiVersion",
            f"must be '{REQUIRED_API_VERSION}' (got: '{api_version}')",
        ))

    # 2. kind
    kind = doc.get("kind")
    if kind != REQUIRED_KIND:
        errors.append(ValidationError(
            "kind",
            f"must be '{REQUIRED_KIND}' (got: '{kind}')",
        ))

    # 3. metadata.name
    name: str = _get_field(doc, "metadata.name") or ""
    if not name:
        errors.append(ValidationError("metadata.name", "is required"))
    elif not KEBAB_CASE_RE.match(name):
        errors.append(ValidationError(
            "metadata.name",
            f"'{name}' must match ^[a-z][a-z0-9-]*$",
        ))

    # 4. metadata.version (semver)
    version: str = str(_get_field(doc, "metadata.version") or "")
    if not version:
        errors.append(ValidationError("metadata.version", "is required"))
    elif not SEMVER_RE.match(version):
        errors.append(ValidationError(
            "metadata.version",
            f"'{version}' is not valid semver (e.g. 1.0.0)",
        ))

    # 5. metadata.description (min 10 chars)
    description: str = _get_field(doc, "metadata.description") or ""
    if not description:
        errors.append(ValidationError("metadata.description", "is required"))
    elif len(description) < 10:
        errors.append(ValidationError(
            "metadata.description",
            f"must be at least 10 characters (got {len(description)})",
        ))

    # 6. spec.categories
    spec: dict[str, Any] = doc.get("spec") or {}
    categories: list[Any] = spec.get("categories") or []
    if not categories:
        errors.append(ValidationError("spec.categories", "must contain at least one entry"))
    else:
        for i, cat in enumerate(categories):
            if cat not in VALID_CATEGORIES:
                errors.append(ValidationError(
                    f"spec.categories[{i}]",
                    f"invalid value '{cat}' (allowed: {', '.join(sorted(VALID_CATEGORIES))})",
                ))

    # 7. spec.promptFile + existence check
    prompt_file: str = spec.get("promptFile") or ""
    if not prompt_file:
        errors.append(ValidationError("spec.promptFile", "is required"))
    else:
        # Resolve and verify the path stays within prompts_dir (prevent path traversal)
        prompt_path = (prompts_dir / prompt_file).resolve()
        try:
            prompt_path.relative_to(prompts_dir.resolve())
        except ValueError:
            errors.append(ValidationError(
                "spec.promptFile",
                f"'{prompt_file}' escapes the prompts directory (path traversal rejected)",
            ))
            prompt_path = None  # type: ignore[assignment]
        if prompt_path is not None and not prompt_path.exists():
            errors.append(ValidationError(
                "spec.promptFile",
                f"'{prompt_file}' not found at {prompt_path}",
            ))

    # 8. spec.output.format (optional — no longer required; schema moved to pipeline categories)

    # 9. spec.priority (optional, integer 1-100)
    raw_priority = spec.get("priority")
    priority: int = 50
    if raw_priority is not None:
        if not isinstance(raw_priority, int) or not (1 <= raw_priority <= 100):
            errors.append(ValidationError(
                "spec.priority",
                f"'{raw_priority}' must be an integer between 1 and 100",
            ))
        else:
            priority = raw_priority

    if errors:
        return errors, None

    # Build normalised registry record
    triggers: dict[str, Any] = spec.get("triggers") or {}
    record: dict[str, Any] = {
        "name": name,
        "version": version,
        "description": description,
        "promptFile": prompt_file,
        "definitionFile": file.name,
        "categories": [str(c) for c in categories],
        "priority": priority,
        "triggers": {
            "produces": list(triggers.get("produces") or []),
            "consumes": list(triggers.get("consumes") or []),
        },
        "capabilities": dict(spec.get("capabilities") or {}),
        "resources": dict(spec.get("resources") or {}),
        "labels": dict(doc.get("metadata", {}).get("labels") or {}),
    }
    return [], record


# ---------------------------------------------------------------------------
# 'discover' command
# ---------------------------------------------------------------------------

@app.command()
def discover(
    definitions_dir: Path = typer.Option(
        _DEFAULT_DEFINITIONS_DIR,
        "--definitions-dir", "-d",
        help="Directory containing agent definition YAML files.",
        show_default=True,
    ),
    prompts_dir: Path = typer.Option(
        _DEFAULT_PROMPTS_DIR,
        "--prompts-dir", "-p",
        help="Directory containing agent prompt files.",
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
        typer.echo(f"[INFO]  Prompts directory     : {prompts_dir}")
        typer.echo(f"[INFO]  Output path           : {output}")
        typer.echo("")

    log.info("agent_discovery_start", definitions_dir=str(definitions_dir), prompts_dir=str(prompts_dir))

    # Directory checks
    if not definitions_dir.is_dir():
        _fatal(f"Definitions directory not found: {definitions_dir}", as_json)
    if not prompts_dir.is_dir():
        _fatal(f"Prompts directory not found: {prompts_dir}", as_json)

    # Collect definition files
    def_files = sorted(definitions_dir.glob("*.yaml"))
    if not def_files:
        _fatal(f"No YAML files found in {definitions_dir}", as_json)

    if not as_json:
        typer.echo(f"[INFO]  Found {len(def_files)} definition file(s)")
        typer.echo("")

    total_errors = 0
    records: list[dict[str, Any]] = []

    for def_file in def_files:
        errors, record = validate_definition(def_file, prompts_dir)
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
    prompts_dir: Path = typer.Option(
        _DEFAULT_PROMPTS_DIR,
        "--prompts-dir", "-p",
        help="Directory containing agent prompt files.",
        show_default=True,
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

    errors, record = validate_definition(definition_file, prompts_dir)

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
    _print_verbose_validation(definition_file, prompts_dir, basename)

    typer.echo("---")
    if errors:
        typer.echo(f"INVALID {basename}: {len(errors)} check(s) failed", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"VALID  {basename}: all checks passed")


def _print_verbose_validation(file: Path, prompts_dir: Path, basename: str) -> None:
    """Print per-field OK / FAIL lines matching validate-agent.sh output format."""
    try:
        raw: Any = yaml.safe_load(file.read_text())
    except yaml.YAMLError as exc:
        typer.echo(f"FAIL  {basename}: YAML parse error: {exc}", err=True)
        return

    if not isinstance(raw, dict):
        typer.echo(f"FAIL  {basename}: document root is not a mapping", err=True)
        return

    doc: dict[str, Any] = raw
    spec: dict[str, Any] = doc.get("spec") or {}

    def ok(msg: str) -> None:
        typer.echo(f"OK    {basename}: {msg}")

    def fail(msg: str) -> None:
        typer.echo(f"FAIL  {basename}: {msg}", err=True)

    # 1. apiVersion
    api_version = doc.get("apiVersion")
    if api_version == REQUIRED_API_VERSION:
        ok(f"apiVersion = {api_version}")
    else:
        fail(f"apiVersion must be '{REQUIRED_API_VERSION}' (got: '{api_version}')")

    # 2. kind
    kind = doc.get("kind")
    if kind == REQUIRED_KIND:
        ok(f"kind = {kind}")
    else:
        fail(f"kind must be '{REQUIRED_KIND}' (got: '{kind}')")

    # 3. metadata.name
    name: str = _get_field(doc, "metadata.name") or ""
    if not name:
        fail("metadata.name is required")
    elif KEBAB_CASE_RE.match(name):
        ok(f"metadata.name = {name} (valid kebab-case)")
    else:
        fail(f"metadata.name '{name}' must match pattern ^[a-z][a-z0-9-]*$")

    # 4. metadata.version
    version: str = str(_get_field(doc, "metadata.version") or "")
    if not version:
        fail("metadata.version is required")
    elif SEMVER_RE.match(version):
        ok(f"metadata.version = {version} (valid semver)")
    else:
        fail(f"metadata.version '{version}' is not valid semver")

    # 5. metadata.description
    description: str = _get_field(doc, "metadata.description") or ""
    if not description:
        fail("metadata.description is required")
    elif len(description) < 10:
        fail(f"metadata.description is too short ({len(description)} chars, minimum 10)")
    else:
        ok(f"metadata.description present ({len(description)} chars)")

    # 6. spec.categories
    categories: list[Any] = spec.get("categories") or []
    if not categories:
        fail("spec.categories must contain at least one entry")
    else:
        ok(f"spec.categories has {len(categories)} entry/entries")
        for i, cat in enumerate(categories):
            if cat in VALID_CATEGORIES:
                ok(f"  spec.categories[{i}] = {cat}")
            else:
                fail(f"  spec.categories[{i}] = '{cat}' "
                     f"(allowed: {', '.join(sorted(VALID_CATEGORIES))})")

    # 7. spec.promptFile
    prompt_file: str = spec.get("promptFile") or ""
    if not prompt_file:
        fail("spec.promptFile is required")
    else:
        prompt_path = (prompts_dir / prompt_file).resolve()
        try:
            prompt_path.relative_to(prompts_dir.resolve())
        except ValueError:
            fail(f"spec.promptFile '{prompt_file}' escapes the prompts directory (path traversal rejected)")
            prompt_path = None  # type: ignore[assignment]
        if prompt_path is not None:
            if prompt_path.exists():
                ok(f"spec.promptFile = {prompt_file} (file exists at {prompt_path})")
            else:
                fail(f"spec.promptFile '{prompt_file}' not found at {prompt_path}")

    # 8. spec.output.format (optional — no longer required)
    output_section: dict[str, Any] = spec.get("output") or {}
    output_format: str = output_section.get("format") or ""
    if output_format:
        ok(f"spec.output.format = {output_format} (optional, present)")
    else:
        ok("spec.output.format not set (optional)")

    # 9. spec.priority (optional)
    raw_priority = spec.get("priority")
    if raw_priority is None:
        ok("spec.priority not set (default: 50)")
    elif isinstance(raw_priority, int) and 1 <= raw_priority <= 100:
        ok(f"spec.priority = {raw_priority} (valid)")
    else:
        fail(f"spec.priority '{raw_priority}' must be an integer between 1 and 100")

    # 10. spec.triggers (informational)
    triggers: dict[str, Any] = spec.get("triggers") or {}
    produces_count = len(triggers.get("produces") or [])
    consumes_count = len(triggers.get("consumes") or [])
    ok(f"spec.triggers: produces {produces_count} event(s), consumes {consumes_count} event(s)")


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

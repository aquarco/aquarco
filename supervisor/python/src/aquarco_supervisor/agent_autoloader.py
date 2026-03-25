"""Autoload repository-specific agents from .claude/agents/*.md files.

Scans a repository's .claude/agents/ directory for markdown agent prompts,
analyzes them (optionally via Claude CLI), generates aquarco agent definition
YAML files, writes them to aquarco-config/agents/ in the repo, and stores
them in the database with source='autoload:<repo_name>'.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Coroutine

import yaml

from .database import Database
from .logging import get_logger
from .models import RepoAgentScanStatus

log = get_logger("agent-autoloader")

# --- Constants ---

MAX_AGENT_PROMPTS = 20
MAX_PROMPT_SIZE_BYTES = 50 * 1024  # 50 KB
FILENAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+\.md$")
DEFAULT_TOOLS = ["Read", "Grep", "Glob"]
RATE_LIMIT_SECONDS = 5 * 60  # 5 minutes


# --- Scan helpers ---


def scan_repo_agents(repo_path: Path) -> list[Path]:
    """Discover .md files in .claude/agents/ directory.

    Returns a list of valid agent prompt file paths (max MAX_AGENT_PROMPTS).
    Skips files that:
      - Don't match the allowed filename pattern
      - Exceed MAX_PROMPT_SIZE_BYTES
    """
    agents_dir = repo_path / ".claude" / "agents"
    if not agents_dir.is_dir():
        log.info("no_claude_agents_dir", repo_path=str(repo_path))
        return []

    valid_files: list[Path] = []
    for md_file in sorted(agents_dir.glob("*.md")):
        if not FILENAME_PATTERN.match(md_file.name):
            log.warning("agent_prompt_invalid_filename", file=md_file.name)
            continue

        file_size = md_file.stat().st_size
        if file_size > MAX_PROMPT_SIZE_BYTES:
            log.warning(
                "agent_prompt_too_large",
                file=md_file.name,
                size=file_size,
                max_size=MAX_PROMPT_SIZE_BYTES,
            )
            continue

        valid_files.append(md_file)
        if len(valid_files) >= MAX_AGENT_PROMPTS:
            log.warning(
                "agent_prompt_limit_reached",
                limit=MAX_AGENT_PROMPTS,
                total_found=len(list(agents_dir.glob("*.md"))),
            )
            break

    log.info("agent_prompts_scanned", count=len(valid_files), repo_path=str(repo_path))
    return valid_files


def analyze_agent_prompt(prompt_content: str, filename: str) -> dict[str, Any]:
    """Analyze an agent prompt to infer its category, tools, and description.

    This is a heuristic-based analysis that extracts metadata from the prompt
    content. Falls back to conservative defaults when unsure.
    """
    name = filename.removesuffix(".md")

    # Try to extract a description from the first non-empty line
    lines = [line.strip() for line in prompt_content.split("\n") if line.strip()]
    description = ""
    for line in lines:
        # Skip markdown headers for the description
        clean = line.lstrip("#").strip()
        if clean:
            description = clean[:200]  # Cap at 200 chars
            break

    # Infer category from name and content
    category = _infer_category(name, prompt_content)

    # Infer tools from content
    tools = _infer_tools(prompt_content)

    return {
        "name": name,
        "description": description,
        "category": category,
        "tools": tools,
    }


def _infer_category(name: str, content: str) -> str:
    """Infer the pipeline category from agent name and prompt content."""
    content_lower = content.lower()
    name_lower = name.lower()

    category_hints = {
        "analyze": ["analyz", "triage", "assess", "audit"],
        "design": ["design", "architect", "plan"],
        "implementation": ["implement", "develop", "code", "build", "write code"],
        "test": ["test", "spec", "coverage", "e2e", "playwright"],
        "review": ["review", "quality", "qa", "lint"],
        "docs": ["document", "readme", "changelog", "docs"],
        "security": ["security", "auth", "owasp", "vulnerabilit"],
    }

    for category, hints in category_hints.items():
        for hint in hints:
            if hint in name_lower or hint in content_lower:
                return category

    return "implementation"  # Default category


def _infer_tools(content: str) -> list[str]:
    """Infer allowed tools from prompt content."""
    content_lower = content.lower()
    tools = list(DEFAULT_TOOLS)

    tool_hints = {
        "Bash": ["bash", "shell", "command", "npm", "pip", "docker"],
        "Write": ["write", "create file", "generate"],
        "Edit": ["edit", "modify", "update file"],
        "WebSearch": ["search", "web search", "lookup"],
        "WebFetch": ["fetch", "download", "http"],
    }

    for tool, hints in tool_hints.items():
        if tool not in tools:
            for hint in hints:
                if hint in content_lower:
                    tools.append(tool)
                    break

    return tools


def generate_agent_definition(
    analysis: dict[str, Any],
    repo_name: str,
    prompt_content: str,
) -> dict[str, Any]:
    """Build a full aquarco agent definition YAML dict from analysis results."""
    name = analysis["name"]
    return {
        "apiVersion": "aquarco.agents/v1",
        "kind": "AgentDefinition",
        "metadata": {
            "name": f"{repo_name}-{name}",
            "version": "1.0.0",
            "description": analysis.get("description", f"Autoloaded agent: {name}"),
            "labels": {
                "source": "autoloaded",
                "repository": repo_name,
                "original-name": name,
            },
        },
        "spec": {
            "categories": [analysis.get("category", "implementation")],
            "priority": 50,
            "promptInline": prompt_content,
            "tools": {
                "allowed": analysis.get("tools", DEFAULT_TOOLS),
                "denied": [],
            },
            "resources": {
                "timeoutMinutes": 30,
                "maxConcurrent": 1,
                "maxTurns": 30,
                "maxCost": 5.0,
            },
            "environment": {},
        },
    }


def write_aquarco_config(
    repo_path: Path,
    definitions: list[dict[str, Any]],
) -> int:
    """Write generated agent definition YAML files to aquarco-config/agents/.

    Returns the number of files written.
    """
    config_dir = repo_path / "aquarco-config" / "agents"
    config_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for defn in definitions:
        name = defn.get("metadata", {}).get("name", "")
        if not name:
            continue

        out_path = config_dir / f"{name}.yaml"
        out_path.write_text(
            yaml.dump(defn, default_flow_style=False, sort_keys=False)
        )
        count += 1
        log.debug("agent_definition_written", agent=name, path=str(out_path))

    log.info("aquarco_config_written", count=count, dir=str(config_dir))
    return count


# --- Scan status DB helpers ---


async def create_scan_record(db: Database, repo_name: str) -> int:
    """Create a new scan record and return its ID."""
    row = await db.fetch_one(
        """INSERT INTO repo_agent_scans (repo_name, status, created_at)
           VALUES (%(repo_name)s, 'pending', NOW())
           RETURNING id""",
        {"repo_name": repo_name},
    )
    return row["id"]


async def update_scan_status(
    db: Database,
    scan_id: int,
    status: str,
    *,
    agents_found: int | None = None,
    agents_created: int | None = None,
    error_message: str | None = None,
) -> None:
    """Update scan record status and optional fields."""
    updates = ["status = %(status)s"]
    params: dict[str, Any] = {"scan_id": scan_id, "status": status}

    if status == "scanning":
        updates.append("started_at = NOW()")
    if status in ("completed", "failed"):
        updates.append("completed_at = NOW()")
    if agents_found is not None:
        updates.append("agents_found = %(agents_found)s")
        params["agents_found"] = agents_found
    if agents_created is not None:
        updates.append("agents_created = %(agents_created)s")
        params["agents_created"] = agents_created
    if error_message is not None:
        updates.append("error_message = %(error_message)s")
        params["error_message"] = error_message

    await db.execute(
        f"UPDATE repo_agent_scans SET {', '.join(updates)} WHERE id = %(scan_id)s",
        params,
    )


async def get_latest_scan(db: Database, repo_name: str) -> dict[str, Any] | None:
    """Get the most recent scan record for a repository."""
    return await db.fetch_one(
        """SELECT * FROM repo_agent_scans
           WHERE repo_name = %(repo_name)s
           ORDER BY created_at DESC
           LIMIT 1""",
        {"repo_name": repo_name},
    )


async def is_scan_in_progress(db: Database, repo_name: str) -> bool:
    """Check if a scan is currently in progress for a repository."""
    row = await db.fetch_one(
        """SELECT id FROM repo_agent_scans
           WHERE repo_name = %(repo_name)s
             AND status IN ('pending', 'scanning', 'analyzing', 'writing')
           LIMIT 1""",
        {"repo_name": repo_name},
    )
    return row is not None


async def check_rate_limit(db: Database, repo_name: str) -> bool:
    """Check if a scan was completed within the rate limit window.

    Returns True if rate-limited (scan too recent), False if OK to proceed.
    """
    row = await db.fetch_one(
        """SELECT id FROM repo_agent_scans
           WHERE repo_name = %(repo_name)s
             AND created_at > NOW() - INTERVAL '%(seconds)s seconds'
             AND status IN ('completed', 'failed')
           LIMIT 1""",
        {"repo_name": repo_name, "seconds": RATE_LIMIT_SECONDS},
    )
    return row is not None


# --- Agent definition DB helpers ---


async def deactivate_autoloaded_agents(db: Database, repo_name: str) -> int:
    """Deactivate all previously autoloaded agents for a repository.

    Returns the number of agents deactivated.
    """
    result = await db.fetch_one(
        """UPDATE agent_definitions
           SET is_active = false
           WHERE source = %(source)s AND is_active = true
           RETURNING COUNT(*) OVER() as count""",
        {"source": f"autoload:{repo_name}"},
    )
    count = result["count"] if result else 0
    log.info("autoloaded_agents_deactivated", repo_name=repo_name, count=count)
    return count


async def store_autoloaded_agents(
    db: Database,
    definitions: list[dict[str, Any]],
    repo_name: str,
) -> int:
    """Store autoloaded agent definitions in the database.

    Uses source='autoload:<repo_name>' to distinguish from other sources.
    Returns the number of agents stored.
    """
    source = f"autoload:{repo_name}"
    count = 0

    for doc in definitions:
        meta = doc.get("metadata", {})
        name = meta.get("name", "")
        version = meta.get("version", "1.0.0")
        if not name:
            continue

        # Deactivate previous versions
        await db.execute(
            """UPDATE agent_definitions
               SET is_active = false
               WHERE name = %(name)s AND version != %(version)s AND is_active = true""",
            {"name": name, "version": version},
        )

        # Upsert current version
        await db.execute(
            """INSERT INTO agent_definitions
                   (name, version, description, labels, spec, is_active, source)
               VALUES
                   (%(name)s, %(version)s, %(description)s, %(labels)s, %(spec)s, true, %(source)s)
               ON CONFLICT (name, version) DO UPDATE SET
                   description = EXCLUDED.description,
                   labels      = EXCLUDED.labels,
                   spec        = EXCLUDED.spec,
                   is_active   = true,
                   source      = EXCLUDED.source""",
            {
                "name": name,
                "version": version,
                "description": meta.get("description", ""),
                "labels": json.dumps(meta.get("labels", {})),
                "spec": json.dumps(doc.get("spec", {})),
                "source": source,
            },
        )
        count += 1
        log.debug("autoloaded_agent_stored", agent=name, source=source)

    log.info("autoloaded_agents_stored", count=count, repo_name=repo_name)
    return count


# --- Main orchestration ---


async def autoload_repo_agents(
    repo_path: Path,
    repo_name: str,
    db: Database,
    scan_id: int | None = None,
) -> dict[str, Any]:
    """Orchestrate the full agent autoloading flow.

    1. Scan .claude/agents/ for .md files
    2. Analyze each prompt
    3. Generate agent definitions
    4. Write aquarco-config/ YAML files
    5. Store definitions in DB
    6. Update scan status throughout

    Returns a summary dict with agents_found, agents_created, and any errors.
    """
    result: dict[str, Any] = {
        "agents_found": 0,
        "agents_created": 0,
        "error": None,
    }

    try:
        # Phase 1: Scanning
        if scan_id:
            await update_scan_status(db, scan_id, RepoAgentScanStatus.SCANNING.value)

        prompt_files = scan_repo_agents(repo_path)
        result["agents_found"] = len(prompt_files)

        if scan_id:
            await update_scan_status(
                db, scan_id, RepoAgentScanStatus.ANALYZING.value,
                agents_found=len(prompt_files),
            )

        if not prompt_files:
            if scan_id:
                await update_scan_status(
                    db, scan_id, RepoAgentScanStatus.COMPLETED.value,
                    agents_found=0, agents_created=0,
                )
            return result

        # Phase 2: Analyze prompts and generate definitions
        definitions: list[dict[str, Any]] = []
        for prompt_file in prompt_files:
            try:
                content = prompt_file.read_text(encoding="utf-8")
                analysis = analyze_agent_prompt(content, prompt_file.name)
                defn = generate_agent_definition(analysis, repo_name, content)
                definitions.append(defn)
            except Exception:
                log.exception("agent_prompt_analysis_error", file=str(prompt_file))

        # Phase 3: Write aquarco-config/
        if scan_id:
            await update_scan_status(db, scan_id, RepoAgentScanStatus.WRITING.value)

        write_aquarco_config(repo_path, definitions)

        # Phase 4: Store in DB (deactivate old, store new)
        await deactivate_autoloaded_agents(db, repo_name)
        agents_created = await store_autoloaded_agents(db, definitions, repo_name)
        result["agents_created"] = agents_created

        # Phase 5: Complete
        if scan_id:
            await update_scan_status(
                db, scan_id, RepoAgentScanStatus.COMPLETED.value,
                agents_found=len(prompt_files),
                agents_created=agents_created,
            )

        log.info(
            "agent_autoload_completed",
            repo_name=repo_name,
            agents_found=len(prompt_files),
            agents_created=agents_created,
        )

    except Exception as e:
        result["error"] = str(e)
        log.exception("agent_autoload_failed", repo_name=repo_name)
        if scan_id:
            await update_scan_status(
                db, scan_id, RepoAgentScanStatus.FAILED.value,
                error_message=str(e),
            )

    return result


def has_claude_agents(repo_path: Path) -> bool:
    """Check if a repository has a .claude/agents/ directory with .md files."""
    agents_dir = repo_path / ".claude" / "agents"
    if not agents_dir.is_dir():
        return False
    return any(agents_dir.glob("*.md"))

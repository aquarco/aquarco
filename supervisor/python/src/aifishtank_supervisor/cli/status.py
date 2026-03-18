"""Status reporting CLI command for AI Fishtank supervisor."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from ..config import load_config
from ..database import Database
from ..exceptions import ConnectionPoolError
from ..logging import get_logger, setup_logging

log = get_logger("status")

DEFAULT_CONFIG = "/home/agent/ai-fishtank/supervisor/config/supervisor.yaml"
DEFAULT_PID_FILE = "/var/run/aifishtank/supervisor.pid"


# ── Process status ────────────────────────────────────────────────────────────


def _get_supervisor_process_status(pid_file: str) -> dict[str, Any]:
    """Check supervisor PID file and process liveness."""
    pid_path = Path(pid_file)
    pid: int | None = None
    uptime: str | None = None
    status = "stopped"

    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
        except (ValueError, OSError):
            pid = None

        if pid is not None:
            try:
                os.kill(pid, 0)  # signal 0: existence check only
                status = "running"
                uptime = _compute_uptime(pid)
            except ProcessLookupError:
                status = "stale-pid"
            except PermissionError:
                # Process exists but we can't signal it — still counts as running
                status = "running"
                uptime = _compute_uptime(pid)

    return {
        "status": status,
        "pid": str(pid) if pid is not None else "",
        "uptime": uptime or "unknown",
    }


def _compute_uptime(pid: int) -> str | None:
    """Compute process uptime from /proc/<pid>/stat (Linux only)."""
    stat_path = Path(f"/proc/{pid}/stat")
    uptime_path = Path("/proc/uptime")

    if not stat_path.exists() or not uptime_path.exists():
        return None

    try:
        stat_fields = stat_path.read_text().split()
        start_ticks = int(stat_fields[21])  # field 22, 0-indexed at 21

        hz_str = _getconf_clk_tck()
        hz = int(hz_str) if hz_str else 100

        total_uptime_s = float(uptime_path.read_text().split()[0])
        elapsed_s = int(total_uptime_s - (start_ticks / hz))

        hours, remainder = divmod(elapsed_s, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}h {minutes}m {seconds}s"
    except (IndexError, ValueError, OSError):
        return None


def _getconf_clk_tck() -> str:
    """Read CLK_TCK via sysconf rather than subprocess."""
    try:
        import ctypes
        libc = ctypes.CDLL(None)
        sc_clk_tck = 2  # _SC_CLK_TCK on Linux
        result = libc.sysconf(sc_clk_tck)
        return str(result) if result > 0 else "100"
    except Exception:
        return "100"


# ── Registry summary ──────────────────────────────────────────────────────────


def _get_registry_summary(agents_dir: str) -> dict[str, Any]:
    """Count agents and categories from the filesystem registry."""
    agents_path = Path(agents_dir)
    registry_file = agents_path.parent / "schemas" / "agent-registry.json"

    agent_count = 0
    categories: list[str] = []

    if registry_file.exists():
        try:
            data = json.loads(registry_file.read_text())
            agents_list = data.get("agents", []) if isinstance(data, dict) else data
            if isinstance(agents_list, list):
                agent_count = len(agents_list)
                seen: set[str] = set()
                for agent in agents_list:
                    for cat in agent.get("spec", {}).get("categories", []):
                        seen.add(cat)
                categories = sorted(seen)
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    elif agents_path.exists():
        agent_count = sum(1 for _ in agents_path.glob("*.yaml"))

    return {"agent_count": agent_count, "categories": categories}


# ── Database queries ──────────────────────────────────────────────────────────


async def _query_task_queue_stats(db: Database) -> dict[str, int]:
    """GROUP BY status on the tasks table."""
    rows = await db.fetch_all(
        "SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status ORDER BY status"
    )
    return {row["status"]: int(row["cnt"]) for row in rows}


async def _query_active_instances(db: Database) -> list[dict[str, Any]]:
    """Active or recently-executed agent instances."""
    rows = await db.fetch_all(
        """
        SELECT
            agent_name,
            active_count,
            total_executions,
            to_char(last_execution_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_execution_at
        FROM agent_instances
        WHERE active_count > 0
           OR last_execution_at IS NOT NULL
        ORDER BY active_count DESC, last_execution_at DESC NULLS LAST
        LIMIT 20
        """
    )
    return [dict(row) for row in rows]


async def _query_recent_tasks(db: Database) -> list[dict[str, Any]]:
    """Last 10 tasks ordered by creation time."""
    rows = await db.fetch_all(
        """
        SELECT
            id,
            title,
            category,
            status,
            pipeline,
            repository,
            to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at,
            to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS updated_at
        FROM tasks
        ORDER BY created_at DESC
        LIMIT 10
        """
    )
    return [dict(row) for row in rows]


# ── Rendering ─────────────────────────────────────────────────────────────────


def _render_human(
    generated_at: str,
    proc: dict[str, Any],
    registry: dict[str, Any],
    task_stats: dict[str, int],
    instances: list[dict[str, Any]],
    recent: list[dict[str, Any]],
    db_error: str | None,
) -> None:
    """Print human-readable status to stdout."""
    print("==============================")
    print(" AI Fishtank Supervisor Status")
    print(f" {generated_at}")
    print("==============================")

    print("\nSupervisor Status")
    print(f"  Status : {proc['status']}")
    print(f"  PID    : {proc['pid'] or 'n/a'}")
    print(f"  Uptime : {proc['uptime']}")

    print("\nAgent Registry")
    print(f"  Agents     : {registry['agent_count']}")
    cats = ", ".join(registry["categories"]) if registry["categories"] else "none"
    print(f"  Categories : {cats}")

    print("\nTask Queue Stats")
    if db_error:
        print(f"  (database unavailable: {db_error})")
    elif not task_stats:
        print("  (no data)")
    else:
        for status, cnt in sorted(task_stats.items()):
            print(f"  {status:<12} : {cnt}")

    print("\nActive Agent Instances")
    if db_error:
        print(f"  (database unavailable: {db_error})")
    elif not instances:
        print("  (none)")
    else:
        print(f"  {'AGENT':<30}  {'ACTIVE':<6}  {'TOTAL':<10}  LAST EXECUTION")
        for row in instances:
            print(
                f"  {str(row['agent_name']):<30}  "
                f"{str(row['active_count']):<6}  "
                f"{str(row['total_executions']):<10}  "
                f"{row['last_execution_at'] or ''}"
            )

    print("\nRecent Tasks (last 10)")
    if db_error:
        print(f"  (database unavailable: {db_error})")
    elif not recent:
        print("  (none)")
    else:
        print(f"  {'ID':<40}  {'STATUS':<14}  {'CATEGORY':<10}  {'PIPELINE':<20}  CREATED")
        for row in recent:
            print(
                f"  {str(row['id']):<40}  "
                f"{str(row['status']):<14}  "
                f"{str(row['category']):<10}  "
                f"{str(row['pipeline']):<20}  "
                f"{row['created_at'] or ''}"
            )

    print("\n==============================")


# ── Typer command ─────────────────────────────────────────────────────────────


def status(
    config: str = typer.Option(
        DEFAULT_CONFIG,
        "--config",
        "-c",
        help="Path to supervisor.yaml",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON instead of human-readable text",
    ),
    pid_file: str = typer.Option(
        DEFAULT_PID_FILE,
        "--pid-file",
        help="Path to supervisor PID file",
        envvar="SUPERVISOR_PID_FILE",
    ),
) -> None:
    """Show supervisor status, task queue stats, and recent tasks."""
    setup_logging(level="warning")
    asyncio.run(_run_status(config, output_json, pid_file))


async def _run_status(config_file: str, output_json: bool, pid_file: str) -> None:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Load config (best-effort — status must not hard-fail on bad config)
    db_url: str | None = None
    agents_dir = "/home/agent/ai-fishtank/agents/definitions"

    try:
        cfg = load_config(config_file)
        db_url = cfg.spec.database.url
        agents_dir = cfg.spec.agents_dir
    except Exception as exc:
        log.warning("config_load_failed", error=str(exc))

    # Gather non-DB data
    proc = _get_supervisor_process_status(pid_file)
    registry = _get_registry_summary(agents_dir)

    # Gather DB data
    task_stats: dict[str, int] = {}
    instances: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    db_error: str | None = None

    if db_url:
        db = Database(dsn=db_url, max_connections=2)
        try:
            await db.connect()
            task_stats = await _query_task_queue_stats(db)
            instances = await _query_active_instances(db)
            recent = await _query_recent_tasks(db)
        except ConnectionPoolError as exc:
            db_error = str(exc)
        except Exception as exc:
            db_error = str(exc)
        finally:
            try:
                await db.close()
            except Exception:
                pass
    else:
        db_error = "no database URL (config not loaded)"

    # Output
    if output_json:
        doc: dict[str, Any] = {
            "generated_at": generated_at,
            "supervisor": proc,
            "registry": registry,
            "task_queue": task_stats,
            "agent_instances": instances,
            "recent_tasks": recent,
        }
        if db_error:
            doc["db_error"] = db_error
        typer.echo(json.dumps(doc, indent=2, default=str))
    else:
        _render_human(generated_at, proc, registry, task_stats, instances, recent, db_error)

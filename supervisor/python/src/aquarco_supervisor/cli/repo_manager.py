"""Repo manager CLI — Docker Compose stack management for target repositories.

Each repository gets its own docker-compose.yml and .env file generated from
templates in supervisor/templates/.  Ports are auto-allocated or read from
supervisor.yaml (RepositoryConfig.ports).

Commands
--------
  setup    <repo_name> <clone_dir> <ports_json>  Copy templates + substitute vars
  start    <repo_name> <clone_dir>               docker compose up -d
  stop     <repo_name> <clone_dir>               docker compose down
  restart  <repo_name> <clone_dir> [service]     docker compose restart [service]
  status   <repo_name> <clone_dir>               docker compose ps
  logs     <repo_name> <clone_dir>               docker compose logs (--follow)
  destroy  <repo_name> <clone_dir>               docker compose down -v  (REMOVES DATA)
  list                                           Print running repos
  alloc    <repo_name>                           Print port allocation as JSON
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional

import typer

from ..config import get_repository_config, load_config
from ..logging import get_logger, setup_logging

# ---------------------------------------------------------------------------
# Module-level setup
# ---------------------------------------------------------------------------

repo_app = typer.Typer(
    name="repo",
    help="Manage Docker Compose stacks for target repositories.",
    no_args_is_help=True,
)

log = get_logger("repo-manager")

# Port allocation base values — each repo occupies a consecutive slot:
#   slot 1 → frontend 3001 / api 4001 / postgres 5433
#   slot 2 → frontend 3002 / api 4002 / postgres 5434
#   ...
_FRONTEND_PORT_BASE = 3000
_API_PORT_BASE = 4000
_POSTGRES_PORT_BASE = 5432

_DEFAULT_REPOS_ROOT = "/home/agent/repos"
_DEFAULT_CONFIG = "/home/agent/aquarco/supervisor/config/supervisor.yaml"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compose_file(clone_dir: Path) -> Path:
    return clone_dir / "docker-compose.yml"


def _env_file(clone_dir: Path) -> Path:
    return clone_dir / ".env"


def _die(msg: str) -> None:
    log.error("fatal", msg=msg)
    typer.echo(f"ERROR: {msg}", err=True)
    raise typer.Exit(1)


async def _run_compose(
    compose_file: Path,
    env_file: Path,
    *compose_args: str,
) -> int:
    """Run a docker compose command, streaming output to stdout/stderr.

    Returns the process exit code.
    """
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "--env-file", str(env_file),
        *compose_args,
    ]
    log.debug("running_compose", cmd=" ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    await proc.wait()
    return proc.returncode or 0


async def _compose_quiet(
    compose_file: Path,
    env_file: Path,
    *compose_args: str,
) -> tuple[int, str, str]:
    """Run a docker compose command and capture stdout/stderr.

    Returns (returncode, stdout_text, stderr_text).
    """
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "--env-file", str(env_file),
        *compose_args,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _require_compose_files(clone_dir: Path, repo_name: str) -> tuple[Path, Path]:
    """Return (compose_file, env_file), exiting if either is absent."""
    cf = _compose_file(clone_dir)
    ef = _env_file(clone_dir)
    if not cf.exists():
        _die(
            f"docker-compose.yml not found in {clone_dir} — "
            "run 'aquarco-supervisor repo setup' first"
        )
    if not ef.exists():
        _die(
            f".env not found in {clone_dir} — "
            "run 'aquarco-supervisor repo setup' first"
        )
    return cf, ef


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


def _allocate_ports(
    repo_name: str,
    repos_root: Path,
    config_file: Optional[str],
) -> dict[str, int]:
    """Return a port dict for repo_name.

    Priority:
      1. Explicit ports in supervisor.yaml  (RepositoryConfig.ports)
      2. Auto-allocate from the highest slot found in existing .env files.
    """
    # 1. Try supervisor.yaml
    if config_file:
        try:
            cfg = load_config(config_file)
            repo_cfg = get_repository_config(cfg, repo_name)
            if repo_cfg and repo_cfg.get("ports"):
                ports = repo_cfg["ports"]
                fe = ports.get("frontend")
                api = ports.get("api")
                pg = ports.get("postgres")
                if fe and api and pg:
                    log.debug(
                        "ports_from_config",
                        repo=repo_name,
                        frontend=fe,
                        api=api,
                        postgres=pg,
                    )
                    return {"frontend": int(fe), "api": int(api), "postgres": int(pg)}
        except Exception as exc:
            log.warning("config_load_failed", error=str(exc))

    # 2. Auto-allocate
    max_slot = 0
    for env_path in repos_root.glob("*/.env"):
        try:
            text = env_path.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("FRONTEND_PORT="):
                try:
                    port = int(line.split("=", 1)[1].strip())
                    slot = port - _FRONTEND_PORT_BASE
                    if slot > max_slot:
                        max_slot = slot
                except ValueError:
                    pass

    next_slot = max_slot + 1
    ports = {
        "frontend": _FRONTEND_PORT_BASE + next_slot,
        "api": _API_PORT_BASE + next_slot,
        "postgres": _POSTGRES_PORT_BASE + next_slot,
    }
    log.debug("ports_auto_allocated", repo=repo_name, slot=next_slot, **ports)
    return ports


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@repo_app.command("setup")
def cmd_setup(
    repo_name: str = typer.Argument(..., help="Logical name of the repository"),
    clone_dir: str = typer.Argument(..., help="Absolute path to the cloned repository"),
    ports_json: str = typer.Argument(
        ...,
        help='JSON object with port keys: {"frontend":N,"api":N,"postgres":N}',
    ),
    templates_dir: str = typer.Option(
        "",
        "--templates-dir",
        help="Override path to supervisor/templates directory",
    ),
) -> None:
    """Copy compose + env templates to the repo dir, substituting port placeholders.

    Template tokens replaced in the .env file:
      __REPO_NAME__      — repo_name argument
      __FRONTEND_PORT__  — frontend port
      __API_PORT__       — API port
      __POSTGRES_PORT__  — Postgres port

    The docker-compose.yml template is copied verbatim; Docker Compose reads
    ports from the generated .env file at runtime.
    """
    setup_logging()
    clone_path = Path(clone_dir)
    if not clone_path.is_dir():
        _die(f"clone_dir does not exist: {clone_dir}")

    # Validate repo_name: only alphanumeric, hyphens, and underscores permitted.
    # This prevents newline injection and shell metacharacter smuggling into the
    # generated .env file and log messages.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", repo_name):
        _die(
            f"Invalid repo_name '{repo_name}': only letters, digits, hyphens "
            "and underscores are allowed"
        )

    # Parse ports JSON
    try:
        ports = json.loads(ports_json)
    except json.JSONDecodeError as exc:
        _die(f"Invalid ports_json: {exc}")
        return  # unreachable — satisfies type checker

    fe_port = ports.get("frontend")
    api_port = ports.get("api")
    pg_port = ports.get("postgres")

    if not all(isinstance(p, int) for p in (fe_port, api_port, pg_port)):
        _die("ports_json must have integer keys 'frontend', 'api', 'postgres'")

    # Resolve templates directory
    if templates_dir:
        tmpl_dir = Path(templates_dir)
    else:
        # Default: relative to this file → supervisor/templates
        # parents[0]=cli/ [1]=aquarco_supervisor/ [2]=src/ [3]=python/ [4]=supervisor/
        tmpl_dir = Path(__file__).parents[4] / "templates"

    compose_tmpl = tmpl_dir / "docker-compose.repo.yml.tmpl"
    env_tmpl = tmpl_dir / "repo.env.tmpl"

    if not compose_tmpl.exists():
        _die(f"Compose template not found: {compose_tmpl}")
    if not env_tmpl.exists():
        _die(f"Env template not found: {env_tmpl}")

    # Copy compose file verbatim
    dest_compose = _compose_file(clone_path)
    dest_compose.write_text(compose_tmpl.read_text())

    # Substitute tokens in the .env template
    env_content = (
        env_tmpl.read_text()
        .replace("__REPO_NAME__", repo_name)
        .replace("__FRONTEND_PORT__", str(fe_port))
        .replace("__API_PORT__", str(api_port))
        .replace("__POSTGRES_PORT__", str(pg_port))
    )
    dest_env = _env_file(clone_path)
    dest_env.write_text(env_content)

    log.info(
        "repo_setup_complete",
        repo=repo_name,
        dir=str(clone_path),
        frontend=fe_port,
        api=api_port,
        postgres=pg_port,
    )
    typer.echo(
        f"Setup complete: {repo_name}  "
        f"(frontend={fe_port}, api={api_port}, postgres={pg_port})"
    )


@repo_app.command("start")
def cmd_start(
    repo_name: str = typer.Argument(..., help="Logical name of the repository"),
    clone_dir: str = typer.Argument(..., help="Absolute path to the cloned repository"),
) -> None:
    """Start the Docker Compose stack for a repository (docker compose up -d)."""
    setup_logging()
    clone_path = Path(clone_dir)
    cf, ef = _require_compose_files(clone_path, repo_name)

    log.info("repo_starting", repo=repo_name)
    rc = asyncio.run(_run_compose(cf, ef, "up", "-d"))
    if rc != 0:
        _die(f"docker compose up -d failed (exit {rc})")
    log.info("repo_started", repo=repo_name)


@repo_app.command("stop")
def cmd_stop(
    repo_name: str = typer.Argument(..., help="Logical name of the repository"),
    clone_dir: str = typer.Argument(..., help="Absolute path to the cloned repository"),
) -> None:
    """Stop the Docker Compose stack for a repository (docker compose down)."""
    setup_logging()
    clone_path = Path(clone_dir)
    cf, ef = _require_compose_files(clone_path, repo_name)

    log.info("repo_stopping", repo=repo_name)
    rc = asyncio.run(_run_compose(cf, ef, "down"))
    if rc != 0:
        _die(f"docker compose down failed (exit {rc})")
    log.info("repo_stopped", repo=repo_name)


@repo_app.command("restart")
def cmd_restart(
    repo_name: str = typer.Argument(..., help="Logical name of the repository"),
    clone_dir: str = typer.Argument(..., help="Absolute path to the cloned repository"),
    service: Optional[str] = typer.Argument(
        None, help="Optional service name to restart (default: all)"
    ),
) -> None:
    """Restart the whole stack or a single service (docker compose restart)."""
    setup_logging()
    clone_path = Path(clone_dir)
    cf, ef = _require_compose_files(clone_path, repo_name)

    log.info("repo_restarting", repo=repo_name, service=service or "all")
    extra: list[str] = [service] if service else []
    rc = asyncio.run(_run_compose(cf, ef, "restart", *extra))
    if rc != 0:
        _die(f"docker compose restart failed (exit {rc})")
    log.info("repo_restarted", repo=repo_name, service=service or "all")


@repo_app.command("status")
def cmd_status(
    repo_name: str = typer.Argument(..., help="Logical name of the repository"),
    clone_dir: str = typer.Argument(..., help="Absolute path to the cloned repository"),
) -> None:
    """Show container status for a repository stack (docker compose ps)."""
    setup_logging()
    clone_path = Path(clone_dir)
    cf, ef = _require_compose_files(clone_path, repo_name)

    rc = asyncio.run(_run_compose(cf, ef, "ps"))
    if rc != 0:
        raise typer.Exit(rc)


@repo_app.command("logs")
def cmd_logs(
    repo_name: str = typer.Argument(..., help="Logical name of the repository"),
    clone_dir: str = typer.Argument(..., help="Absolute path to the cloned repository"),
    service: Optional[str] = typer.Argument(
        None, help="Service name to tail (default: all services)"
    ),
    lines: int = typer.Option(100, "--lines", "-n", help="Number of tail lines"),
    follow: bool = typer.Option(True, "--follow/--no-follow", "-f/-F", help="Follow log output"),
) -> None:
    """Stream or dump logs for a repository stack (docker compose logs)."""
    setup_logging()
    clone_path = Path(clone_dir)
    cf, ef = _require_compose_files(clone_path, repo_name)

    args = ["logs", f"--tail={lines}"]
    if follow:
        args.append("--follow")
    if service:
        args.append(service)

    rc = asyncio.run(_run_compose(cf, ef, *args))
    if rc != 0:
        raise typer.Exit(rc)


@repo_app.command("destroy")
def cmd_destroy(
    repo_name: str = typer.Argument(..., help="Logical name of the repository"),
    clone_dir: str = typer.Argument(..., help="Absolute path to the cloned repository"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt"
    ),
) -> None:
    """Stop the stack and remove all volumes — DESTROYS ALL DATA (docker compose down -v)."""
    setup_logging()
    clone_path = Path(clone_dir)
    cf, ef = _require_compose_files(clone_path, repo_name)

    if not yes:
        typer.confirm(
            f"This will destroy all volumes for '{repo_name}'. Continue?",
            abort=True,
        )

    log.warning("repo_destroying", repo=repo_name, dir=str(clone_path))
    rc = asyncio.run(_run_compose(cf, ef, "down", "-v"))
    if rc != 0:
        _die(f"docker compose down -v failed (exit {rc})")
    log.info("repo_destroyed", repo=repo_name)


@repo_app.command("list")
def cmd_list(
    repos_root: str = typer.Option(
        _DEFAULT_REPOS_ROOT,
        "--repos-root",
        help="Root directory containing per-repo clone directories",
    ),
) -> None:
    """List all repositories that have at least one running container.

    Output format: repo_name<TAB>clone_dir  (one per line)
    """
    setup_logging()

    root = Path(repos_root)
    if not root.is_dir():
        log.warning("repos_root_missing", path=str(root))
        return

    async def _check_all() -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []
        for compose_path in sorted(root.glob("*/docker-compose.yml")):
            repo_dir = compose_path.parent
            repo_name = repo_dir.name
            env_path = repo_dir / ".env"
            if not env_path.exists():
                continue
            rc, stdout, _ = await _compose_quiet(
                compose_path,
                env_path,
                "ps", "--status", "running", "--quiet",
            )
            if rc == 0:
                running = [ln for ln in stdout.splitlines() if ln.strip()]
                if running:
                    results.append((repo_name, str(repo_dir)))
        return results

    running = asyncio.run(_check_all())
    if not running:
        log.info("no_running_stacks", repos_root=str(root))
        typer.echo("No running repo stacks found.")
        return

    for name, path in running:
        typer.echo(f"{name}\t{path}")


@repo_app.command("alloc")
def cmd_alloc(
    repo_name: str = typer.Argument(..., help="Logical name of the repository"),
    repos_root: str = typer.Option(
        _DEFAULT_REPOS_ROOT,
        "--repos-root",
        help="Root directory used when scanning for existing port allocations",
    ),
    config_file: str = typer.Option(
        _DEFAULT_CONFIG,
        "--config",
        "-c",
        help="Supervisor config file (used for explicit port overrides)",
    ),
) -> None:
    """Allocate (or look up) ports for a repository and print them as JSON.

    Priority:
      1. Explicit ports defined in supervisor.yaml under the repository entry.
      2. Auto-allocate the next free consecutive slot above the base ports.

    Output: {"frontend": N, "api": N, "postgres": N}
    """
    setup_logging()
    ports = _allocate_ports(
        repo_name=repo_name,
        repos_root=Path(repos_root),
        config_file=config_file if Path(config_file).exists() else None,
    )
    typer.echo(json.dumps(ports))

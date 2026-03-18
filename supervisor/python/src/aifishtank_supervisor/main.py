"""Supervisor main loop and entry point."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from .cli.agents import app as agents_app
from .cli.auth_helper import auth_watch
from .cli.repo_manager import repo_app
from .cli.status import status
from .config import load_config, load_secrets
from .database import Database
from .logging import get_logger, setup_logging
from .models import SupervisorConfig
from .pipeline.agent_registry import AgentRegistry
from .pipeline.executor import PipelineExecutor
from .pollers.external_triggers import ExternalTriggersPoller
from .pollers.github_source import GitHubSourcePoller
from .pollers.github_tasks import GitHubTasksPoller
from .task_queue import TaskQueue
from .utils import url_to_slug
from .workers.clone_worker import CloneWorker
from .workers.pull_worker import PullWorker

app = typer.Typer()
app.add_typer(repo_app, name="repo")
app.add_typer(agents_app, name="agents", help="Agent discovery and validation commands.")
app.command("status")(status)
app.command("auth-watch")(auth_watch)
log = get_logger("supervisor")

DEFAULT_CONFIG = "/home/agent/ai-fishtank/supervisor/config/supervisor.yaml"


class Supervisor:
    """Main supervisor process."""

    def __init__(self, config: SupervisorConfig, secrets: dict[str, str]) -> None:
        self._config = config
        self._secrets = secrets
        self._shutdown = False
        self._shutdown_event: asyncio.Event | None = None
        self._reload_requested = False
        self._config_file = ""
        self._start_time = datetime.now(timezone.utc)

        # Components (initialized in start())
        self._db: Database | None = None
        self._tq: TaskQueue | None = None
        self._registry: AgentRegistry | None = None
        self._executor: PipelineExecutor | None = None
        self._clone_worker: CloneWorker | None = None
        self._pull_worker: PullWorker | None = None
        self._pollers: list[Any] = []

        # Poller timing
        self._poller_last_run: dict[str, float] = {}
        self._last_health_report: float = 0
        self._in_flight: set[asyncio.Task[None]] = set()

    async def start(self, config_file: str) -> None:
        """Initialize components and start the main loop."""
        self._config_file = config_file
        self._shutdown_event = asyncio.Event()

        # Setup logging
        setup_logging(
            level=self._config.spec.logging.level,
            log_file=self._config.spec.logging.file,
        )

        log.info(
            "supervisor_starting",
            version=self._config.metadata.get("version", "1.0.0"),
            pid=os.getpid(),
        )

        # Set up GitHub auth env vars for all subprocesses
        self._apply_github_env()

        # Initialize database
        self._db = Database(
            dsn=self._config.spec.database.url,
            max_connections=self._config.spec.database.max_connections,
        )
        await self._db.connect()

        # Initialize components
        self._tq = TaskQueue(
            self._db,
            max_retries=self._config.spec.global_limits.max_retries,
        )
        self._registry = AgentRegistry(
            self._db,
            agents_dir=self._config.spec.agents_dir,
            prompts_dir=self._config.spec.prompts_dir,
        )
        await self._registry.load()

        self._executor = PipelineExecutor(
            self._db, self._tq, self._registry, self._config
        )
        self._clone_worker = CloneWorker(
            self._db, github_token=self._secrets.get("github_token")
        )
        self._pull_worker = PullWorker(self._db)

        # Initialize pollers
        self._pollers = [
            GitHubTasksPoller(self._config, self._tq),
            GitHubSourcePoller(self._config, self._tq),
            ExternalTriggersPoller(self._config, self._tq),
        ]

        # Install signal handlers
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self._handle_shutdown)
        loop.add_signal_handler(signal.SIGINT, self._handle_shutdown)
        loop.add_signal_handler(signal.SIGHUP, self._handle_reload)

        log.info("supervisor_started")
        await self._main_loop()

    async def _main_loop(self) -> None:
        """Run the supervisor main loop."""
        cooldown = self._config.spec.global_limits.cooldown_between_tasks_seconds

        while not self._shutdown:
            try:
                # Config reload if requested
                if self._reload_requested:
                    await self._reload_config()
                    self._reload_requested = False

                # Re-read secrets (token may appear after user logs in via web UI)
                self._refresh_secrets()

                # Clone pending repos
                if self._clone_worker:
                    await self._clone_worker.clone_pending_repos()

                # Pull ready repos (every 30s)
                if self._should_run("repo-pull", 30) and self._pull_worker:
                    await self._pull_worker.pull_ready_repos()
                    self._mark_ran("repo-pull")

                # Run pollers
                await self._run_pollers()

                # Dispatch pending tasks
                await self._dispatch_pending_tasks()

                # Check timed-out tasks
                await self._check_timed_out_tasks()

                # Health report
                await self._maybe_report_health()

            except Exception:
                log.exception("main_loop_error")
            finally:
                # Clean up completed in-flight tasks (always, even after errors)
                self._in_flight = {t for t in self._in_flight if not t.done()}

            # Wait for cooldown or shutdown
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=cooldown
                )
            except asyncio.TimeoutError:
                pass

        # Graceful shutdown: wait for in-flight tasks
        if self._in_flight:
            log.info("draining_tasks", count=len(self._in_flight))
            await asyncio.gather(*self._in_flight, return_exceptions=True)

        if self._db:
            await self._db.close()
        log.info("supervisor_stopped")

    async def _run_pollers(self) -> None:
        """Run each poller if its interval has elapsed."""
        for poller in self._pollers:
            if not poller.is_enabled():
                continue
            interval = poller.get_interval()
            if not self._should_run(poller.name, interval):
                continue
            try:
                await poller.poll()
            except Exception:
                log.exception("poller_error", poller=poller.name)
            self._mark_ran(poller.name)

    async def _dispatch_pending_tasks(self) -> None:
        """Dispatch pending tasks to agents."""
        if not self._tq or not self._registry or not self._executor or not self._db:
            return

        # Check capacity
        active = await self._db.fetch_val(
            "SELECT COALESCE(SUM(active_count), 0) FROM agent_instances"
        )
        max_concurrent = self._config.spec.global_limits.max_concurrent_agents
        available_slots = max_concurrent - (active or 0)

        if available_slots <= 0:
            return

        for _ in range(available_slots):
            task = await self._tq.get_next_task()
            if not task:
                break

            # Launch task in background
            coro = self._run_task(task.id, task.pipeline or "", task.initial_context or {})
            t = asyncio.create_task(coro)
            self._in_flight.add(t)

    async def _run_task(
        self, task_id: str, pipeline: str, initial_context: dict[str, Any]
    ) -> None:
        """Execute a single task (runs as background coroutine)."""
        if not self._tq or not self._executor:
            return

        try:
            await self._tq.assign_agent(task_id, "pending-assignment")
            await self._executor.execute_pipeline(pipeline, task_id, initial_context)
        except Exception:
            log.exception("task_execution_error", task_id=task_id)
            if self._tq:
                await self._tq.fail_task(task_id, "Unhandled execution error")

    async def _check_timed_out_tasks(self) -> None:
        """Mark executing tasks that have exceeded the timeout."""
        if not self._tq:
            return
        timed_out = await self._tq.get_timed_out_tasks(timeout_minutes=90)
        for task_id in timed_out:
            log.warning("task_timed_out", task_id=task_id)
            await self._tq.fail_task(task_id, "Task execution timed out (90 min)")

    async def _maybe_report_health(self) -> None:
        """Post a health report if the interval has elapsed."""
        if not self._config.spec.health.enabled:
            return

        interval = self._config.spec.health.report_interval_minutes * 60
        now = time.time()
        if now - self._last_health_report < interval:
            return
        self._last_health_report = now

        if not self._db:
            return

        try:
            rows = await self._db.fetch_all(
                """
                SELECT status, COUNT(*) as count FROM tasks
                WHERE created_at > NOW() - INTERVAL '24 hours'
                GROUP BY status
                """
            )

            stats = {row["status"]: row["count"] for row in rows}
            uptime_minutes = int((now - self._start_time.timestamp()) / 60)

            report = _build_health_report(stats, uptime_minutes)

            # Post to GitHub issue
            repo = self._config.spec.repositories[0] if self._config.spec.repositories else None
            if repo:
                slug = url_to_slug(repo.url)
                issue_number = self._config.spec.health.issue_number
                if slug:
                    proc = await asyncio.create_subprocess_exec(
                        "gh", "issue", "comment", str(issue_number),
                        "--repo", slug,
                        "--body", report,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        _, stderr = await asyncio.wait_for(
                            proc.communicate(), timeout=30
                        )
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                        log.warning("health_report_post_timed_out")
                        return
                    if proc.returncode != 0:
                        log.warning(
                            "health_report_post_failed",
                            returncode=proc.returncode,
                            error=stderr.decode("utf-8", errors="replace").strip(),
                        )

            log.info("health_reported", stats=stats)
        except Exception:
            log.exception("health_report_error")

    async def _reload_config(self) -> None:
        """Reload configuration from file."""
        try:
            self._config = load_config(self._config_file)
            self._secrets = load_secrets(self._config)
            self._apply_github_env()
            log.info("config_reloaded")
        except Exception:
            log.exception("config_reload_failed")

    def _refresh_secrets(self) -> None:
        """Re-read secrets from disk if they changed (e.g. user logged in)."""
        new_secrets = load_secrets(self._config)
        new_token = new_secrets.get("github_token")
        old_token = self._secrets.get("github_token")
        if new_token != old_token:
            self._secrets = new_secrets
            self._apply_github_env()
            if new_token and not old_token:
                log.info("github_token_detected")

    def _apply_github_env(self) -> None:
        """Set up GitHub auth env vars for all subprocesses.

        - GH_TOKEN: used by gh CLI (pollers, health reports)
        - GITHUB_TOKEN + GIT_ASKPASS: used by git for HTTPS auth
        """
        import stat
        import tempfile

        github_token = self._secrets.get("github_token")
        if not github_token:
            return

        os.environ["GH_TOKEN"] = github_token
        os.environ["GITHUB_TOKEN"] = github_token
        os.environ["GIT_TERMINAL_PROMPT"] = "0"

        askpass_path = Path(tempfile.gettempdir()) / "git-askpass-helper.sh"
        if not askpass_path.exists():
            askpass_path.write_text(
                '#!/bin/sh\n'
                'case "$1" in\n'
                '  *assword*) echo "$GITHUB_TOKEN" ;;\n'
                '  *) echo "x-access-token" ;;\n'
                'esac\n'
            )
            askpass_path.chmod(stat.S_IRWXU)
        os.environ["GIT_ASKPASS"] = str(askpass_path)

    def _handle_shutdown(self) -> None:
        """Signal handler for SIGTERM/SIGINT."""
        log.info("shutdown_requested")
        self._shutdown = True
        if self._shutdown_event:
            self._shutdown_event.set()

    def _handle_reload(self) -> None:
        """Signal handler for SIGHUP."""
        log.info("reload_requested")
        self._reload_requested = True

    def _should_run(self, name: str, interval: int) -> bool:
        """Check if enough time has passed to run a poller."""
        last = self._poller_last_run.get(name, 0)
        return time.time() - last >= interval

    def _mark_ran(self, name: str) -> None:
        """Record that a poller just ran."""
        self._poller_last_run[name] = time.time()


def _build_health_report(
    stats: dict[str, int], uptime_minutes: int
) -> str:
    """Build a Markdown health report."""
    lines = [
        "## Supervisor Health Report",
        "",
        f"**Uptime:** {uptime_minutes} minutes",
        f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "### Tasks (last 24h)",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status, count in sorted(stats.items()):
        lines.append(f"| {status} | {count} |")
    if not stats:
        lines.append("| (none) | 0 |")
    return "\n".join(lines)


@app.command()
def run(
    config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Config file path"),
) -> None:
    """Start the AI Fishtank supervisor."""
    cfg = load_config(config)
    secrets = load_secrets(cfg)
    supervisor = Supervisor(cfg, secrets)
    asyncio.run(supervisor.start(config))


if __name__ == "__main__":
    app()

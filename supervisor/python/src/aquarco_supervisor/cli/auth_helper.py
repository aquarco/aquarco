"""Claude CLI auth IPC helper — Python replacement for claude-auth-helper.sh."""

from __future__ import annotations

import asyncio
import json
import signal
import stat
from pathlib import Path

import typer

from ..logging import get_logger, setup_logging

log = get_logger("auth-helper")

DEFAULT_IPC_DIR = "/var/lib/aquarco/claude-ipc"
DEFAULT_POLL_INTERVAL = 2
CLAUDE_AUTH_STATUS_TIMEOUT = 5  # seconds
CLAUDE_LOGOUT_TIMEOUT = 10  # seconds

# ── Subprocess helpers ────────────────────────────────────────────────────────


async def _run_command(
    *args: str,
    timeout: int = 30,
    stdin_data: bytes | None = None,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_data), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return -1, "", "timed out"

    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout_b.decode("utf-8", errors="replace"), stderr_b.decode("utf-8", errors="replace")


def _kill_previous_login_processes() -> None:
    """Send SIGTERM to any lingering claude auth / oauth driver processes."""
    import subprocess  # noqa: PLC0415

    for pattern in ("claude-auth-oauth", "claude-auth-pexpect", "claude auth login"):
        try:
            subprocess.run(
                ["pkill", "-f", pattern],
                check=False,
                capture_output=True,
            )
        except FileNotFoundError:
            pass  # pkill not available


# ── IPC request handlers ──────────────────────────────────────────────────────


async def _handle_login(ipc_dir: Path, oauth_script: Path | None) -> None:
    """Consume login-request and launch the OAuth PKCE driver in the background."""
    request_file = ipc_dir / "login-request"
    if not request_file.exists():
        return

    log.info("login_request_received")
    request_file.unlink(missing_ok=True)
    (ipc_dir / "login-response").unlink(missing_ok=True)
    (ipc_dir / "code-submit").unlink(missing_ok=True)
    (ipc_dir / "code-complete").unlink(missing_ok=True)

    # Terminate any lingering auth processes
    _kill_previous_login_processes()
    await asyncio.sleep(1)

    # Locate the OAuth driver next to this script or in the scripts directory
    if oauth_script is None:
        candidates = [
            # Bundled with the installed package (site-packages/aquarco_supervisor/scripts/)
            Path(__file__).parent.parent / "scripts" / "claude-auth-oauth.py",
            # Dev source checkout (supervisor/python/src/aquarco_supervisor/../../scripts/ → supervisor/scripts/)
            Path(__file__).parent.parent.parent.parent.parent / "scripts" / "claude-auth-oauth.py",
            # Explicit fallback for legacy/non-venv installs
            Path("/home/agent/aquarco/supervisor/scripts/claude-auth-oauth.py"),
            # Stable install location (written by provision.sh on first/re-provision)
            Path("/var/lib/aquarco/scripts/claude-auth-oauth.py"),
        ]
        oauth_script = next((p for p in candidates if p.exists()), None)

        # Last resort: find the script in any checked-out git worktree.
        # The supervisor checks out the repo into worktrees for pipeline execution,
        # so the script is usually present there even when the package was installed
        # before it was bundled.
        if oauth_script is None:
            worktree_root = Path("/var/lib/aquarco/worktrees")
            if worktree_root.is_dir():
                worktree_matches = sorted(
                    worktree_root.glob("*/supervisor/scripts/claude-auth-oauth.py"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if worktree_matches:
                    oauth_script = worktree_matches[0]

    if oauth_script and oauth_script.exists():
        # Validate that the oauth script is within a trusted directory before
        # executing it, to prevent an operator-supplied --oauth-script from
        # running arbitrary files on disk.
        _TRUSTED_SCRIPT_ROOTS = [
            Path(__file__).parent.parent / "scripts",
            Path(__file__).parent.parent.parent.parent.parent / "scripts",
            Path("/home/agent/aquarco/supervisor/scripts"),
            Path("/var/lib/aquarco/scripts"),
        ]
        # Also trust the oauth script inside any checked-out worktree, but
        # only at the expected subpath to prevent a malicious repo from
        # placing an arbitrary file that passes the trust check.
        _wt_root = Path("/var/lib/aquarco/worktrees")
        if _wt_root.is_dir():
            for wt in _wt_root.iterdir():
                if wt.is_dir():
                    _TRUSTED_SCRIPT_ROOTS.append(wt / "supervisor" / "scripts")
        resolved = oauth_script.resolve()
        if not any(
            resolved.is_relative_to(root.resolve())
            for root in _TRUSTED_SCRIPT_ROOTS
            if root.exists()
        ):
            log.error(
                "oauth_script_untrusted_path",
                script=str(oauth_script),
                trusted_roots=[str(r) for r in _TRUSTED_SCRIPT_ROOTS],
            )
            (ipc_dir / "login-response").write_text(
                json.dumps({"error": "oauth driver script is outside trusted directories"})
            )
            return

        # Launch detached — fire and forget
        proc = await asyncio.create_subprocess_exec(
            "python3", str(resolved), str(ipc_dir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        log.info("oauth_driver_started", pid=proc.pid, script=str(resolved))
    else:
        log.warning("oauth_script_not_found", searched=str(oauth_script))
        (ipc_dir / "login-response").write_text(
            json.dumps({"error": "oauth driver script not found"})
        )


async def _handle_status(ipc_dir: Path) -> None:
    """Consume status-request: try `claude auth status --json`, fall back to credential file."""
    request_file = ipc_dir / "status-request"
    if not request_file.exists():
        return

    log.info("status_request_received")
    request_file.unlink(missing_ok=True)
    (ipc_dir / "status-response").unlink(missing_ok=True)

    # Try the CLI first
    rc, stdout, _ = await _run_command(
        "claude", "auth", "status", "--json",
        timeout=CLAUDE_AUTH_STATUS_TIMEOUT,
    )

    status_json: str | None = None
    if rc == 0 and stdout.strip():
        try:
            json.loads(stdout)  # validate it is real JSON
            status_json = stdout.strip()
        except json.JSONDecodeError:
            pass

    # Fall back to reading credentials file directly
    if status_json is None:
        status_json = _read_credentials_file()

    (ipc_dir / "status-response").write_text(status_json)
    log.info("status_response_written", logged_in=_extract_logged_in(status_json))


def _read_credentials_file() -> str:
    """Read ~/.claude/.credentials.json and return a minimal status JSON string."""
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if cred_path.exists():
        try:
            cred = json.loads(cred_path.read_text())
            oauth = cred.get("claudeAiOauth", {})
            if oauth.get("accessToken"):
                return json.dumps({"loggedIn": True, "authMethod": "oauth"})
        except (json.JSONDecodeError, OSError):
            pass
    return json.dumps({"loggedIn": False})


def _extract_logged_in(status_json: str) -> bool:
    """Best-effort extraction of loggedIn field for log context."""
    try:
        return bool(json.loads(status_json).get("loggedIn", False))
    except (json.JSONDecodeError, AttributeError):
        return False


async def _handle_logout(ipc_dir: Path) -> None:
    """Consume logout-request and invoke `claude auth logout`."""
    request_file = ipc_dir / "logout-request"
    if not request_file.exists():
        return

    log.info("logout_request_received")
    request_file.unlink(missing_ok=True)
    (ipc_dir / "logout-response").unlink(missing_ok=True)

    rc, _, stderr = await _run_command(
        "claude", "auth", "logout",
        timeout=CLAUDE_LOGOUT_TIMEOUT,
    )

    if rc == 0:
        (ipc_dir / "logout-response").write_text(json.dumps({"success": True}))
        log.info("logout_completed")
    else:
        # Do not include raw stderr in the response or logs; it may contain
        # credential fragments or token values from the Claude CLI.
        safe_error = f"claude auth logout exited with code {rc}"
        (ipc_dir / "logout-response").write_text(
            json.dumps({"success": False, "error": safe_error})
        )
        log.warning("logout_failed", returncode=rc)


# ── Main poll loop ────────────────────────────────────────────────────────────


async def _watch_loop(
    ipc_dir: Path,
    poll_interval: int,
    oauth_script: Path | None,
    stop_event: asyncio.Event,
) -> None:
    # Create IPC directory restricted to owner only (mode 0o700).
    # This prevents other local users from reading or injecting auth command files.
    ipc_dir.mkdir(parents=True, exist_ok=True)
    ipc_dir.chmod(stat.S_IRWXU)
    log.info(
        "auth_helper_watching",
        ipc_dir=str(ipc_dir),
        poll_interval=poll_interval,
    )

    while not stop_event.is_set():
        try:
            await _handle_login(ipc_dir, oauth_script)
            await _handle_status(ipc_dir)
            await _handle_logout(ipc_dir)
        except Exception:
            log.exception("poll_loop_error")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass


# ── Typer command ─────────────────────────────────────────────────────────────

_cli = typer.Typer(add_completion=False, invoke_without_command=True)


@_cli.command()
def auth_watch(
    ipc_dir: str = typer.Option(
        DEFAULT_IPC_DIR,
        "--ipc-dir",
        help="Directory to watch for auth command files",
        envvar="CLAUDE_IPC_DIR",
    ),
    poll_interval: int = typer.Option(
        DEFAULT_POLL_INTERVAL,
        "--poll-interval",
        help="Seconds between directory polls",
    ),
    oauth_script: str = typer.Option(
        "",
        "--oauth-script",
        help="Path to claude-auth-oauth.py (auto-detected when empty)",
    ),
    log_level: str = typer.Option(
        "info",
        "--log-level",
        help="Logging level (debug, info, warning, error)",
    ),
) -> None:
    """Watch IPC directory for Claude auth command files and handle them."""
    setup_logging(level=log_level)

    ipc_path = Path(ipc_dir)
    oauth_path: Path | None = Path(oauth_script) if oauth_script else None

    stop_event = asyncio.Event()

    def _handle_signal(sig: int, _frame: object) -> None:
        log.info("signal_received", signal=sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    asyncio.run(_watch_loop(ipc_path, poll_interval, oauth_path, stop_event))
    log.info("auth_helper_stopped")

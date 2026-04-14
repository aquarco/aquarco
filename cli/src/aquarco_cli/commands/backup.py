"""aquarco backup — back up the database and credentials."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import typer

from aquarco_cli.console import print_error, print_info, print_success, print_warning
from aquarco_cli.vagrant import VagrantError, VagrantHelper

app = typer.Typer(context_settings={"help_option_names": ["-h", "--help"]})

# Default backup root on the host
DEFAULT_BACKUP_ROOT = Path.home() / ".aquarco" / "backups"

# Docker Compose working dir inside the VM
COMPOSE_DIR = "/home/agent/aquarco/docker"

# Credential file paths inside the VM (as agent user).
# github-token: raw OAuth token written by the API's device-flow handler to the
#   agent-ssh shared volume (compose: ${AGENT_SSH_DIR:-/home/agent/.ssh}:/agent-ssh).
#   The GraphQL API reads it from /agent-ssh/github-token (= /home/agent/.ssh/github-token).
# credentials.json: Claude CLI OAuth tokens used by the claude-auth-helper IPC flow.
_CRED_FILES = {
    "github-token": "/home/agent/.ssh/github-token",
    "credentials.json": "/home/agent/.claude/.credentials.json",
}


def _backup_db(vagrant: VagrantHelper, dest: Path) -> bool:
    """Stream pg_dump from the postgres container to a file on the host."""
    try:
        result = vagrant.ssh(
            f"sudo -u agent HOME=/home/agent bash -c "
            f"'cd {COMPOSE_DIR} && docker compose exec -T postgres pg_dump -U aquarco aquarco'",
            stream=False,
        )
        out = dest / "aquarco.sql"
        out.write_text(result.stdout)
        out.chmod(0o600)
        print_success(f"Database → {out}")
        return True
    except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
        print_error(f"Database backup failed: {exc}")
        return False


def _backup_credentials(vagrant: VagrantHelper, dest: Path) -> bool:
    """Copy credential files from the VM to the host backup directory."""
    found = False
    for filename, vm_path in _CRED_FILES.items():
        try:
            result = vagrant.ssh(
                f"sudo -u agent HOME=/home/agent bash -c 'test -f {vm_path} && cat {vm_path} || true'",
                stream=False,
            )
            content = result.stdout.strip()
            if not content:
                print_warning(f"  {filename}: not found in VM, skipping")
                continue
            out = dest / filename
            out.write_text(content)
            out.chmod(0o600)
            print_success(f"  {filename} → {out}")
            found = True
        except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
            print_warning(f"  {filename}: {exc}")
    return found


def perform_backup(vagrant: VagrantHelper, output: Path = DEFAULT_BACKUP_ROOT) -> None:
    """Run a full backup (db + creds). Prints results; raises SystemExit on failure."""
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = output / timestamp
    dest.mkdir(parents=True, exist_ok=True)
    dest.chmod(0o700)

    print_info("Backing up database...")
    ok = _backup_db(vagrant, dest)
    print_info("Backing up credentials...")
    ok = _backup_credentials(vagrant, dest) and ok

    if not ok:
        print_warning(f"Backup completed with errors. Partial files in {dest}")
        raise typer.Exit(code=1)

    print_success(f"Backup complete: {dest}")


@app.callback(invoke_without_command=True)
def backup(
    db: bool = typer.Option(True, "--db/--no-db", help="Back up the PostgreSQL database."),
    creds: bool = typer.Option(True, "--creds/--no-creds", help="Back up GitHub and Claude credentials."),
    output: Path = typer.Option(
        DEFAULT_BACKUP_ROOT,
        "--output", "-o",
        help="Directory on the host where the backup is stored. "
             f"Default: {DEFAULT_BACKUP_ROOT}",
    ),
    dev: bool = typer.Option(
        False, "--dev",
        help="Target the development VM (aquarco-dev) instead of the production VM.",
    ),
) -> None:
    """Back up the database and credentials to ~/.aquarco/backups/ on the host."""
    if dev:
        import os
        os.environ.setdefault("AQUARCO_VM_NAME", "aquarco-dev")
    vagrant = VagrantHelper()

    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco init' first.")
        raise typer.Exit(code=1)

    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = output / timestamp
    dest.mkdir(parents=True, exist_ok=True)
    dest.chmod(0o700)

    ok = True

    if db:
        print_info("Backing up database...")
        ok = _backup_db(vagrant, dest) and ok

    if creds:
        print_info("Backing up credentials...")
        ok = _backup_credentials(vagrant, dest) and ok

    if not ok:
        print_warning(f"Backup completed with errors. Partial files in {dest}")
        raise typer.Exit(code=1)

    print_success(f"Backup complete: {dest}")

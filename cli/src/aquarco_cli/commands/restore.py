"""aquarco restore — restore database and credentials from a backup."""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from aquarco_cli.console import print_error, print_info, print_success, print_warning
from aquarco_cli.vagrant import COMPOSE_DIR, LOAD_SECRETS, VagrantError, VagrantHelper, get_compose_prefix

app = typer.Typer(context_settings={"help_option_names": ["-h", "--help"]})

# Default backup root on the host (mirrors backup.py)
DEFAULT_BACKUP_ROOT = Path.home() / ".aquarco" / "backups"

# Credential file paths inside the VM.
# Must mirror backup.py exactly so backup ↔ restore filenames align.
_CRED_FILES = {
    "github-token": "/home/agent/.ssh/github-token",
    "credentials.json": "/home/agent/.claude/.credentials.json",
}


def latest_backup(root: Path) -> Path | None:
    """Return the most recent timestamped subdirectory under *root*, or None."""
    if not root.exists():
        return None
    dirs = sorted(d for d in root.iterdir() if d.is_dir())
    return dirs[-1] if dirs else None


def restore_db(vagrant: VagrantHelper, src: Path) -> bool:
    """Pipe aquarco.sql from the backup into the postgres container."""
    sql_file = src / "aquarco.sql"
    if not sql_file.exists():
        print_warning("  aquarco.sql not found in backup, skipping database restore")
        return False
    try:
        sql = sql_file.read_text()
        vagrant.ssh(
            f"sudo -u agent HOME=/home/agent bash -c "
            f"'{LOAD_SECRETS}; cd {COMPOSE_DIR} && docker compose exec -T postgres psql -U aquarco aquarco'",
            input=sql,
        )
        print_success(f"Database ← {sql_file}")
    except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
        print_error(f"Database restore failed: {exc}")
        return False

    # Reset all repos to 'pending' so the clone worker re-verifies them on this VM.
    # Safe even if dirs already exist: clone_worker skips repos with a valid .git dir.
    try:
        vagrant.ssh(
            f"sudo -u agent HOME=/home/agent bash -c "
            f"'{LOAD_SECRETS}; cd {COMPOSE_DIR} && docker compose exec -T postgres psql -U aquarco aquarco "
            f"-c \"UPDATE aquarco.repositories SET clone_status = '\\''pending'\\'',"
            f" error_message = NULL WHERE clone_status IN ('\\''ready'\\'',"
            f" '\\''error'\\'');\"'",
        )
        print_success("  Reset repository clone statuses to pending")
    except (VagrantError, subprocess.CalledProcessError, OSError):
        # Non-fatal: clone worker will catch stale statuses on first run anyway
        print_warning("  Could not reset clone statuses (clone worker will recover)")

    return True


def run_migrations(vagrant: VagrantHelper) -> bool:
    """Run yoyo migrations after restore to bring the schema up to date."""
    dc = get_compose_prefix(vagrant)
    try:
        vagrant.ssh(
            f"sudo -u agent HOME=/home/agent bash -c "
            f"'{LOAD_SECRETS}; cd {COMPOSE_DIR} && {dc} run --rm migrations'",
            stream=True,
        )
        print_success("Migrations applied")
        return True
    except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
        print_error(f"Migrations failed: {exc}")
        return False


def restore_credentials(vagrant: VagrantHelper, src: Path) -> bool:
    """Write credential files from the backup directory back into the VM."""
    ok = True
    for filename, vm_path in _CRED_FILES.items():
        host_file = src / filename
        if not host_file.exists():
            print_warning(f"  {filename}: not found in backup, skipping")
            continue
        try:
            content = host_file.read_text()
            vagrant.ssh(
                f"sudo -u agent HOME=/home/agent bash -c "
                f"'mkdir -p $(dirname {vm_path}) && cat > {vm_path} && chmod 600 {vm_path}'",
                input=content,
            )
            print_success(f"  {filename} → {vm_path}")
        except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
            print_warning(f"  {filename}: {exc}")
            ok = False
    return ok


@app.callback(invoke_without_command=True)
def restore(
    from_file: Path = typer.Option(
        None,
        "--from-file", "-f",
        help="Path to a specific backup directory. Defaults to the latest backup.",
        show_default=False,
    ),
    db: bool = typer.Option(True, "--db/--no-db", help="Restore the PostgreSQL database."),
    creds: bool = typer.Option(True, "--creds/--no-creds", help="Restore GitHub and Claude credentials."),
) -> None:
    """Restore database and credentials from a backup.

    Without --from-file the latest backup under ~/.aquarco/backups/ is used.
    """
    vagrant = VagrantHelper()

    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco init' first.")
        raise typer.Exit(code=1)

    if from_file is not None:
        src = from_file
        if not src.is_dir():
            print_error(f"Backup directory not found: {src}")
            raise typer.Exit(code=1)
    else:
        src = latest_backup(DEFAULT_BACKUP_ROOT)
        if src is None:
            print_error(
                f"No backups found in {DEFAULT_BACKUP_ROOT}. "
                "Run 'aquarco backup' first or specify --from-file."
            )
            raise typer.Exit(code=1)

    print_info(f"Restoring from {src}")
    ok = True

    if db:
        print_info("Restoring database...")
        ok = restore_db(vagrant, src) and ok
        if ok:
            print_info("Running migrations...")
            ok = run_migrations(vagrant) and ok

    if creds:
        print_info("Restoring credentials...")
        ok = restore_credentials(vagrant, src) and ok

    if not ok:
        print_warning("Restore completed with errors.")
        raise typer.Exit(code=1)

    print_success("Restore complete.")
    print_warning(
        "Claude OAuth tokens expire. If the supervisor agents fail to authenticate, "
        "run: aquarco auth claude"
    )

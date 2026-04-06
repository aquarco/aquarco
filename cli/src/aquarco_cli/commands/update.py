"""aquarco update — update the VM to the latest version."""

from __future__ import annotations

import shlex
import subprocess

import httpx
import typer
from rich.prompt import Prompt

from aquarco_cli._build import BUILD_TYPE
from aquarco_cli.console import console, handle_api_error, print_error, print_info, print_success, print_warning
from aquarco_cli.health import print_health_table
from aquarco_cli.graphql_client import (
    MUTATION_SET_DRAIN_MODE,
    QUERY_DRAIN_STATUS,
    GraphQLClient,
)
from aquarco_cli.vagrant import VagrantError, VagrantHelper

app = typer.Typer(context_settings={"help_option_names": ["-h", "--help"]})

STEPS = [
    ("Update OS packages", "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq"),
    ("Pull latest Docker images", "cd /home/agent/aquarco/docker && sudo docker compose pull"),
    ("Run database migrations", "cd /home/agent/aquarco/docker && sudo docker compose run --rm migrations"),
    ("Restart Docker services", "cd /home/agent/aquarco/docker && sudo docker compose up -d --build"),
    ("Fix venv permissions", "sudo chown -R agent:agent /home/agent/.venv && sudo chmod -R u+w /home/agent/.venv"),
    ("Upgrade supervisor package", "sudo -u agent /home/agent/.venv/bin/pip install -e /home/agent/aquarco/supervisor/python/"),
    ("Lock venv", "sudo chmod -R a-w /home/agent/.venv/lib/"),
    ("Restart supervisor service", "sudo systemctl restart aquarco-supervisor-python"),
]


def _backup_credentials(vagrant: VagrantHelper) -> str | None:
    """Run backup-credentials.sh inside the VM and return the backup dir path.

    Returns ``None`` if the backup script fails (e.g. no credentials found).
    """
    try:
        result = vagrant.ssh(
            "bash /home/agent/aquarco/vagrant/scripts/backup-credentials.sh",
            stream=False,
        )
        # The script prints the backup dir on the last line of stdout
        backup_dir = result.stdout.strip().splitlines()[-1].strip()
        print_success(f"Credentials backed up to {backup_dir}")
        return backup_dir
    except (VagrantError, subprocess.CalledProcessError, OSError, IndexError) as exc:
        print_warning(f"Credential backup failed: {exc}. Continuing without backup.")
        return None


def _run_rollback(vagrant: VagrantHelper, backup_dir: str) -> None:
    """Invoke the rollback script inside the VM."""
    print_warning("Rolling back update...")
    try:
        vagrant.ssh(
            f"bash /home/agent/aquarco/vagrant/scripts/rollback.sh --backup-dir {shlex.quote(backup_dir)}",
            stream=True,
        )
        print_info("Rollback completed.")
    except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
        print_error(f"Rollback failed: {exc}")


def _run_update_steps(
    vagrant: VagrantHelper,
    steps: list[tuple[str, str]],
    skip_provision: bool,
    backup_dir: str | None = None,
) -> None:
    """Execute SSH update steps, re-provision, and run health checks.

    On any step failure the update is aborted immediately (hard-fail).
    If a backup directory is available, rollback is invoked before exiting.
    """
    for name, cmd in steps:
        print_info(f"{name}...")
        try:
            vagrant.ssh(cmd, stream=True)
        except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
            print_error(f"Step failed: {name} — {exc}")
            if backup_dir:
                _run_rollback(vagrant, backup_dir)
            raise typer.Exit(code=1) from exc

    # Re-provision
    if not skip_provision:
        print_info("Re-provisioning VM...")
        try:
            vagrant.provision()
        except (VagrantError, subprocess.CalledProcessError, OSError) as exc:
            print_error(f"Provisioning failed: {exc}")
            if backup_dir:
                _run_rollback(vagrant, backup_dir)
            raise typer.Exit(code=1) from exc

    # Health checks
    print_info("Checking stack health...")
    all_healthy = print_health_table()
    if all_healthy:
        print_success("Update completed successfully!")
    else:
        print_warning("Update completed but some services are unhealthy.")


def _query_drain_status(client: GraphQLClient) -> dict | None:
    """Query drain status from the API. Returns None on connection failure."""
    try:
        data = client.execute(QUERY_DRAIN_STATUS)
        return data["drainStatus"]
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return None
    except (KeyError, TypeError) as exc:
        print_warning(f"Unexpected drain status response: {exc}. Proceeding without drain check.")
        return None
    except Exception as exc:
        print_warning(f"Failed to query drain status: {type(exc).__name__}: {exc}. Proceeding without drain check.")
        return None


@app.callback(invoke_without_command=True)
def update(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned steps without executing"),
    skip_migrations: bool = typer.Option(False, "--skip-migrations", help="Skip database migrations"),
    skip_provision: bool = typer.Option(False, "--skip-provision", help="Skip VM re-provisioning"),
) -> None:
    """Update the VM to the latest version including Docker images."""
    # ── Production guard ─────────────────────────────────────────────────────
    if BUILD_TYPE == "production":
        print_error(
            "aquarco update is not available for public Homebrew installs. "
            "To update, reinstall via Homebrew: brew upgrade aquarco"
        )
        raise typer.Exit(code=1)

    vagrant = VagrantHelper()

    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco init' first.")
        raise typer.Exit(code=1)

    steps = list(STEPS)
    if skip_migrations:
        steps = [(n, c) for n, c in steps if "migrations" not in c]

    if dry_run:
        console.print("[bold]Dry run — the following steps would be executed:[/bold]\n")
        for i, (name, cmd) in enumerate(steps, 1):
            console.print(f"  {i}. {name}")
            console.print(f"     [dim]{cmd}[/dim]")
        if not skip_provision:
            console.print(f"  {len(steps) + 1}. Re-provision VM")
            console.print("     [dim]vagrant provision[/dim]")
        console.print(f"\n  {len(steps) + (0 if skip_provision else 1) + 1}. Health checks")
        return

    # Check drain/active status before proceeding
    client = GraphQLClient()
    drain = _query_drain_status(client)

    if drain is not None:
        if drain["enabled"]:
            # Drain mode is already active
            if drain["activeTasks"] == 0 and drain["activeAgents"] == 0:
                # All idle — clear drain and proceed with update
                print_info("Drain mode was pending and all work is idle. Proceeding with update...")
                try:
                    client.execute(MUTATION_SET_DRAIN_MODE, {"enabled": False})
                except Exception as exc:
                    print_warning(f"Could not clear drain flag: {type(exc).__name__}: {exc}. Proceeding with update.")
                backup_dir = _backup_credentials(vagrant)
                _run_update_steps(vagrant, steps, skip_provision, backup_dir=backup_dir)
                return

            # Work still in progress during drain
            console.print(
                f"\n[bold yellow]A planned update is pending.[/bold yellow] "
                f"{drain['activeAgents']} agents working on {drain['activeTasks']} tasks.\n"
            )
            choice = Prompt.ask(
                "Choose an action",
                choices=["keep", "now", "cancel"],
                default="keep",
            )
            if choice == "keep":
                print_info("Keeping planned update. The supervisor will auto-restart when idle.")
                return
            elif choice == "now":
                print_warning("Forcing immediate restart...")
                try:
                    client.execute(MUTATION_SET_DRAIN_MODE, {"enabled": False})
                except Exception as exc:
                    print_warning(f"Could not clear drain flag ({type(exc).__name__}: {exc}) — proceeding with restart anyway.")
                backup_dir = _backup_credentials(vagrant)
                _run_update_steps(vagrant, steps, skip_provision, backup_dir=backup_dir)
                return
            else:  # cancel
                print_info("Cancelling planned update. Resuming normal operation.")
                try:
                    client.execute(MUTATION_SET_DRAIN_MODE, {"enabled": False})
                    print_success("Drain mode disabled. Normal operation resumed.")
                except Exception as exc:
                    handle_api_error(exc)
                    raise typer.Exit(code=1) from exc
                return

        elif drain["activeTasks"] > 0 or drain["activeAgents"] > 0:
            # Active work but no drain mode yet
            console.print(
                f"\n[bold yellow]Warning:[/bold yellow] "
                f"{drain['activeAgents']} agents working on {drain['activeTasks']} tasks.\n"
            )
            choice = Prompt.ask(
                "Restart now, abort, or plan update when idle?",
                choices=["yes", "no", "plan"],
                default="no",
            )
            if choice == "no":
                print_info("Update aborted.")
                return
            elif choice == "plan":
                print_info("Setting drain mode. Supervisor will stop picking up new work and auto-restart when idle.")
                try:
                    client.execute(MUTATION_SET_DRAIN_MODE, {"enabled": True})
                except Exception as exc:
                    handle_api_error(exc)
                    raise typer.Exit(code=1) from exc
                print_success("Drain mode enabled. Run 'aquarco update' again to check status.")
                return
            # choice == "yes" → fall through to immediate update

    backup_dir = _backup_credentials(vagrant)
    _run_update_steps(vagrant, steps, skip_provision, backup_dir=backup_dir)

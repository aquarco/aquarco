"""aquarco update — update the VM to the latest version."""

from __future__ import annotations

import subprocess

import typer

from aquarco_cli.config import get_config
from aquarco_cli.console import console, print_error, print_info, print_success, print_warning
from aquarco_cli.health import print_health_table
from aquarco_cli.vagrant import VagrantHelper

app = typer.Typer()

STEPS = [
    ("Pull latest source code", "git pull --ff-only"),
    ("Pull latest Docker images", "cd /home/agent/aquarco/docker && sudo docker compose pull"),
    ("Run database migrations", "cd /home/agent/aquarco/docker && sudo docker compose run --rm migrations"),
    ("Restart Docker services", "cd /home/agent/aquarco/docker && sudo docker compose up -d --build"),
    ("Upgrade supervisor package", "cd /home/agent/aquarco/supervisor/python && sudo pip install -e '.[dev]'"),
    ("Restart supervisor service", "sudo systemctl restart aquarco-supervisor-python"),
]


@app.callback(invoke_without_command=True)
def update(
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned steps without executing"),
    skip_migrations: bool = typer.Option(False, "--skip-migrations", help="Skip database migrations"),
    skip_provision: bool = typer.Option(False, "--skip-provision", help="Skip VM re-provisioning"),
) -> None:
    """Update the VM to the latest version including Docker images."""
    vagrant = VagrantHelper()

    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco install' first.")
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

    # Step 0: git pull on host (run from repo root, not CWD)
    repo_root = get_config().resolve_vagrant_dir().parent
    print_info("Pulling latest source code on host...")
    try:
        subprocess.run(
            ["git", "pull", "--ff-only"],
            check=True, capture_output=True, text=True,
            cwd=str(repo_root),
        )
    except subprocess.CalledProcessError as exc:
        print_warning(f"git pull failed: {exc.stderr.strip()}")

    # SSH steps
    for name, cmd in steps:
        if cmd.startswith("git"):
            continue  # already done on host
        print_info(f"{name}...")
        try:
            vagrant.ssh(cmd, stream=True)
        except Exception as exc:
            print_error(f"Step failed: {name} — {exc}")
            print_warning("Continuing with remaining steps...")

    # Re-provision
    if not skip_provision:
        print_info("Re-provisioning VM...")
        try:
            vagrant.provision()
        except Exception as exc:
            print_warning(f"Provisioning failed: {exc}")

    # Health checks
    print_info("Checking stack health...")
    all_healthy = print_health_table()
    if all_healthy:
        print_success("Update completed successfully!")
    else:
        print_warning("Update completed but some services are unhealthy.")

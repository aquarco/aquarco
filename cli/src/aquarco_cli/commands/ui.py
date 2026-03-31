"""aquarco ui — manage the web UI."""

from __future__ import annotations

import webbrowser

import typer

from aquarco_cli.console import print_error, print_info, print_success
from aquarco_cli.vagrant import VagrantHelper

app = typer.Typer(help="Start or stop the Aquarco web UI.")

COMPOSE_DIR = "/home/agent/aquarco/docker"
UP_CMD = f"cd {COMPOSE_DIR} && sudo docker compose up -d web api postgres caddy"
STOP_CMD = f"cd {COMPOSE_DIR} && sudo docker compose stop web api"


@app.callback(invoke_without_command=True)
def ui(
    ctx: typer.Context,
    open_browser: bool = typer.Option(False, "--open", "-o", help="Open browser after starting"),
) -> None:
    """Start the web UI (web + API + Postgres + Caddy)."""
    if ctx.invoked_subcommand is not None:
        return

    vagrant = VagrantHelper()
    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco install' first.")
        raise typer.Exit(code=1)

    print_info("Starting UI services...")
    try:
        vagrant.ssh(UP_CMD, stream=True)
    except Exception as exc:
        print_error(f"Failed to start UI: {exc}")
        raise typer.Exit(code=1) from exc

    print_success("Web UI is running at http://localhost:8080")

    if open_browser:
        webbrowser.open("http://localhost:8080")


@app.command()
def stop() -> None:
    """Stop the web UI services."""
    vagrant = VagrantHelper()
    if not vagrant.is_running():
        print_error("VM is not running.")
        raise typer.Exit(code=1)

    print_info("Stopping UI services...")
    try:
        vagrant.ssh(STOP_CMD, stream=True)
    except Exception as exc:
        print_error(f"Failed to stop UI: {exc}")
        raise typer.Exit(code=1) from exc

    print_success("UI services stopped.")

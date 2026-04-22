"""aquarco ui — manage the web UI."""

from __future__ import annotations

import subprocess
import webbrowser

import typer

from aquarco_cli.config import get_config
from aquarco_cli.console import print_error, print_info, print_success, print_warning
from aquarco_cli.vagrant import COMPOSE_DIR, VagrantError, VagrantHelper, get_compose_prefix

app = typer.Typer(
    help="Start or stop the Aquarco web UI.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _ensure_running() -> VagrantHelper:
    """Return a VagrantHelper or exit if VM is not running."""
    vagrant = VagrantHelper()
    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco init' first.")
        raise typer.Exit(code=1)
    return vagrant


def _start_and_open(
    vagrant: VagrantHelper,
    services: str,
    url: str,
    no_open: bool,
) -> None:
    """Start compose services, print URL, and optionally open browser."""
    dc = get_compose_prefix(vagrant)
    cmd = f"sudo -u agent HOME=/home/agent bash -c 'cd {COMPOSE_DIR} && {dc} up -d {services}'"
    try:
        vagrant.ssh(cmd, stream=True)
    except Exception as exc:
        print_error(f"Failed to start services: {exc}")
        raise typer.Exit(code=1) from exc

    print_success(f"Service is running at {url}")
    if not no_open:
        webbrowser.open(url)


@app.callback(invoke_without_command=True)
def ui(
    ctx: typer.Context,
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the browser after starting"),
) -> None:
    """Start the web UI (web + API + Postgres + Caddy).

    Without a subcommand, starts the full web stack and opens the browser.
    """
    if ctx.invoked_subcommand is not None:
        return

    # Default behavior: start web
    port = get_config().port
    vagrant = _ensure_running()
    _start_and_open(
        vagrant,
        "web api postgres caddy",
        f"http://localhost:{port}",
        no_open,
    )


@app.command()
def web(
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the browser"),
) -> None:
    """Start the web UI and open it in the browser."""
    port = get_config().port
    vagrant = _ensure_running()
    _start_and_open(
        vagrant,
        "web api postgres caddy",
        f"http://localhost:{port}",
        no_open,
    )


@app.command()
def db(
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the browser"),
) -> None:
    """Start Adminer (database UI) and open it in the browser.

    After starting the service, the Adminer connection credentials
    (server, database, username, password) are printed to the terminal so the
    user can paste them into the Adminer login form. The password is printed
    in plaintext — this command is intended for local developer VMs only.
    """
    port = get_config().port
    vagrant = _ensure_running()
    _start_and_open(
        vagrant,
        "adminer postgres",
        f"http://localhost:{port}/adminer/",
        no_open,
    )

    # Read the DB password from the VM secrets file and print Adminer credentials.
    # The password is printed in plaintext to ease the local developer workflow
    # (there is no other way for the user to learn it without SSH'ing into the VM).
    # This is only meaningful on a local developer VM; be mindful of terminal
    # recordings and screen shares when using this command.
    try:
        result = vagrant.ssh(
            "sudo grep '^POSTGRES_PASSWORD=' /etc/aquarco/docker-secrets.env"
        )
        password = (result.stdout or "").strip().removeprefix("POSTGRES_PASSWORD=")
    except (VagrantError, subprocess.CalledProcessError, OSError):
        password = None

    print_info("Adminer credentials:")
    print_info("  Server:   postgres")
    print_info("  Database: aquarco")
    print_info("  Username: aquarco")
    if password:
        print_info(f"  Password: {password}")
    else:
        print_warning("  Password: (could not read — check /etc/aquarco/docker-secrets.env on VM)")


@app.command()
def api(
    no_open: bool = typer.Option(False, "--no-open", help="Do not open the browser"),
) -> None:
    """Start the GraphQL API and open the playground."""
    port = get_config().port
    vagrant = _ensure_running()
    _start_and_open(
        vagrant,
        "api postgres",
        f"http://localhost:{port}/api/graphql",
        no_open,
    )


@app.command()
def stop() -> None:
    """Stop UI services (web, adminer) but keep the API running."""
    vagrant = _ensure_running()

    print_info("Stopping UI services...")
    dc = get_compose_prefix(vagrant)
    cmd = f"sudo -u agent HOME=/home/agent bash -c 'cd {COMPOSE_DIR} && {dc} stop web adminer'"
    try:
        vagrant.ssh(cmd, stream=True)
    except Exception as exc:
        print_error(f"Failed to stop UI: {exc}")
        raise typer.Exit(code=1) from exc

    print_success("UI services stopped.")

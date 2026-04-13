"""aquarco start/stop/destroy — VM lifecycle commands."""

from __future__ import annotations

import typer

from aquarco_cli.console import print_error, print_info, print_success
from aquarco_cli.vagrant import VagrantError, VagrantHelper

start_app = typer.Typer(
    help="Start the Aquarco VM (vagrant up).",
    context_settings={"help_option_names": ["-h", "--help"]},
)

stop_app = typer.Typer(
    help="Stop the Aquarco VM (vagrant halt).",
    context_settings={"help_option_names": ["-h", "--help"]},
)

destroy_app = typer.Typer(
    help="Destroy the Aquarco VM (vagrant destroy).",
    context_settings={"help_option_names": ["-h", "--help"]},
)


@start_app.callback(invoke_without_command=True)
def start() -> None:
    """Start the Aquarco VM."""
    vagrant = VagrantHelper()
    if vagrant.is_running():
        print_info("VM is already running.")
        return
    print_info("Starting VM...")
    try:
        vagrant.up()
    except VagrantError as exc:
        print_error(f"Failed to start VM: {exc}")
        raise typer.Exit(code=1) from exc
    print_success("VM is running.")


@stop_app.callback(invoke_without_command=True)
def stop() -> None:
    """Stop the Aquarco VM."""
    vagrant = VagrantHelper()
    if not vagrant.is_running():
        print_info("VM is already stopped.")
        return
    print_info("Stopping VM...")
    try:
        vagrant.halt()
    except VagrantError as exc:
        print_error(f"Failed to stop VM: {exc}")
        raise typer.Exit(code=1) from exc
    print_success("VM stopped.")


@destroy_app.callback(invoke_without_command=True)
def destroy(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Destroy the Aquarco VM. This is irreversible."""
    if not yes:
        typer.confirm("This will permanently destroy the VM. Continue?", abort=True)
    print_info("Destroying VM...")
    try:
        VagrantHelper().destroy()
    except VagrantError as exc:
        print_error(f"Failed to destroy VM: {exc}")
        raise typer.Exit(code=1) from exc
    print_success("VM destroyed.")

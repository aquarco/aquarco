"""aquarco config — sync agent and pipeline definitions between config files and the database."""

from __future__ import annotations

import os

import typer

from aquarco_cli.console import print_error, print_info
from aquarco_cli.vagrant import VagrantError, VagrantHelper

app = typer.Typer(
    help="Sync agent and pipeline definitions between config files and the database.",
    context_settings={"help_option_names": ["-h", "--help"]},
)

_SUPERVISOR_CONFIG = "/home/agent/aquarco/supervisor/config/supervisor.yaml"
_SUPERVISOR_CMD = "sudo -u agent HOME=/home/agent bash -c 'aquarco-supervisor config {subcommand} --config {config}'"

_DEV_VM_NAME = "aquarco-dev"


def _run(subcommand: str, dev: bool) -> None:
    env_patch: dict[str, str] = {}
    if dev:
        env_patch["AQUARCO_VM_NAME"] = os.environ.get("AQUARCO_VM_NAME", _DEV_VM_NAME)
    vagrant = VagrantHelper(vm_name=env_patch.get("AQUARCO_VM_NAME", ""))
    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco init' first.")
        raise typer.Exit(code=1)
    cmd = _SUPERVISOR_CMD.format(subcommand=subcommand, config=_SUPERVISOR_CONFIG)
    try:
        vagrant.ssh(cmd, stream=True)
    except VagrantError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)


@app.command()
def update(
    dev: bool = typer.Option(False, "--dev", help="Target the development VM."),
) -> None:
    """Sync agent and pipeline definitions from config files into the database."""
    print_info("Syncing config files → database...")
    _run("update", dev)


@app.command()
def export(
    dev: bool = typer.Option(False, "--dev", help="Target the development VM."),
) -> None:
    """Export active agent and pipeline definitions from the database back to config files."""
    print_info("Exporting database → config files...")
    _run("export", dev)

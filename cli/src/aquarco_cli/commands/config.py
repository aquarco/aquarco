"""aquarco config — sync agent and pipeline definitions between config files and the database."""

from __future__ import annotations

import typer

from aquarco_cli.console import print_error, print_info
from aquarco_cli.vagrant import LOAD_SUPERVISOR_SECRETS, VagrantError, VagrantHelper

app = typer.Typer(
    help="Sync agent and pipeline definitions between config files and the database.",
    context_settings={"help_option_names": ["-h", "--help"]},
)

# Detect the supervisor config path at runtime on the VM:
# production installs use /etc/aquarco/supervisor.yaml;
# dev (synced-folder) installs keep it in the repo tree.
_DETECT_CONFIG = (
    "if [ -f /etc/aquarco/supervisor.yaml ]; then "
    "  SCFG=/etc/aquarco/supervisor.yaml; "
    "else "
    "  SCFG=/home/agent/aquarco/supervisor/config/supervisor.yaml; "
    "fi"
)
_SUPERVISOR_CMD = (
    "sudo -u agent HOME=/home/agent "
    f"bash -c '{LOAD_SUPERVISOR_SECRETS}; {_DETECT_CONFIG}; "
    '/home/agent/.venv/bin/aquarco-supervisor config {subcommand} --config "$SCFG"\''
)


def _run(subcommand: str) -> None:
    vagrant = VagrantHelper()
    if not vagrant.is_running():
        print_error("VM is not running. Start it with 'aquarco init' first.")
        raise typer.Exit(code=1)
    cmd = _SUPERVISOR_CMD.format(subcommand=subcommand)
    try:
        vagrant.ssh(cmd, stream=True)
    except VagrantError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1)


@app.command()
def update() -> None:
    """Sync agent and pipeline definitions from config files into the database."""
    print_info("Syncing config files → database...")
    _run("update")


@app.command()
def export() -> None:
    """Export active agent and pipeline definitions from the database back to config files."""
    print_info("Exporting database → config files...")
    _run("export")

"""Aquarco CLI — main entry point."""

from __future__ import annotations

import os
import subprocess

import typer

from aquarco_cli import __version__
from aquarco_cli._build import BUILD_TYPE
from aquarco_cli.commands import auth, backup, config, init, repos, restore, run, status, ui, update, vm

# Directory containing the installed ``aquarco_cli`` package. Used to anchor
# ``git`` lookups for the dev version so they always describe the aquarco
# checkout regardless of the user's current working directory.
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

app = typer.Typer(
    name="aquarco",
    help="Aquarco CLI — manage your Aquarco VM from the host.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _get_dev_version() -> str:
    """Return a development version string of the form ``local-dev <branch>@<hash>``.

    Calls ``git`` at runtime to resolve the current branch and short commit hash
    of the ``aquarco_cli`` package's own source tree. Anchoring to
    :data:`_PACKAGE_DIR` ensures the reported branch/hash always describes the
    installed aquarco checkout, not whatever repository the user happens to be
    standing in when they invoke ``aquarco --version``.

    Falls back to ``local-dev unknown`` if git is not available or the package
    directory is not inside a git repository (e.g. installed from a wheel).
    """
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=_PACKAGE_DIR,
        ).strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=_PACKAGE_DIR,
        ).strip()
        if not branch or not commit:
            return "local-dev unknown"
        return f"local-dev {branch}@{commit}"
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "local-dev unknown"


def _version_callback(value: bool) -> None:
    if value:
        if BUILD_TYPE == "development":
            typer.echo(f"aquarco {_get_dev_version()}")
        else:
            typer.echo(f"aquarco {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Aquarco CLI — manage your Aquarco VM from the host."""


# Register top-level commands
app.add_typer(init.app, name="init", help="Bootstrap the Aquarco VM.")
app.add_typer(backup.app, name="backup", help="Back up database and credentials.")
app.add_typer(restore.app, name="restore", help="Restore database and credentials from a backup.")
app.add_typer(update.app, name="update", help="Update VM to latest version.")
app.add_typer(auth.app, name="auth", help="Manage Claude and GitHub authentication.")
app.add_typer(repos.app, name="repos", help="Manage repositories.")
app.add_typer(run.app, name="run", help="Create a task for agent execution.")
app.add_typer(status.app, name="status", help="Task overview and details.")
app.add_typer(ui.app, name="ui", help="Start or stop the web UI.")
app.add_typer(config.app, name="config", help="Sync agent and pipeline definitions between config files and the database.")
app.add_typer(vm.start_app, name="start", help="Start the Aquarco VM.")
app.add_typer(vm.stop_app, name="stop", help="Stop the Aquarco VM.")
app.add_typer(vm.destroy_app, name="destroy", help="Destroy the Aquarco VM.")


if __name__ == "__main__":
    app()

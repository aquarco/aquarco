"""Aquarco CLI — main entry point."""

from __future__ import annotations

import typer

from aquarco_cli import __version__
from aquarco_cli.commands import auth, install, run, status, ui, update, watch

app = typer.Typer(
    name="aquarco",
    help="Aquarco CLI — manage your Aquarco VM from the host.",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aquarco {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Aquarco CLI — manage your Aquarco VM from the host."""


# Register top-level commands
app.add_typer(install.app, name="install", help="Bootstrap the Aquarco VM.")
app.add_typer(update.app, name="update", help="Update VM to latest version.")
app.add_typer(auth.app, name="auth", help="Manage Claude and GitHub authentication.")
app.add_typer(watch.app, name="watch", help="Manage watched repositories.")
app.add_typer(run.app, name="run", help="Create a task for agent execution.")
app.add_typer(status.app, name="status", help="Task overview and details.")
app.add_typer(ui.app, name="ui", help="Start or stop the web UI.")


if __name__ == "__main__":
    app()

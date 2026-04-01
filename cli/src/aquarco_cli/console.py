"""Shared Rich console helpers."""

from __future__ import annotations

import httpx
from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


def print_success(message: str) -> None:
    console.print(f"[bold green]{message}[/bold green]")


def print_error(message: str) -> None:
    err_console.print(f"[bold red]Error:[/bold red] {message}")


def print_warning(message: str) -> None:
    err_console.print(f"[bold yellow]Warning:[/bold yellow] {message}")


def print_info(message: str) -> None:
    console.print(f"[bold blue]{message}[/bold blue]")


def make_table(title: str, columns: list[tuple[str, str]]) -> Table:
    """Create a Rich Table with the given title and (name, style) columns."""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    for name, style in columns:
        table.add_column(name, style=style)
    return table


def handle_api_error(exc: Exception) -> None:
    """Print a user-friendly message for common API connectivity issues."""
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        print_error(
            "Cannot reach the Aquarco API. Is the VM running? Try 'aquarco install' or 'aquarco ui'."
        )
    else:
        print_error(str(exc))

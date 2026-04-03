"""aquarco repos — manage watched repositories."""

from __future__ import annotations

import json
from urllib.parse import urlparse

import typer

from aquarco_cli.console import console, handle_api_error, make_table, print_error, print_success
from aquarco_cli.graphql_client import (
    MUTATION_REGISTER_REPOSITORY,
    MUTATION_REMOVE_REPOSITORY,
    QUERY_REPOSITORIES,
    GraphQLClient,
)

app = typer.Typer(
    help="Manage repositories.",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _repo_name_from_url(url: str) -> str:
    """Extract a default repository name from a GitHub URL."""
    path = urlparse(url).path.strip("/")
    # e.g. "user/repo" -> "repo", "user/repo.git" -> "repo"
    name = path.rsplit("/", 1)[-1] if "/" in path else path
    return name.removesuffix(".git")


@app.command()
def add(
    url: str = typer.Argument(..., help="Git repository URL"),
    name: str = typer.Option("", "--name", "-n", help="Repository name (default: derived from URL)"),
    branch: str = typer.Option("", "--branch", "-b", help="Branch to track"),
    pollers: list[str] = typer.Option(
        None, "--poller", "-p",
        help="Pollers to enable (e.g. github-tasks, github-source)",
    ),
) -> None:
    """Add a repository for autonomous watching."""
    repo_name = name or _repo_name_from_url(url)
    client = GraphQLClient()

    variables: dict = {
        "input": {
            "name": repo_name,
            "url": url,
        }
    }
    if branch:
        variables["input"]["branch"] = branch
    if pollers:
        variables["input"]["pollers"] = pollers

    try:
        data = client.execute(MUTATION_REGISTER_REPOSITORY, variables)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    payload = data["registerRepository"]
    if payload.get("errors"):
        for err in payload["errors"]:
            print_error(f"{err.get('field', '')}: {err['message']}")
        raise typer.Exit(code=1)

    repo = payload["repository"]
    print_success(f"Repository '{repo['name']}' registered (status: {repo['cloneStatus']})")


@app.command("list")
def list_repos(
    json_output: bool = typer.Option(False, "--json", help="Output repository list as JSON"),
) -> None:
    """List all watched repositories."""
    client = GraphQLClient()
    try:
        data = client.execute(QUERY_REPOSITORIES)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    repos = data["repositories"]

    if json_output:
        console.print_json(json.dumps({"repositories": repos}))
        return

    if not repos:
        console.print("No repositories registered.")
        return

    table = make_table("Repositories", [
        ("Name", "cyan"),
        ("URL", ""),
        ("Branch", ""),
        ("Clone Status", ""),
        ("Pollers", "dim"),
        ("Tasks", ""),
    ])

    for r in repos:
        status_style = {
            "READY": "[green]READY[/green]",
            "CLONING": "[yellow]CLONING[/yellow]",
            "PENDING": "[yellow]PENDING[/yellow]",
            "ERROR": "[red]ERROR[/red]",
        }.get(r["cloneStatus"], r["cloneStatus"])

        table.add_row(
            r["name"],
            r["url"],
            r.get("branch") or "-",
            status_style,
            ", ".join(r.get("pollers", [])) or "-",
            str(r.get("taskCount", 0)),
        )

    console.print(table)


@app.command()
def remove(
    name: str = typer.Argument(..., help="Repository name to remove"),
) -> None:
    """Remove a watched repository."""
    client = GraphQLClient()
    try:
        data = client.execute(MUTATION_REMOVE_REPOSITORY, {"name": name})
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    payload = data["removeRepository"]
    if payload.get("errors"):
        for err in payload["errors"]:
            print_error(err["message"])
        raise typer.Exit(code=1)

    print_success(f"Repository '{name}' removed.")

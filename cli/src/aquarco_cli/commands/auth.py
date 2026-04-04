"""aquarco auth — authenticate Claude Code and GitHub."""

from __future__ import annotations

import json
import time
import webbrowser

import typer

from aquarco_cli.console import console, handle_api_error, make_table, print_error, print_info, print_success
from aquarco_cli.graphql_client import (
    MUTATION_CLAUDE_LOGIN_START,
    MUTATION_CLAUDE_SUBMIT_CODE,
    MUTATION_GITHUB_LOGIN_POLL,
    MUTATION_GITHUB_LOGIN_START,
    QUERY_CLAUDE_AUTH_STATUS,
    QUERY_GITHUB_AUTH_STATUS,
    GraphQLClient,
    GraphQLError,
)

app = typer.Typer(
    help="Manage authentication for Claude and GitHub.",
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)


@app.callback()
def auth_callback(ctx: typer.Context) -> None:
    """Manage authentication for Claude and GitHub.

    When invoked without a subcommand, automatically detects unauthenticated
    services and runs their login flows.
    """
    if ctx.invoked_subcommand is not None:
        return

    client = GraphQLClient()
    try:
        claude_data = client.execute(QUERY_CLAUDE_AUTH_STATUS)
        github_data = client.execute(QUERY_GITHUB_AUTH_STATUS)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    cs = claude_data["claudeAuthStatus"]
    gs = github_data["githubAuthStatus"]

    if cs["authenticated"] and gs["authenticated"]:
        print_info("All services are already authenticated.")
        ctx.invoke(status)
        return

    if not cs["authenticated"]:
        print_info("Claude is not authenticated. Starting login flow...")
        try:
            ctx.invoke(claude)
        except KeyboardInterrupt:
            raise
        except (SystemExit, Exception) as exc:
            print_error(f"Claude login flow failed: {type(exc).__name__}. Continuing to check GitHub...")

    if not gs["authenticated"]:
        print_info("GitHub is not authenticated. Starting login flow...")
        try:
            ctx.invoke(github)
        except KeyboardInterrupt:
            raise
        except (SystemExit, Exception) as exc:
            print_error(f"GitHub login flow failed: {type(exc).__name__}.")

    # Show final status
    print_info("Checking final auth status...")
    ctx.invoke(status)


@app.command()
def status(
    json_output: bool = typer.Option(False, "--json", help="Output auth status as JSON"),
) -> None:
    """Check authentication status for Claude and GitHub."""
    client = GraphQLClient()
    try:
        claude = client.execute(QUERY_CLAUDE_AUTH_STATUS)
        github = client.execute(QUERY_GITHUB_AUTH_STATUS)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    cs = claude["claudeAuthStatus"]
    gs = github["githubAuthStatus"]

    if json_output:
        console.print_json(json.dumps({
            "claudeAuthStatus": cs,
            "githubAuthStatus": gs,
        }))
        return

    table = make_table("Auth Status", [
        ("Service", "cyan"),
        ("Authenticated", ""),
        ("User", "green"),
    ])

    table.add_row(
        "Claude",
        "[green]Yes[/green]" if cs["authenticated"] else "[red]No[/red]",
        cs.get("email") or "-",
    )
    table.add_row(
        "GitHub",
        "[green]Yes[/green]" if gs["authenticated"] else "[red]No[/red]",
        gs.get("username") or "-",
    )
    console.print(table)


@app.command()
def claude() -> None:
    """Authenticate with Claude via OAuth PKCE flow."""
    client = GraphQLClient()
    try:
        data = client.execute(MUTATION_CLAUDE_LOGIN_START)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    info = data["claudeLoginStart"]
    url = info["authorizeUrl"]

    print_info("Opening browser for Claude authentication...")
    webbrowser.open(url)
    console.print(f"\nIf the browser didn't open, visit:\n  [link={url}]{url}[/link]\n")

    code = typer.prompt("Enter the authorization code")

    try:
        result = client.execute(MUTATION_CLAUDE_SUBMIT_CODE, {"code": code})
        login = result["claudeSubmitCode"]
    except GraphQLError as exc:
        print_error(f"Code submission failed: {exc}")
        raise typer.Exit(code=1) from exc

    if login["success"]:
        print_success(f"Claude authenticated as {login.get('email', 'unknown')}")
    else:
        print_error(f"Authentication failed: {login.get('error', 'unknown error')}")
        raise typer.Exit(code=1)


@app.command()
def github() -> None:
    """Authenticate with GitHub via device flow."""
    client = GraphQLClient()
    try:
        data = client.execute(MUTATION_GITHUB_LOGIN_START)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    info = data["githubLoginStart"]
    user_code = info["userCode"]
    verification_uri = info["verificationUri"]
    expires_in = info["expiresIn"]

    console.print(f"\nYour device code: [bold yellow]{user_code}[/bold yellow]")
    console.print(f"Visit: [link={verification_uri}]{verification_uri}[/link]\n")

    webbrowser.open(verification_uri)
    print_info("Waiting for authorization...")

    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(5)
        try:
            result = client.execute(MUTATION_GITHUB_LOGIN_POLL)
            poll = result["githubLoginPoll"]
        except GraphQLError:
            continue

        if poll["success"]:
            print_success(f"GitHub authenticated as {poll.get('username', 'unknown')}")
            return

        err = poll.get("error", "")
        if err and err not in ("authorization_pending", "slow_down"):
            print_error(f"GitHub auth failed: {err}")
            raise typer.Exit(code=1)

    print_error("GitHub authentication timed out.")
    raise typer.Exit(code=1)

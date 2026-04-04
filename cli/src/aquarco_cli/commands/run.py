"""aquarco run — create a task for agent execution."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import typer

from aquarco_cli.console import console, handle_api_error, print_error, print_info, print_success, print_warning
from aquarco_cli.graphql_client import (
    MUTATION_CREATE_TASK,
    QUERY_PIPELINE_STATUS,
    TERMINAL_STATUSES,
    GraphQLClient,
)

app = typer.Typer(context_settings={"help_option_names": ["-h", "--help"]})

MAX_FOLLOW_ERRORS = 5


@app.callback(invoke_without_command=True)
def run(
    title: str = typer.Argument(..., help="Task title"),
    repo: str = typer.Option(..., "--repo", "-r", help="Target repository name"),
    pipeline: str = typer.Option("", "--pipeline", "-p", help="Pipeline override"),
    priority: int = typer.Option(0, "--priority", help="Task priority (higher = more urgent)"),
    context: str = typer.Option("", "--context", "-c", help="Initial context JSON string or @filepath"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow task progress"),
) -> None:
    """Create a task for autonomous agent execution."""
    client = GraphQLClient()

    # Resolve initial context
    initial_context = None
    if context:
        if context.startswith("@"):
            filepath = Path(context[1:])
            if not filepath.exists():
                print_error(f"Context file not found: {filepath}")
                raise typer.Exit(code=1)
            try:
                initial_context = json.loads(filepath.read_text())
            except json.JSONDecodeError as exc:
                print_error(f"Invalid JSON in context file {filepath}: {exc}")
                raise typer.Exit(code=1) from exc
        else:
            try:
                initial_context = json.loads(context)
            except json.JSONDecodeError:
                # Treat as plain text
                initial_context = {"text": context}

    variables: dict = {
        "input": {
            "title": title,
            "repository": repo,
            "source": "cli",
        }
    }
    if pipeline:
        variables["input"]["pipeline"] = pipeline
    if priority:
        variables["input"]["priority"] = priority
    if initial_context:
        variables["input"]["initialContext"] = initial_context

    try:
        data = client.execute(MUTATION_CREATE_TASK, variables)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

    payload = data["createTask"]
    if payload.get("errors"):
        for err in payload["errors"]:
            print_error(f"{err.get('field', '')}: {err['message']}")
        raise typer.Exit(code=1)

    task = payload["task"]
    task_id = task["id"]
    print_success(f"Task created: {task_id} ({task['status']})")

    if not follow:
        return

    # Poll for progress
    print_info("Following task progress (Ctrl+C to stop)...")
    last_stage = -1
    consecutive_errors = 0
    try:
        while True:
            time.sleep(2)
            try:
                ps_data = client.execute(QUERY_PIPELINE_STATUS, {"taskId": task_id})
                consecutive_errors = 0
            except (httpx.ConnectError, httpx.TimeoutException) as conn_exc:
                handle_api_error(conn_exc)
                raise typer.Exit(code=1) from conn_exc
            except Exception as poll_exc:
                consecutive_errors += 1
                print_warning(f"Poll error: {poll_exc}")
                if consecutive_errors >= MAX_FOLLOW_ERRORS:
                    print_error(
                        f"Too many consecutive errors ({MAX_FOLLOW_ERRORS}), stopping."
                    )
                    raise typer.Exit(code=1) from poll_exc
                continue

            ps = ps_data.get("pipelineStatus")
            if not ps:
                continue

            # Print new stage transitions
            for stage in ps.get("stages", []):
                snum = stage["stageNumber"]
                if snum > last_stage:
                    status_str = stage["status"]
                    agent = stage.get("agent") or "-"
                    console.print(
                        f"  Stage {snum} [{stage['category']}] "
                        f"agent={agent} status={status_str}"
                    )
                    last_stage = snum

            if ps["status"] in TERMINAL_STATUSES:
                style = "green" if ps["status"] == "COMPLETED" else "red"
                console.print(f"\n[bold {style}]Task {task_id}: {ps['status']}[/bold {style}]")
                return

    except KeyboardInterrupt:
        console.print("\nStopped following.")

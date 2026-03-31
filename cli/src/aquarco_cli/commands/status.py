"""aquarco status — task overview and details."""

from __future__ import annotations

import json
import time
from typing import Optional

import typer

from aquarco_cli.console import console, handle_api_error, make_table, print_error, print_info, print_warning
from aquarco_cli.graphql_client import (
    QUERY_DASHBOARD_STATS,
    QUERY_PIPELINE_STATUS,
    QUERY_TASK,
    QUERY_TASKS,
    GraphQLClient,
)

app = typer.Typer()

TERMINAL_STATUSES = {"COMPLETED", "FAILED", "TIMEOUT", "CLOSED"}

MAX_FOLLOW_ERRORS = 5


def _status_style(status: str) -> str:
    return {
        "COMPLETED": "[green]COMPLETED[/green]",
        "EXECUTING": "[yellow]EXECUTING[/yellow]",
        "PENDING": "[blue]PENDING[/blue]",
        "QUEUED": "[blue]QUEUED[/blue]",
        "FAILED": "[red]FAILED[/red]",
        "TIMEOUT": "[red]TIMEOUT[/red]",
        "BLOCKED": "[red]BLOCKED[/red]",
        "RATE_LIMITED": "[yellow]RATE_LIMITED[/yellow]",
        "CLOSED": "[dim]CLOSED[/dim]",
    }.get(status, status)


def _print_dashboard(client: GraphQLClient, limit: int) -> None:
    """Print a dashboard overview."""
    stats_data = client.execute(QUERY_DASHBOARD_STATS)
    tasks_data = client.execute(QUERY_TASKS, {"limit": limit})

    stats = stats_data["dashboardStats"]

    # Summary
    console.print("\n[bold]Dashboard[/bold]\n")
    summary_table = make_table("Task Summary", [("Metric", "cyan"), ("Count", "")])
    summary_table.add_row("Pending", str(stats["pendingTasks"]))
    summary_table.add_row("Executing", str(stats["executingTasks"]))
    summary_table.add_row("Completed", str(stats["completedTasks"]))
    summary_table.add_row("Failed", str(stats["failedTasks"]))
    summary_table.add_row("Blocked", str(stats["blockedTasks"]))
    summary_table.add_row("Total", str(stats["totalTasks"]))
    summary_table.add_row("Active Agents", str(stats["activeAgents"]))
    summary_table.add_row("Cost Today", f"${stats['totalCostToday']:.2f}")
    console.print(summary_table)

    # Recent tasks
    nodes = tasks_data["tasks"]["nodes"]
    if nodes:
        task_table = make_table("Recent Tasks", [
            ("ID", "dim"),
            ("Title", ""),
            ("Repo", "cyan"),
            ("Status", ""),
            ("Pipeline", "dim"),
            ("Created", "dim"),
        ])
        for t in nodes:
            task_table.add_row(
                str(t["id"]),
                t["title"][:50],
                t["repository"]["name"],
                _status_style(t["status"]),
                t["pipeline"],
                t.get("createdAt", "-"),
            )
        console.print(task_table)


def _print_task_detail(client: GraphQLClient, task_id: str) -> None:
    """Print detailed task information."""
    data = client.execute(QUERY_TASK, {"id": task_id})
    task = data.get("task")
    if not task:
        print_error(f"Task {task_id} not found.")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]Task {task['id']}[/bold]\n")
    console.print(f"  Title:      {task['title']}")
    console.print(f"  Status:     {_status_style(task['status'])}")
    console.print(f"  Repository: {task['repository']['name']}")
    console.print(f"  Pipeline:   {task['pipeline']}")
    console.print(f"  Priority:   {task['priority']}")
    console.print(f"  Source:     {task['source']}")
    console.print(f"  Created:    {task.get('createdAt', '-')}")
    console.print(f"  Started:    {task.get('startedAt') or '-'}")
    console.print(f"  Completed:  {task.get('completedAt') or '-'}")
    console.print(f"  Retries:    {task['retryCount']}")
    if task.get("branchName"):
        console.print(f"  Branch:     {task['branchName']}")
    if task.get("prNumber"):
        console.print(f"  PR:         #{task['prNumber']}")
    if task.get("totalCostUsd"):
        console.print(f"  Cost:       ${task['totalCostUsd']:.4f}")
    if task.get("errorMessage"):
        console.print(f"  [red]Error:     {task['errorMessage']}[/red]")

    # Stages
    stages = task.get("stages", [])
    if stages:
        stage_table = make_table("Stages", [
            ("#", "dim"),
            ("Category", "cyan"),
            ("Agent", ""),
            ("Status", ""),
            ("Started", "dim"),
            ("Completed", "dim"),
            ("Cost", "dim"),
        ])
        for s in stages:
            stage_table.add_row(
                str(s["stageNumber"]),
                s["category"],
                s.get("agent") or "-",
                _status_style(s["status"]),
                s.get("startedAt") or "-",
                s.get("completedAt") or "-",
                f"${s['costUsd']:.4f}" if s.get("costUsd") else "-",
            )
        console.print(stage_table)


@app.callback(invoke_without_command=True)
def status(
    task_id: Optional[str] = typer.Argument(None, help="Task ID for detailed view"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow real-time updates"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
    limit: int = typer.Option(10, "--limit", "-l", help="Number of recent tasks to show"),
) -> None:
    """Show task overview or detailed task status."""
    client = GraphQLClient()

    if follow and not task_id:
        print_warning("--follow is only supported with a specific task ID; ignoring.")

    try:
        if task_id:
            if json_output:
                data = client.execute(QUERY_TASK, {"id": task_id})
                console.print_json(json.dumps(data))
                return

            _print_task_detail(client, task_id)

            if follow:
                print_info("Following task (Ctrl+C to stop)...")
                consecutive_errors = 0
                try:
                    while True:
                        time.sleep(2)
                        try:
                            ps = client.execute(QUERY_PIPELINE_STATUS, {"taskId": task_id})
                            consecutive_errors = 0
                        except Exception as poll_exc:
                            consecutive_errors += 1
                            print_warning(f"Poll error: {poll_exc}")
                            if consecutive_errors >= MAX_FOLLOW_ERRORS:
                                print_error(
                                    f"Too many consecutive errors ({MAX_FOLLOW_ERRORS}), stopping."
                                )
                                raise typer.Exit(code=1) from poll_exc
                            continue
                        ps_data = ps.get("pipelineStatus")
                        if ps_data and ps_data["status"] in TERMINAL_STATUSES:
                            _print_task_detail(client, task_id)
                            return
                except KeyboardInterrupt:
                    console.print("\nStopped following.")
        else:
            if json_output:
                stats = client.execute(QUERY_DASHBOARD_STATS)
                tasks = client.execute(QUERY_TASKS, {"limit": limit})
                console.print_json(json.dumps({"dashboardStats": stats, "tasks": tasks}))
                return

            _print_dashboard(client, limit)
    except Exception as exc:
        handle_api_error(exc)
        raise typer.Exit(code=1) from exc

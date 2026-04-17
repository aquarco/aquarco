"""aquarco status — task overview and details."""

from __future__ import annotations

import json
from typing import Optional

import typer

from aquarco_cli.console import console, handle_api_error, make_table, print_error, print_info, print_warning
from aquarco_cli.graphql_client import (
    QUERY_DASHBOARD_STATS,
    QUERY_TASK,
    QUERY_TASKS,
    TERMINAL_STATUSES,
    GraphQLClient,
)
from aquarco_cli.task import follow_task

app = typer.Typer(context_settings={"help_option_names": ["-h", "--help"]})


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
    summary_table.add_row("Cost Today", f"${float(stats.get('totalCostToday') or 0):.2f}")
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
                (t.get("repository") or {}).get("name", "-"),
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
    console.print(f"  Repository: {(task.get('repository') or {}).get('name', '-')}")
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
    task_id: Optional[str] = typer.Argument(
        None,
        help="Optional task ID (e.g. 'github-issue-aquarco-42') for detailed view. "
        "When omitted, shows the dashboard overview.",
    ),
    follow: bool = typer.Option(
        False, "--follow", "-f",
        help="Continuously poll for updates until the task reaches a terminal state. "
        "Requires a TASK_ID.",
    ),
    json_output: bool = typer.Option(
        False, "--json",
        help="Output raw JSON instead of formatted tables. "
        "Works with both dashboard and single-task views.",
    ),
    limit: int = typer.Option(
        10, "--limit", "-l",
        help="Maximum number of recent tasks to display in the dashboard view.",
    ),
) -> None:
    """Show task overview or detailed task status.

    Without arguments, displays the dashboard with task summary and recent tasks.
    Pass a TASK_ID to see detailed information including stages, cost, and timing.
    """
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

                def _on_poll(ps: dict) -> bool:
                    if ps["status"] in TERMINAL_STATUSES:
                        _print_task_detail(client, task_id)
                        return True
                    return False

                follow_task(client, task_id, _on_poll)
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

"""Shared task-following logic used by the run and status commands."""

from __future__ import annotations

import time
from typing import Callable

import httpx
import typer

from aquarco_cli.console import console, handle_api_error, print_error, print_warning
from aquarco_cli.graphql_client import (
    MAX_FOLLOW_ERRORS,
    QUERY_PIPELINE_STATUS,
    TERMINAL_STATUSES,
    GraphQLClient,
)


def follow_task(
    client: GraphQLClient,
    task_id: str,
    on_poll: Callable[[dict], bool],
) -> None:
    """Poll pipelineStatus until *on_poll* returns True or a terminal state is reached.

    *on_poll* receives the ``pipelineStatus`` dict on each successful poll and
    should return ``True`` to stop polling (e.g. when a terminal state is detected).

    Handles connection errors, consecutive-error backoff, KeyboardInterrupt, and
    the standard exit path — callers only need to supply the per-iteration logic.
    """
    consecutive_errors = 0
    try:
        while True:
            time.sleep(2)
            try:
                data = client.execute(QUERY_PIPELINE_STATUS, {"taskId": task_id})
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

            ps = data.get("pipelineStatus")
            if ps and on_poll(ps):
                return

            # Safety net: stop if terminal even if on_poll didn't catch it
            if ps and ps.get("status") in TERMINAL_STATUSES:
                return
    except KeyboardInterrupt:
        console.print("\nStopped following.")

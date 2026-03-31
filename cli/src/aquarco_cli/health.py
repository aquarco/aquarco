"""Stack health probes — check that Aquarco services are reachable."""

from __future__ import annotations

import socket
from dataclasses import dataclass

import httpx

from aquarco_cli.console import console, make_table


@dataclass
class ServiceHealth:
    name: str
    port: int
    healthy: bool
    detail: str = ""


def _check_http(name: str, url: str, port: int, timeout: float = 5.0) -> ServiceHealth:
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return ServiceHealth(name=name, port=port, healthy=resp.status_code < 500,
                             detail=f"HTTP {resp.status_code}")
    except httpx.ConnectError:
        return ServiceHealth(name=name, port=port, healthy=False, detail="Connection refused")
    except Exception as exc:
        return ServiceHealth(name=name, port=port, healthy=False, detail=str(exc))


def _check_tcp(name: str, host: str, port: int, timeout: float = 3.0) -> ServiceHealth:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ServiceHealth(name=name, port=port, healthy=True, detail="TCP open")
    except OSError as exc:
        return ServiceHealth(name=name, port=port, healthy=False, detail=str(exc))


def check_stack_health() -> list[ServiceHealth]:
    """Probe core Aquarco services and return their health."""
    return [
        _check_http("Web/Proxy (Caddy)", "http://localhost:8080", 8080),
        _check_http("GraphQL API", "http://localhost:8080/api/graphql", 8080),
        _check_tcp("PostgreSQL", "localhost", 15432),
    ]


def print_health_table(services: list[ServiceHealth] | None = None) -> bool:
    """Print a Rich table of service health.  Returns True if all healthy."""
    if services is None:
        services = check_stack_health()

    table = make_table("Stack Health", [
        ("Service", "cyan"),
        ("Port", ""),
        ("Status", ""),
        ("Detail", "dim"),
    ])
    all_ok = True
    for svc in services:
        status = "[green]Healthy[/green]" if svc.healthy else "[red]Unhealthy[/red]"
        if not svc.healthy:
            all_ok = False
        table.add_row(svc.name, str(svc.port), status, svc.detail)

    console.print(table)
    return all_ok

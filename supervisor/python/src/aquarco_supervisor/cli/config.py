"""CLI commands for syncing agent and pipeline definitions between config files and the DB.

  aquarco-supervisor config update  [--config PATH]
  aquarco-supervisor config export  [--config PATH]
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from ..agent_store import export_agent_definitions_to_files, sync_all_agent_definitions_to_db
from ..config import load_config
from ..database import Database
from ..exceptions import ConfigError
from ..logging import get_logger
from ..pipeline_store import export_pipeline_definitions_to_file, sync_pipeline_definitions_to_db

log = get_logger("cli-config")

app = typer.Typer(help="Sync agent and pipeline definitions between config files and the database.")

DEFAULT_CONFIG = "/home/agent/aquarco/supervisor/config/supervisor.yaml"


@app.command()
def update(
    config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Supervisor config file path."),
) -> None:
    """Sync agent and pipeline definitions from config files into the database.

    Equivalent to sending SIGHUP to a running supervisor but usable standalone.
    """
    asyncio.run(_update(config))


@app.command()
def export(
    config: str = typer.Option(DEFAULT_CONFIG, "--config", "-c", help="Supervisor config file path."),
) -> None:
    """Export active agent and pipeline definitions from the database back to config files."""
    asyncio.run(_export(config))


async def _update(config_path: str) -> None:
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    db = Database(cfg.spec.database.url)
    await db.connect()
    try:
        agents_dir = Path(cfg.spec.agents_dir)
        schema_dir = agents_dir.parent.parent / "schemas"
        system_schema = schema_dir / "system-agent-v1.json"
        pipeline_schema = schema_dir / "pipeline-agent-v1.json"

        agent_count = await sync_all_agent_definitions_to_db(
            db,
            agents_dir,
            system_schema_path=system_schema if system_schema.exists() else None,
            pipeline_schema_path=pipeline_schema if pipeline_schema.exists() else None,
        )
        typer.echo(f"Agents synced to DB: {agent_count}")

        pipelines_file = cfg.spec.pipelines_file
        if pipelines_file:
            pipeline_count = await sync_pipeline_definitions_to_db(db, Path(pipelines_file))
            typer.echo(f"Pipelines synced to DB: {pipeline_count}")
        else:
            typer.echo("No pipelinesFile configured, skipping pipeline sync.", err=True)
    finally:
        await db.close()


async def _export(config_path: str) -> None:
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    db = Database(cfg.spec.database.url)
    await db.connect()
    try:
        agents_dir = Path(cfg.spec.agents_dir)
        agent_count = await export_agent_definitions_to_files(db, agents_dir)
        typer.echo(f"Agents exported from DB: {agent_count}")

        pipelines_file = cfg.spec.pipelines_file
        if pipelines_file:
            pipeline_count = await export_pipeline_definitions_to_file(db, Path(pipelines_file))
            typer.echo(f"Pipelines exported from DB: {pipeline_count}")
        else:
            typer.echo("No pipelinesFile configured, skipping pipeline export.", err=True)
    finally:
        await db.close()

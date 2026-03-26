"""YAML configuration loading and validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigError, ConfigFileNotFoundError, ConfigValidationError
from .logging import get_logger
from .models import PipelineConfig, PipelineTrigger, StageConfig, SupervisorConfig

log = get_logger("config")

EXPECTED_API_VERSION = "aquarco.supervisor/v1"


def load_config(config_file: str | Path) -> SupervisorConfig:
    """Load and validate supervisor configuration from a YAML file."""
    path = Path(config_file)
    if not path.exists():
        raise ConfigFileNotFoundError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse YAML: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigValidationError("Config file must contain a YAML mapping")

    api_version = raw.get("apiVersion", "")
    if api_version != EXPECTED_API_VERSION:
        raise ConfigValidationError(
            f"Expected apiVersion '{EXPECTED_API_VERSION}', got '{api_version}'"
        )

    try:
        config = SupervisorConfig.model_validate(raw)
    except Exception as e:
        raise ConfigValidationError(f"Config validation failed: {e}") from e

    _validate_config(config)
    return config


def _validate_config(config: SupervisorConfig) -> None:
    """Run additional validation beyond Pydantic field types."""
    if not config.spec.database.url:
        raise ConfigValidationError("Database URL must not be empty")


def load_pipelines(pipelines_file: str | Path) -> list[PipelineConfig]:
    """Load pipeline definitions from a YAML file."""
    path = Path(pipelines_file)
    if not path.exists():
        log.warning("pipelines_file_not_found", path=str(path))
        return []

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"Failed to parse pipelines YAML: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigValidationError("Pipelines file must contain a YAML mapping")

    # Parse top-level categories: list of {name, outputSchema} -> dict
    raw_categories = raw.get("categories", [])
    categories_map: dict[str, dict[str, Any]] = {}
    if isinstance(raw_categories, list):
        for cat in raw_categories:
            if isinstance(cat, dict) and "name" in cat:
                cat_name = cat["name"]
                categories_map[cat_name] = cat.get("outputSchema", {})

    pipelines: list[PipelineConfig] = []
    for entry in raw.get("pipelines", []):
        trigger_data = entry.get("trigger", {})
        trigger = PipelineTrigger(
            labels=trigger_data.get("labels", []),
            events=trigger_data.get("events", []),
        )
        stages = [StageConfig(**s) for s in entry.get("stages", [])]
        pipelines.append(PipelineConfig(
            name=entry["name"],
            version=entry.get("version", "0.0.0"),
            trigger=trigger,
            stages=stages,
            categories=categories_map,
        ))

    log.info("pipelines_loaded", count=len(pipelines))
    return pipelines


def load_secrets(config: SupervisorConfig) -> dict[str, str]:
    """Load secret values from files referenced in config."""
    secrets: dict[str, str] = {}

    github_token_path = Path(config.spec.secrets.github_token_file)
    if github_token_path.exists():
        secrets["github_token"] = github_token_path.read_text().strip()
    else:
        log.warning("github_token_file_missing", path=str(github_token_path))

    anthropic_key_path = Path(config.spec.secrets.anthropic_key_file)
    if anthropic_key_path.exists():
        secrets["anthropic_api_key"] = anthropic_key_path.read_text().strip()
    else:
        log.warning("anthropic_key_file_missing", path=str(anthropic_key_path))

    return secrets


def get_poller_config(config: SupervisorConfig, poller_name: str) -> dict[str, Any] | None:
    """Get configuration for a named poller."""
    for poller in config.spec.pollers:
        if poller.name == poller_name:
            return poller.config
    return None


def get_repository_config(config: SupervisorConfig, repo_name: str) -> dict[str, Any] | None:
    """Get repository config by name. Repositories are now DB-managed; returns None."""
    return None


def get_pipeline_config(
    pipelines: list[PipelineConfig], pipeline_name: str,
) -> list[dict[str, Any]] | None:
    """Get stage configs for a named pipeline."""
    for pipeline in pipelines:
        if pipeline.name == pipeline_name:
            return [s.model_dump() for s in pipeline.stages]
    return None


def get_pipeline_categories(
    pipelines: list[PipelineConfig], pipeline_name: str,
) -> dict[str, dict[str, Any]]:
    """Get category -> outputSchema map for a named pipeline."""
    for pipeline in pipelines:
        if pipeline.name == pipeline_name:
            return pipeline.categories
    return {}

"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aquarco_supervisor.config import (
    get_pipeline_categories,
    get_pipeline_config,
    get_poller_config,
    load_config,
    load_pipelines,
    load_secrets,
)
from aquarco_supervisor.exceptions import (
    ConfigFileNotFoundError,
    ConfigValidationError,
)
from aquarco_supervisor.models import PipelineConfig


def test_load_valid_config(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    assert config.api_version == "aquarco.supervisor/v1"
    assert config.spec.database.url == "postgresql://test:test@localhost:5432/test"
    assert config.spec.database.max_connections == 2
    assert config.spec.global_limits.max_concurrent_agents == 2


def test_load_missing_file() -> None:
    with pytest.raises(ConfigFileNotFoundError):
        load_config("/nonexistent/path.yaml")


def test_load_invalid_api_version(tmp_path: Path) -> None:
    config_file = tmp_path / "bad.yaml"
    config_file.write_text(yaml.dump({"apiVersion": "wrong/v1", "spec": {}}))
    with pytest.raises(ConfigValidationError, match="apiVersion"):
        load_config(config_file)


def test_load_empty_database_url(tmp_path: Path) -> None:
    config = {
        "apiVersion": "aquarco.supervisor/v1",
        "spec": {
            "workdir": "/tmp",
            "agentsDir": "/tmp",
            "promptsDir": "/tmp",
            "database": {"url": ""},
        },
    }
    config_file = tmp_path / "empty-db.yaml"
    config_file.write_text(yaml.dump(config))
    with pytest.raises(ConfigValidationError, match="Database URL"):
        load_config(config_file)


def test_load_invalid_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "bad.yaml"
    config_file.write_text("{{invalid yaml")
    with pytest.raises(Exception):
        load_config(config_file)


def test_get_poller_config(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    github_tasks_cfg = get_poller_config(config, "github-tasks")
    assert github_tasks_cfg is not None
    assert github_tasks_cfg["repositories"] == "all"

    assert get_poller_config(config, "nonexistent") is None


def test_get_pipeline_config(sample_pipelines: list[PipelineConfig]) -> None:
    stages = get_pipeline_config(sample_pipelines, "feature-pipeline")
    assert stages is not None
    assert len(stages) == 5
    assert stages[0]["category"] == "analyze"

    assert get_pipeline_config(sample_pipelines, "nonexistent") is None


def test_load_pipelines(tmp_path: Path) -> None:
    pipelines_data = {
        "pipelines": [
            {
                "name": "test-pipeline",
                "trigger": {"labels": ["test"]},
                "stages": [{"name": "analysis", "category": "analyze", "required": True}],
            }
        ]
    }
    path = tmp_path / "pipelines.yaml"
    path.write_text(yaml.dump(pipelines_data))
    pipelines = load_pipelines(path)
    assert len(pipelines) == 1
    assert pipelines[0].name == "test-pipeline"
    assert pipelines[0].stages[0].name == "analysis"


def test_load_pipelines_with_categories(tmp_path: Path) -> None:
    pipelines_data = {
        "categories": [
            {"name": "analyze", "outputSchema": {"type": "object", "required": ["risks"]}},
            {"name": "design", "outputSchema": {"type": "object"}},
        ],
        "pipelines": [
            {
                "name": "test-pipeline",
                "trigger": {"labels": ["test"]},
                "stages": [{"name": "analysis", "category": "analyze", "required": True}],
            }
        ],
    }
    path = tmp_path / "pipelines.yaml"
    path.write_text(yaml.dump(pipelines_data))
    pipelines = load_pipelines(path)
    assert len(pipelines) == 1
    assert "analyze" in pipelines[0].categories
    assert pipelines[0].categories["analyze"]["type"] == "object"
    assert "design" in pipelines[0].categories


def test_load_pipelines_with_structured_conditions(tmp_path: Path) -> None:
    pipelines_data = {
        "pipelines": [
            {
                "name": "test-pipeline",
                "trigger": {"labels": ["test"]},
                "stages": [
                    {
                        "name": "review",
                        "category": "review",
                        "required": True,
                        "conditions": [
                            {"simple": "severity == major_issues", "no": "fix", "maxRepeats": 3},
                        ],
                    },
                ],
            }
        ]
    }
    path = tmp_path / "pipelines.yaml"
    path.write_text(yaml.dump(pipelines_data))
    pipelines = load_pipelines(path)
    stage = pipelines[0].stages[0]
    assert stage.name == "review"
    assert len(stage.conditions) == 1
    assert stage.conditions[0]["simple"] == "severity == major_issues"
    assert stage.conditions[0]["no"] == "fix"


def test_get_pipeline_categories_found(sample_pipelines: list[PipelineConfig]) -> None:
    categories = get_pipeline_categories(sample_pipelines, "feature-pipeline")
    # sample_pipelines from conftest don't have categories, so empty dict
    assert isinstance(categories, dict)


def test_get_pipeline_categories_not_found(sample_pipelines: list[PipelineConfig]) -> None:
    categories = get_pipeline_categories(sample_pipelines, "nonexistent")
    assert categories == {}


def test_load_pipelines_missing_file() -> None:
    result = load_pipelines("/nonexistent/pipelines.yaml")
    assert result == []


def test_config_defaults(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    assert config.spec.global_limits.max_tokens_per_hour == 1_000_000
    assert config.spec.global_limits.retry_delay_seconds == 60


def test_poller_definitions(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    assert len(config.spec.pollers) == 3
    names = [p.name for p in config.spec.pollers]
    assert "github-tasks" in names
    assert "github-source" in names
    assert "external-triggers" in names


def test_load_non_mapping_yaml(tmp_path: Path) -> None:
    """YAML that is a list (not a mapping) should raise ConfigValidationError."""
    config_file = tmp_path / "list.yaml"
    config_file.write_text("- item1\n- item2\n")
    with pytest.raises(ConfigValidationError, match="YAML mapping"):
        load_config(config_file)


def test_load_config_pydantic_validation_error(tmp_path: Path) -> None:
    """Invalid spec structure should raise ConfigValidationError."""
    config = {
        "apiVersion": "aquarco.supervisor/v1",
        "spec": "not-a-dict",
    }
    config_file = tmp_path / "bad-spec.yaml"
    config_file.write_text(yaml.dump(config))
    with pytest.raises(ConfigValidationError, match="validation failed"):
        load_config(config_file)


def test_load_secrets_reads_files(tmp_path: Path, sample_config_path: Path) -> None:
    """load_secrets reads token and key from files."""
    config = load_config(sample_config_path)

    # Create secret files
    token_file = Path(config.spec.secrets.github_token_file)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("ghp_test_token\n")

    key_file = Path(config.spec.secrets.anthropic_key_file)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text("sk-ant-test-key\n")

    try:
        secrets = load_secrets(config)
        assert secrets["github_token"] == "ghp_test_token"
        assert secrets["anthropic_api_key"] == "sk-ant-test-key"
    finally:
        token_file.unlink(missing_ok=True)
        key_file.unlink(missing_ok=True)


def test_load_secrets_missing_files(sample_config_path: Path) -> None:
    """load_secrets returns empty dict when files don't exist."""
    config = load_config(sample_config_path)
    secrets = load_secrets(config)
    # Files don't exist in test, so secrets should be empty
    assert "github_token" not in secrets or "anthropic_api_key" not in secrets


def test_load_minimal_config(tmp_path: Path) -> None:
    """Config with no pollers should load without error."""
    config = {
        "apiVersion": "aquarco.supervisor/v1",
        "metadata": {"name": "test"},
        "spec": {
            "workdir": "/tmp",
            "agentsDir": "/tmp",
            "promptsDir": "/tmp",
            "database": {
                "url": "postgresql://test:test@localhost/test",
                "maxConnections": 1,
            },
            "logging": {"level": "debug", "format": "json"},
            "globalLimits": {
                "maxConcurrentAgents": 1,
                "maxRetries": 1,
                "cooldownBetweenTasksSeconds": 1,
            },
            "secrets": {
                "githubTokenFile": "/tmp/t",
                "anthropicKeyFile": "/tmp/a",
            },
            "health": {"enabled": False},
            "pollers": [],
        },
    }
    config_file = tmp_path / "minimal.yaml"
    config_file.write_text(yaml.dump(config))
    result = load_config(config_file)
    assert len(result.spec.pollers) == 0

"""Tests for configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aifishtank_supervisor.config import (
    get_pipeline_config,
    get_poller_config,
    get_repository_config,
    load_config,
    load_secrets,
)
from aifishtank_supervisor.exceptions import (
    ConfigFileNotFoundError,
    ConfigValidationError,
)


def test_load_valid_config(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    assert config.api_version == "aifishtank.supervisor/v1"
    assert config.spec.database.url == "postgresql://test:test@localhost:5432/test"
    assert config.spec.database.max_connections == 2
    assert config.spec.global_limits.max_concurrent_agents == 2
    assert len(config.spec.repositories) == 1
    assert config.spec.repositories[0].name == "test-repo"


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
        "apiVersion": "aifishtank.supervisor/v1",
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


def test_get_repository_config(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    repo_cfg = get_repository_config(config, "test-repo")
    assert repo_cfg is not None
    assert repo_cfg["url"] == "git@github.com:test/repo.git"

    assert get_repository_config(config, "nonexistent") is None


def test_get_pipeline_config(sample_config_path: Path) -> None:
    config = load_config(sample_config_path)
    stages = get_pipeline_config(config, "feature-pipeline")
    assert stages is not None
    assert len(stages) == 5
    assert stages[0]["category"] == "analyze"

    assert get_pipeline_config(config, "nonexistent") is None


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
        "apiVersion": "aifishtank.supervisor/v1",
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


def test_load_no_repositories_warns(tmp_path: Path) -> None:
    """Config with no repositories should warn but not raise."""
    config = {
        "apiVersion": "aifishtank.supervisor/v1",
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
            "repositories": [],
            "pollers": [],
            "pipelines": [],
        },
    }
    config_file = tmp_path / "no-repos.yaml"
    config_file.write_text(yaml.dump(config))
    # Should not raise — just warns
    result = load_config(config_file)
    assert len(result.spec.repositories) == 0

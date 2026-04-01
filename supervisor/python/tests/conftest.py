"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aquarco_supervisor.config import load_config, load_pipelines
from aquarco_supervisor.models import PipelineConfig, SupervisorConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_config_path(tmp_path: Path) -> Path:
    """Create a minimal valid supervisor config file."""
    config = {
        "apiVersion": "aquarco.supervisor/v1",
        "metadata": {"name": "test-supervisor", "version": "1.0.0"},
        "spec": {
            "workdir": "/tmp/test",
            "agentsDir": "/tmp/test/agents/definitions",
            "promptsDir": "/tmp/test/agents/prompts",
            "pipelinesFile": str(tmp_path / "pipelines.yaml"),
            "database": {
                "url": "postgresql://test:test@localhost:5432/test",
                "maxConnections": 2,
            },
            "logging": {"level": "debug", "format": "json"},
            "globalLimits": {
                "maxConcurrentAgents": 2,
                "maxRetries": 3,
                "cooldownBetweenTasksSeconds": 1,
            },
            "secrets": {
                "githubTokenFile": "/tmp/test/github-token",
                "anthropicKeyFile": "/tmp/test/anthropic-key",
            },
            "health": {"enabled": False},
            "pollers": [
                {
                    "name": "github-tasks",
                    "type": "github-tasks",
                    "enabled": True,
                    "intervalSeconds": 60,
                    "config": {
                        "repositories": "all",
                        "categorization": {
                            "defaultCategory": "analyze",
                            "labelMapping": {
                                "bug": "implement",
                                "feature": "analyze",
                            },
                        },
                    },
                },
                {
                    "name": "github-source",
                    "type": "github-source",
                    "enabled": True,
                    "intervalSeconds": 30,
                    "config": {
                        "repositories": "all",
                        "triggers": {
                            "pr_opened": ["review"],
                            "pr_updated": ["review"],
                        },
                    },
                },
                {
                    "name": "external-triggers",
                    "type": "file-watch",
                    "enabled": True,
                    "intervalSeconds": 10,
                    "config": {
                        "watchDir": str(tmp_path / "triggers"),
                        "processedDir": str(tmp_path / "triggers" / "processed"),
                    },
                },
            ],
        },
    }

    config_file = tmp_path / "supervisor.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))

    # Write pipelines file
    pipelines = {
        "apiVersion": "aquarco.agents/v1",
        "kind": "PipelineDefinition",
        "pipelines": [
            {
                "name": "feature-pipeline",
                "version": "1.0.0",
                "trigger": {"labels": ["feature", "enhancement"]},
                "stages": [
                    {"name": "analysis", "category": "analyze", "required": True},
                    {
                        "name": "design",
                        "category": "design",
                        "required": True,
                    },
                    {"name": "implementation", "category": "implement", "required": True},
                    {"name": "test", "category": "test", "required": True},
                    {"name": "review", "category": "review", "required": True},
                ],
            },
            {
                "name": "bugfix-pipeline",
                "version": "1.0.0",
                "trigger": {"labels": ["bug"]},
                "stages": [
                    {"name": "analysis", "category": "analyze", "required": True},
                    {"name": "implementation", "category": "implement", "required": True},
                    {"name": "test", "category": "test", "required": True},
                    {"name": "review", "category": "review", "required": True},
                ],
            },
        ],
    }
    pipelines_file = tmp_path / "pipelines.yaml"
    pipelines_file.write_text(yaml.dump(pipelines, default_flow_style=False))

    return config_file


@pytest.fixture
def sample_pipelines(sample_config_path: Path, tmp_path: Path) -> list[PipelineConfig]:
    """Load sample pipelines from the test pipelines file."""
    return load_pipelines(tmp_path / "pipelines.yaml")


@pytest.fixture
def sample_config(sample_config_path: Path) -> SupervisorConfig:
    """Load a parsed sample config."""
    return load_config(sample_config_path)

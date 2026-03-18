"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aifishtank_supervisor.config import load_config
from aifishtank_supervisor.models import SupervisorConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_config_path(tmp_path: Path) -> Path:
    """Create a minimal valid supervisor config file."""
    config = {
        "apiVersion": "aifishtank.supervisor/v1",
        "metadata": {"name": "test-supervisor", "version": "1.0.0"},
        "spec": {
            "workdir": "/tmp/test",
            "agentsDir": "/tmp/test/agents/definitions",
            "promptsDir": "/tmp/test/agents/prompts",
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
            "repositories": [
                {
                    "name": "test-repo",
                    "url": "git@github.com:test/repo.git",
                    "branch": "main",
                    "cloneDir": "/tmp/test/repos/test-repo",
                    "pollers": ["github-tasks", "github-source"],
                    "auth": "ssh",
                }
            ],
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
                                "bug": "implementation",
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
            "pipelines": [
                {
                    "name": "feature-pipeline",
                    "trigger": {"labels": ["feature", "enhancement"]},
                    "stages": [
                        {"category": "analyze", "required": True},
                        {
                            "category": "design",
                            "required": True,
                            "conditions": [
                                "analysis.estimated_complexity >= medium"
                            ],
                        },
                        {"category": "implementation", "required": True},
                        {"category": "test", "required": True},
                        {"category": "review", "required": True},
                    ],
                },
                {
                    "name": "bugfix-pipeline",
                    "trigger": {"labels": ["bug"]},
                    "stages": [
                        {"category": "analyze", "required": True},
                        {"category": "implementation", "required": True},
                        {"category": "test", "required": True},
                        {"category": "review", "required": True},
                    ],
                },
            ],
        },
    }

    config_file = tmp_path / "supervisor.yaml"
    config_file.write_text(yaml.dump(config, default_flow_style=False))
    return config_file


@pytest.fixture
def sample_config(sample_config_path: Path) -> SupervisorConfig:
    """Load a parsed sample config."""
    return load_config(sample_config_path)

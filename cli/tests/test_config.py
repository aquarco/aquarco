"""Tests for CLI configuration."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from aquarco_cli.config import CliConfig


class TestCliConfig:
    def test_default_api_url(self):
        config = CliConfig()
        assert config.api_url == "http://localhost:8080/api/graphql"

    @patch.dict(os.environ, {"AQUARCO_API_URL": "http://custom:9090/graphql"})
    def test_api_url_from_env(self):
        config = CliConfig()
        assert config.api_url == "http://custom:9090/graphql"

    @patch.dict(os.environ, {"AQUARCO_HTTP_TIMEOUT": "60"})
    def test_timeout_from_env(self):
        config = CliConfig()
        assert config.http_timeout == 60.0

    def test_resolve_vagrant_dir_explicit(self):
        config = CliConfig(vagrant_dir="/explicit/path")
        result = config.resolve_vagrant_dir()
        assert result == Path("/explicit/path")

    def test_resolve_vagrant_dir_fallback_to_cwd(self):
        config = CliConfig(vagrant_dir="")
        # When no Vagrantfile can be found, falls back to cwd
        result = config.resolve_vagrant_dir()
        assert result.is_absolute()

"""Tests for CLI configuration."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from aquarco_cli.config import CliConfig, get_config, reset_config


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

    def test_default_http_timeout(self):
        config = CliConfig()
        assert config.http_timeout == 30.0

    def test_default_vm_name_empty(self):
        config = CliConfig()
        assert config.vm_name == ""

    @patch.dict(os.environ, {"AQUARCO_VM_NAME": "myvm"})
    def test_vm_name_from_env(self):
        config = CliConfig()
        assert config.vm_name == "myvm"

    def test_max_parent_depth_bounds_walk(self):
        config = CliConfig(vagrant_dir="")
        # The _MAX_PARENT_DEPTH should be 10
        assert config._MAX_PARENT_DEPTH == 10

    def test_resolve_vagrant_dir_with_vagrantfile_in_parent(self, tmp_path):
        """When vagrant/prod/Vagrantfile exists in a parent, it should be found."""
        prod_dir = tmp_path / "vagrant" / "prod"
        prod_dir.mkdir(parents=True)
        (prod_dir / "Vagrantfile").write_text("# Vagrant config")
        subdir = tmp_path / "sub" / "dir"
        subdir.mkdir(parents=True)

        config = CliConfig(vagrant_dir="")
        with patch("aquarco_cli.config.Path.cwd", return_value=subdir):
            result = config.resolve_vagrant_dir()
        assert result == prod_dir.resolve()

    def test_resolve_vagrant_dir_direct_vagrantfile(self, tmp_path):
        """When Vagrantfile is directly in a parent directory."""
        (tmp_path / "Vagrantfile").write_text("# Vagrant config")
        config = CliConfig(vagrant_dir="")
        with patch("aquarco_cli.config.Path.cwd", return_value=tmp_path):
            result = config.resolve_vagrant_dir()
        assert result == tmp_path.resolve()


class TestLazySingleton:
    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()

    def test_get_config_returns_same_instance(self):
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_reset_config_clears_singleton(self):
        c1 = get_config()
        reset_config()
        c2 = get_config()
        assert c1 is not c2

    @patch.dict(os.environ, {"AQUARCO_API_URL": "http://lazy:1234/gql"})
    def test_lazy_reads_env_on_first_access(self):
        config = get_config()
        assert config.api_url == "http://lazy:1234/gql"

"""Regression tests for issue #79: Remove repository-specific agents.

These tests guard against accidental re-introduction of the autoloading
subsystem that was removed. They verify that deleted modules, classes,
functions, and parameters no longer exist in the codebase.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib

import pytest


# ---------------------------------------------------------------------------
# 1. agent_autoloader module must not exist
# ---------------------------------------------------------------------------

def test_agent_autoloader_module_cannot_be_imported():
    """The agent_autoloader module was deleted and must not be importable."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aquarco_supervisor.agent_autoloader")


# ---------------------------------------------------------------------------
# 2. config_overlay.resolve_config must not accept autoloaded_agents
# ---------------------------------------------------------------------------

def test_resolve_config_has_no_autoloaded_agents_parameter():
    """resolve_config() signature must not include an autoloaded_agents param."""
    from aquarco_supervisor.config_overlay import resolve_config

    sig = inspect.signature(resolve_config)
    assert "autoloaded_agents" not in sig.parameters, (
        "resolve_config() still accepts 'autoloaded_agents' — "
        "the autoloaded layer was supposed to be removed"
    )


def test_resolve_config_accepts_three_layers_only():
    """resolve_config() should accept exactly 6 params: default agents/pipelines
    + global overlay/base + repo overlay/base.  No 4th autoloaded layer."""
    from aquarco_supervisor.config_overlay import resolve_config

    sig = inspect.signature(resolve_config)
    param_names = list(sig.parameters.keys())
    expected = [
        "default_agents",
        "default_pipelines",
        "global_overlay",
        "global_overlay_base",
        "repo_overlay",
        "repo_overlay_base",
    ]
    assert param_names == expected, (
        f"resolve_config() signature changed unexpectedly: {param_names}"
    )


# ---------------------------------------------------------------------------
# 3. AgentRegistry must not have _load_autoloaded_agents
# ---------------------------------------------------------------------------

def test_agent_registry_has_no_autoloaded_agents_method():
    """AgentRegistry must not have _load_autoloaded_agents()."""
    from aquarco_supervisor.pipeline.agent_registry import AgentRegistry

    assert not hasattr(AgentRegistry, "_load_autoloaded_agents"), (
        "AgentRegistry still has _load_autoloaded_agents — removal incomplete"
    )


# ---------------------------------------------------------------------------
# 4. models must not contain RepoAgentScan or RepoAgentScanStatus
# ---------------------------------------------------------------------------

def test_models_no_repo_agent_scan_status():
    """RepoAgentScanStatus enum must not exist in models."""
    import aquarco_supervisor.models as models

    assert not hasattr(models, "RepoAgentScanStatus"), (
        "models.RepoAgentScanStatus still exists — removal incomplete"
    )


def test_models_no_repo_agent_scan():
    """RepoAgentScan dataclass must not exist in models."""
    import aquarco_supervisor.models as models

    assert not hasattr(models, "RepoAgentScan"), (
        "models.RepoAgentScan still exists — removal incomplete"
    )


# ---------------------------------------------------------------------------
# 5. config_store must not have autoload-specific functions
# ---------------------------------------------------------------------------

def test_config_store_no_deactivate_autoloaded_agents():
    """deactivate_autoloaded_agents() must not exist in config_store."""
    import aquarco_supervisor.config_store as cs

    assert not hasattr(cs, "deactivate_autoloaded_agents"), (
        "config_store.deactivate_autoloaded_agents still exists"
    )


def test_config_store_no_read_autoloaded_agents_from_db():
    """read_autoloaded_agents_from_db() must not exist in config_store."""
    import aquarco_supervisor.config_store as cs

    assert not hasattr(cs, "read_autoloaded_agents_from_db"), (
        "config_store.read_autoloaded_agents_from_db still exists"
    )


# ---------------------------------------------------------------------------
# 6. main.py must not have auto-scan or agent-scan-command methods
# ---------------------------------------------------------------------------

def test_main_no_auto_scan_new_repos():
    """Supervisor must not have _auto_scan_new_repos()."""
    from aquarco_supervisor.main import Supervisor

    assert not hasattr(Supervisor, "_auto_scan_new_repos"), (
        "Supervisor._auto_scan_new_repos still exists"
    )


def test_main_no_process_agent_scan_commands():
    """Supervisor must not have _process_agent_scan_commands()."""
    from aquarco_supervisor.main import Supervisor

    assert not hasattr(Supervisor, "_process_agent_scan_commands"), (
        "Supervisor._process_agent_scan_commands still exists"
    )


# ---------------------------------------------------------------------------
# 7. repo-descriptor-agent.yaml must not exist
# ---------------------------------------------------------------------------

def test_repo_descriptor_agent_yaml_removed():
    """repo-descriptor-agent.yaml should have been deleted."""
    project_root = pathlib.Path(__file__).resolve().parents[3]  # supervisor/python/tests -> project root
    agent_file = project_root / "config" / "agents" / "definitions" / "system" / "repo-descriptor-agent.yaml"
    assert not agent_file.exists(), (
        f"repo-descriptor-agent.yaml still exists at {agent_file}"
    )


# ---------------------------------------------------------------------------
# 8. No source files import agent_autoloader
# ---------------------------------------------------------------------------

def test_no_source_imports_agent_autoloader():
    """No .py file under src/ should import agent_autoloader."""
    src_dir = pathlib.Path(__file__).resolve().parents[1] / "src"
    violations = []
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text()
        if "agent_autoloader" in content:
            violations.append(str(py_file))
    assert not violations, (
        f"Files still reference agent_autoloader: {violations}"
    )


# ---------------------------------------------------------------------------
# 9. DB migration for dropping repo_agent_scans exists
# ---------------------------------------------------------------------------

def test_drop_repo_agent_scans_migration_exists():
    """A migration to drop the repo_agent_scans table must exist."""
    project_root = pathlib.Path(__file__).resolve().parents[3]
    migrations_dir = project_root / "db" / "migrations"
    if not migrations_dir.exists():
        pytest.skip("db/migrations directory not found")

    drop_migrations = [
        f for f in migrations_dir.iterdir()
        if f.is_file() and "drop_repo_agent_scan" in f.name.lower()
    ]
    assert drop_migrations, (
        "No migration found to drop repo_agent_scans table"
    )

"""Tests for backward-compatible re-exports via __getattr__.

After the codebase simplification (#109), config_store.py delegates to
agent_store.py and pipeline_store.py via PEP 562 module-level __getattr__.
Similarly, task_queue.py delegates stage methods to StageManager.

These tests ensure that existing import paths continue to work.
"""

from __future__ import annotations

import pytest


class TestConfigStoreReExports:
    """config_store should re-export agent_store and pipeline_store names."""

    def test_import_parse_md_frontmatter(self):
        from aquarco_supervisor.config_store import _parse_md_frontmatter
        assert callable(_parse_md_frontmatter)

    def test_import_sync_all_agent_definitions_to_db(self):
        from aquarco_supervisor.config_store import sync_all_agent_definitions_to_db
        assert callable(sync_all_agent_definitions_to_db)

    def test_import_store_agent_definitions(self):
        from aquarco_supervisor.config_store import store_agent_definitions
        assert callable(store_agent_definitions)

    def test_import_load_pipeline_definitions_from_file(self):
        from aquarco_supervisor.config_store import load_pipeline_definitions_from_file
        assert callable(load_pipeline_definitions_from_file)

    def test_import_store_pipeline_definitions(self):
        from aquarco_supervisor.config_store import store_pipeline_definitions
        assert callable(store_pipeline_definitions)

    def test_import_export_agent_definitions_to_files(self):
        from aquarco_supervisor.config_store import export_agent_definitions_to_files
        assert callable(export_agent_definitions_to_files)

    def test_import_read_agent_definitions_from_db(self):
        from aquarco_supervisor.config_store import read_agent_definitions_from_db
        assert callable(read_agent_definitions_from_db)

    def test_import_sync_pipeline_definitions_to_db(self):
        from aquarco_supervisor.config_store import sync_pipeline_definitions_to_db
        assert callable(sync_pipeline_definitions_to_db)

    def test_import_export_pipeline_definitions_to_file(self):
        from aquarco_supervisor.config_store import export_pipeline_definitions_to_file
        assert callable(export_pipeline_definitions_to_file)

    def test_import_read_pipeline_definitions_from_db(self):
        from aquarco_supervisor.config_store import read_pipeline_definitions_from_db
        assert callable(read_pipeline_definitions_from_db)

    def test_unknown_attribute_raises(self):
        with pytest.raises(AttributeError, match="no attribute"):
            from aquarco_supervisor import config_store
            config_store.nonexistent_function  # noqa: B018


class TestConfigStoreDirectImports:
    """Shared constants and functions should still be directly available."""

    def test_agent_api_version(self):
        from aquarco_supervisor.config_store import AGENT_API_VERSION
        assert AGENT_API_VERSION == "aquarco.agents/v1"

    def test_agent_kind(self):
        from aquarco_supervisor.config_store import AGENT_KIND
        assert AGENT_KIND == "AgentDefinition"

    def test_pipeline_kind(self):
        from aquarco_supervisor.config_store import PIPELINE_KIND
        assert PIPELINE_KIND == "PipelineDefinition"

    def test_validate_agent_definition(self):
        from aquarco_supervisor.config_store import validate_agent_definition
        assert callable(validate_agent_definition)

    def test_validate_pipeline_definition(self):
        from aquarco_supervisor.config_store import validate_pipeline_definition
        assert callable(validate_pipeline_definition)

    def test_load_json_schema(self):
        from aquarco_supervisor.config_store import _load_json_schema
        assert callable(_load_json_schema)


class TestExecutorReExports:
    """executor.py re-exports for backward compatibility."""

    def test_import_execute_claude_from_executor(self):
        from aquarco_supervisor.pipeline.executor import execute_claude
        assert callable(execute_claude)

    def test_import_check_conditions_from_executor(self):
        from aquarco_supervisor.pipeline.executor import check_conditions
        assert callable(check_conditions)

    def test_import_compare_complexity_from_executor(self):
        from aquarco_supervisor.pipeline.executor import _compare_complexity
        assert callable(_compare_complexity)

    def test_import_git_ops_from_executor(self):
        from aquarco_supervisor.pipeline.executor import _auto_commit
        from aquarco_supervisor.pipeline.executor import _get_ahead_count
        from aquarco_supervisor.pipeline.executor import _git_checkout
        from aquarco_supervisor.pipeline.executor import _push_if_ahead
        assert callable(_auto_commit)
        assert callable(_get_ahead_count)
        assert callable(_git_checkout)
        assert callable(_push_if_ahead)

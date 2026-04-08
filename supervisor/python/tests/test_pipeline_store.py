"""Tests for pipeline_store — pipeline YAML loading and validation.

Covers:
- load_pipeline_definitions_from_file: YAML parsing, missing files, validation
- Backward-compat re-exports from config_store
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from aquarco_supervisor.pipeline_store import load_pipeline_definitions_from_file


# -----------------------------------------------------------------------
# load_pipeline_definitions_from_file
# -----------------------------------------------------------------------


class TestLoadPipelineDefinitionsFromFile:
    def _write_pipelines(self, tmp_path: Path, data: dict[str, Any]) -> Path:
        f = tmp_path / "pipelines.yaml"
        f.write_text(yaml.dump(data, default_flow_style=False))
        return f

    def test_loads_valid_pipelines(self, tmp_path: Path):
        data = {
            "pipelines": [
                {"name": "feature", "version": "1.0.0", "stages": []},
                {"name": "bugfix", "version": "1.0.0", "stages": []},
            ]
        }
        result = load_pipeline_definitions_from_file(self._write_pipelines(tmp_path, data))
        assert len(result) == 2
        assert result[0]["name"] == "feature"
        assert result[1]["name"] == "bugfix"

    def test_returns_empty_for_missing_file(self, tmp_path: Path):
        result = load_pipeline_definitions_from_file(tmp_path / "nonexistent.yaml")
        assert result == []

    def test_returns_empty_for_invalid_yaml(self, tmp_path: Path):
        f = tmp_path / "pipelines.yaml"
        f.write_text(":::\n  - {invalid yaml: [")
        result = load_pipeline_definitions_from_file(f)
        assert result == []

    def test_returns_empty_when_root_not_dict(self, tmp_path: Path):
        f = tmp_path / "pipelines.yaml"
        f.write_text("- just\n- a\n- list\n")
        result = load_pipeline_definitions_from_file(f)
        assert result == []

    def test_returns_empty_when_pipelines_key_not_list(self, tmp_path: Path):
        data = {"pipelines": "not-a-list"}
        result = load_pipeline_definitions_from_file(self._write_pipelines(tmp_path, data))
        assert result == []

    def test_returns_empty_when_no_pipelines_key(self, tmp_path: Path):
        data = {"categories": {"test": {}}}
        result = load_pipeline_definitions_from_file(self._write_pipelines(tmp_path, data))
        assert result == []

    def test_schema_validation_rejects_invalid(self, tmp_path: Path):
        """When a schema is provided and validation fails, returns empty."""
        data = {"pipelines": [{"name": "test"}]}
        # Provide a schema that requires 'version' field
        schema = {
            "type": "object",
            "required": ["apiVersion"],
            "properties": {"apiVersion": {"type": "string"}},
        }
        result = load_pipeline_definitions_from_file(
            self._write_pipelines(tmp_path, data), schema=schema
        )
        assert result == []

    def test_returns_pipeline_dicts_with_all_fields(self, tmp_path: Path):
        data = {
            "pipelines": [
                {
                    "name": "feature",
                    "version": "2.0.0",
                    "trigger": {"labels": ["enhancement"]},
                    "stages": [
                        {"name": "analyze", "category": "analyze", "required": True},
                    ],
                }
            ]
        }
        result = load_pipeline_definitions_from_file(self._write_pipelines(tmp_path, data))
        assert len(result) == 1
        p = result[0]
        assert p["name"] == "feature"
        assert p["version"] == "2.0.0"
        assert p["trigger"]["labels"] == ["enhancement"]
        assert len(p["stages"]) == 1


# -----------------------------------------------------------------------
# Backward compat: config_store re-exports pipeline_store functions
# -----------------------------------------------------------------------


class TestPipelineStoreBackwardCompat:
    def test_sync_reexported(self):
        from aquarco_supervisor.config_store import sync_pipeline_definitions_to_db
        from aquarco_supervisor.pipeline_store import sync_pipeline_definitions_to_db as orig
        assert sync_pipeline_definitions_to_db is orig

    def test_load_reexported(self):
        from aquarco_supervisor.config_store import load_pipeline_definitions_from_file as lazy
        from aquarco_supervisor.pipeline_store import load_pipeline_definitions_from_file as orig
        assert lazy is orig

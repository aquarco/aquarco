"""Tests for config loading of pipeline categories and named stages with conditions.

Covers acceptance criteria:
  - load_pipelines() correctly parses pipelines.yaml with categories and conditions
  - categories['analyze']['type'] == 'object'
  - get_pipeline_config() includes stage name and structured conditions
  - get_pipeline_categories() returns category->outputSchema mapping
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from aquarco_supervisor.config import (
    get_pipeline_categories,
    get_pipeline_config,
    load_pipelines,
)
from aquarco_supervisor.models import PipelineConfig


def _write_pipelines(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "pipelines.yaml"
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


class TestLoadPipelinesCategories:
    def test_categories_parsed_as_dict(self, tmp_path: Path) -> None:
        """Categories list is converted to name->outputSchema dict."""
        data = {
            "categories": [
                {"name": "analyze", "outputSchema": {"type": "object", "required": ["risks"]}},
                {"name": "test", "outputSchema": {"type": "object", "required": ["tests_added"]}},
            ],
            "pipelines": [
                {
                    "name": "test-pipeline",
                    "trigger": {"labels": ["test"]},
                    "stages": [{"name": "s1", "category": "analyze"}],
                }
            ],
        }
        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        assert len(pipelines) == 1
        assert "analyze" in pipelines[0].categories
        assert pipelines[0].categories["analyze"]["type"] == "object"
        assert "risks" in pipelines[0].categories["analyze"]["required"]
        assert "test" in pipelines[0].categories
        assert pipelines[0].categories["test"]["required"] == ["tests_added"]

    def test_categories_shared_across_pipelines(self, tmp_path: Path) -> None:
        """All pipelines in same file share the same categories dict."""
        data = {
            "categories": [
                {"name": "analyze", "outputSchema": {"type": "object"}},
            ],
            "pipelines": [
                {
                    "name": "pipeline-a",
                    "trigger": {"labels": ["a"]},
                    "stages": [{"name": "s1", "category": "analyze"}],
                },
                {
                    "name": "pipeline-b",
                    "trigger": {"labels": ["b"]},
                    "stages": [{"name": "s1", "category": "analyze"}],
                },
            ],
        }
        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        assert len(pipelines) == 2
        assert pipelines[0].categories == pipelines[1].categories

    def test_no_categories_section(self, tmp_path: Path) -> None:
        """Missing categories section => empty dict."""
        data = {
            "pipelines": [
                {
                    "name": "simple",
                    "trigger": {"labels": ["test"]},
                    "stages": [{"name": "s1", "category": "analyze"}],
                }
            ],
        }
        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        assert pipelines[0].categories == {}

    def test_categories_without_output_schema(self, tmp_path: Path) -> None:
        """Category entry without outputSchema => empty dict for that category."""
        data = {
            "categories": [
                {"name": "custom_category"},
            ],
            "pipelines": [
                {
                    "name": "test",
                    "trigger": {"labels": ["test"]},
                    "stages": [{"name": "s1", "category": "custom_category"}],
                }
            ],
        }
        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        assert "custom_category" in pipelines[0].categories
        assert pipelines[0].categories["custom_category"] == {}


class TestGetPipelineConfig:
    def test_includes_stage_name(self, tmp_path: Path) -> None:
        """get_pipeline_config returns dicts including 'name' field."""
        data = {
            "pipelines": [
                {
                    "name": "test-pipeline",
                    "trigger": {"labels": ["test"]},
                    "stages": [
                        {"name": "analysis", "category": "analyze"},
                        {"name": "review", "category": "review"},
                    ],
                }
            ],
        }
        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        stages = get_pipeline_config(pipelines, "test-pipeline")
        assert stages is not None
        assert stages[0]["name"] == "analysis"
        assert stages[1]["name"] == "review"

    def test_includes_structured_conditions(self, tmp_path: Path) -> None:
        """get_pipeline_config returns dicts with structured conditions."""
        data = {
            "pipelines": [
                {
                    "name": "test-pipeline",
                    "trigger": {"labels": ["test"]},
                    "stages": [
                        {
                            "name": "review",
                            "category": "review",
                            "conditions": [
                                {"simple": "severity == blocking", "no": "fix", "maxRepeats": 3},
                                {"ai": "All risks mitigated?", "no": "implementation"},
                            ],
                        }
                    ],
                }
            ],
        }
        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        stages = get_pipeline_config(pipelines, "test-pipeline")
        assert stages is not None
        conditions = stages[0]["conditions"]
        assert len(conditions) == 2
        assert conditions[0]["simple"] == "severity == blocking"
        assert conditions[0]["no"] == "fix"
        assert conditions[0]["maxRepeats"] == 3
        assert conditions[1]["ai"] == "All risks mitigated?"


class TestGetPipelineCategories:
    def test_returns_categories_dict(self, tmp_path: Path) -> None:
        data = {
            "categories": [
                {"name": "analyze", "outputSchema": {"type": "object"}},
                {"name": "design", "outputSchema": {"type": "object", "required": ["steps"]}},
            ],
            "pipelines": [
                {
                    "name": "feature-pipeline",
                    "trigger": {"labels": ["feature"]},
                    "stages": [{"name": "s1", "category": "analyze"}],
                }
            ],
        }
        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        categories = get_pipeline_categories(pipelines, "feature-pipeline")
        assert categories["analyze"]["type"] == "object"
        assert categories["design"]["required"] == ["steps"]

    def test_returns_empty_for_unknown_pipeline(self, tmp_path: Path) -> None:
        data = {
            "pipelines": [
                {
                    "name": "test",
                    "trigger": {"labels": ["test"]},
                    "stages": [{"name": "s1", "category": "analyze"}],
                }
            ],
        }
        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        categories = get_pipeline_categories(pipelines, "nonexistent")
        assert categories == {}


class TestLoadPipelinesWithConditions:
    """Full integration tests for loading pipelines with all new fields."""

    def test_complex_pipeline_with_conditions_and_categories(self, tmp_path: Path) -> None:
        """Load a pipeline resembling the real config/pipelines.yaml."""
        data = {
            "categories": [
                {
                    "name": "analyze",
                    "outputSchema": {
                        "type": "object",
                        "required": ["risks", "issue_summary"],
                        "properties": {
                            "risks": {"type": "array"},
                            "issue_summary": {"type": "string"},
                        },
                    },
                },
                {
                    "name": "review",
                    "outputSchema": {
                        "type": "object",
                        "required": ["severity", "recommendation"],
                        "properties": {
                            "severity": {"type": "string"},
                            "recommendation": {"type": "string"},
                        },
                    },
                },
            ],
            "pipelines": [
                {
                    "name": "feature-pipeline",
                    "version": "2.0.0",
                    "trigger": {"labels": ["feature", "enhancement"]},
                    "stages": [
                        {"name": "analysis", "category": "analyze", "required": True},
                        {"name": "design", "category": "design", "required": True},
                        {
                            "name": "implementation",
                            "category": "implement",
                            "required": False,
                            "conditions": [
                                {"ai": "All design.acceptance_criteria fulfilled?", "no": "implementation", "maxRepeats": 5},
                            ],
                        },
                        {
                            "name": "review",
                            "category": "review",
                            "required": True,
                            "conditions": [
                                {"ai": "All analysis.risks mitigated?", "no": "implementation", "maxRepeats": 5},
                                {"simple": "severity == major_issues || severity == blocking", "no": "test", "maxRepeats": 5},
                            ],
                        },
                        {
                            "name": "fix-review-findings",
                            "category": "implement",
                            "required": False,
                            "conditions": [
                                {"simple": True, "yes": "review", "maxRepeats": 5},
                            ],
                        },
                        {
                            "name": "test",
                            "category": "test",
                            "required": False,
                            "conditions": [
                                {"simple": "tests_added == 0 || (coverage_percent >= 80 && tests_failed == 0)", "no": "test", "maxRepeats": 5},
                            ],
                        },
                        {"name": "docs", "category": "document", "required": True},
                    ],
                }
            ],
        }

        pipelines = load_pipelines(_write_pipelines(tmp_path, data))
        assert len(pipelines) == 1
        p = pipelines[0]
        assert p.name == "feature-pipeline"
        assert p.version == "2.0.0"
        assert len(p.stages) == 7

        # Check categories
        assert "analyze" in p.categories
        assert p.categories["analyze"]["type"] == "object"
        assert "review" in p.categories
        assert p.categories["review"]["required"] == ["severity", "recommendation"]

        # Check named stages
        assert p.stages[0].name == "analysis"
        assert p.stages[2].name == "implementation"
        assert p.stages[2].required is False
        assert len(p.stages[2].conditions) == 1
        assert p.stages[2].conditions[0]["ai"] == "All design.acceptance_criteria fulfilled?"

        # Check review stage conditions
        review = p.stages[3]
        assert review.name == "review"
        assert len(review.conditions) == 2
        assert review.conditions[1]["simple"] == "severity == major_issues || severity == blocking"
        assert review.conditions[1]["no"] == "test"

        # Check fix-review-findings with boolean simple
        fix_stage = p.stages[4]
        assert fix_stage.name == "fix-review-findings"
        assert fix_stage.conditions[0]["simple"] is True

        # Check test stage
        test_stage = p.stages[5]
        assert test_stage.name == "test"
        assert "coverage_percent >= 80" in test_stage.conditions[0]["simple"]

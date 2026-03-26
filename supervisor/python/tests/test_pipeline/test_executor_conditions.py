"""Tests for executor integration with structured conditions and output schema resolution.

Covers:
  - _get_output_schema_for_stage (pipeline categories vs agent fallback)
  - check_conditions with structured format (bridge function)
  - Stage execution loop with condition-driven jumps (mocked)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.models import PipelineConfig, PipelineTrigger, StageConfig
from aquarco_supervisor.pipeline.executor import (
    PipelineExecutor,
    check_conditions,
)
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# check_conditions bridge: structured format
# ---------------------------------------------------------------------------


class TestCheckConditionsStructured:
    """check_conditions() with structured condition dicts (new format)."""

    def test_structured_conditions_no_jump_returns_true(self) -> None:
        """Structured conditions with no jump target => True (proceed)."""
        conditions: list[dict[str, Any]] = [{"simple": "true"}]
        assert check_conditions(conditions, {"severity": "ok"}) is True

    def test_structured_conditions_with_jump_returns_false(self) -> None:
        """Structured conditions that produce a jump => False (don't proceed linearly)."""
        conditions: list[dict[str, Any]] = [
            {"simple": "severity == critical", "yes": "fix", "maxRepeats": 5}
        ]
        assert check_conditions(conditions, {"severity": "critical"}) is False

    def test_structured_conditions_false_with_no_jump(self) -> None:
        conditions: list[dict[str, Any]] = [
            {"simple": "severity == blocking", "no": "implementation"}
        ]
        # severity != blocking => False branch => jump to implementation
        assert check_conditions(conditions, {"severity": "minor"}) is False

    def test_structured_conditions_empty_list(self) -> None:
        assert check_conditions([], {}) is True

    def test_mixed_legacy_still_works(self) -> None:
        """Legacy string conditions still work."""
        assert check_conditions(["status == pass"], {"status": "pass"}) is True
        assert check_conditions(["status == pass"], {"status": "fail"}) is False


# ---------------------------------------------------------------------------
# _get_output_schema_for_stage
# ---------------------------------------------------------------------------


class TestOutputSchemaResolution:
    """Test output schema resolution from pipeline categories vs agent spec."""

    def _make_executor(
        self,
        pipelines: list[PipelineConfig] | None = None,
    ) -> PipelineExecutor:
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        mock_registry = MagicMock()
        mock_registry.get_agent_output_schema = MagicMock(return_value=None)

        executor = PipelineExecutor.__new__(PipelineExecutor)
        executor._db = mock_db
        executor._tq = mock_tq
        executor._registry = mock_registry
        executor._pipelines = pipelines or []
        return executor

    def test_resolves_from_pipeline_categories(self) -> None:
        """Schema resolved from pipeline categories when available."""
        categories = {
            "analyze": {"type": "object", "required": ["risks"]},
        }
        pipeline = PipelineConfig(
            name="feature-pipeline",
            trigger=PipelineTrigger(labels=["feature"]),
            stages=[StageConfig(name="analysis", category="analyze")],
            categories=categories,
        )
        executor = self._make_executor([pipeline])
        schema = executor._get_output_schema_for_stage(
            "feature-pipeline", "analyze", "analyze-agent"
        )
        assert schema is not None
        assert schema["type"] == "object"
        assert "risks" in schema["required"]

    def test_falls_back_to_agent_spec(self) -> None:
        """When pipeline has no categories for this category, fall back to agent spec."""
        pipeline = PipelineConfig(
            name="feature-pipeline",
            trigger=PipelineTrigger(labels=["feature"]),
            stages=[StageConfig(name="analysis", category="analyze")],
            categories={},
        )
        executor = self._make_executor([pipeline])
        executor._registry.get_agent_output_schema = MagicMock(
            return_value={"type": "object", "properties": {"from_agent": {}}}
        )
        schema = executor._get_output_schema_for_stage(
            "feature-pipeline", "analyze", "analyze-agent"
        )
        assert schema is not None
        assert "from_agent" in schema["properties"]
        executor._registry.get_agent_output_schema.assert_called_once_with("analyze-agent")

    def test_returns_none_when_no_schema(self) -> None:
        """No schema in categories or agent spec => None."""
        pipeline = PipelineConfig(
            name="feature-pipeline",
            trigger=PipelineTrigger(labels=["feature"]),
            stages=[StageConfig(name="analysis", category="analyze")],
            categories={},
        )
        executor = self._make_executor([pipeline])
        schema = executor._get_output_schema_for_stage(
            "feature-pipeline", "analyze", "analyze-agent"
        )
        assert schema is None

    def test_pipeline_not_found_falls_back(self) -> None:
        """When pipeline_name doesn't match, fall back to agent spec."""
        executor = self._make_executor([])
        executor._registry.get_agent_output_schema = MagicMock(return_value={"type": "string"})
        schema = executor._get_output_schema_for_stage(
            "nonexistent-pipeline", "analyze", "analyze-agent"
        )
        assert schema == {"type": "string"}

    def test_empty_schema_in_categories_falls_back(self) -> None:
        """Empty dict schema in categories => falls back to agent spec."""
        pipeline = PipelineConfig(
            name="test-pipeline",
            trigger=PipelineTrigger(labels=["test"]),
            stages=[StageConfig(name="s1", category="test")],
            categories={"test": {}},
        )
        executor = self._make_executor([pipeline])
        executor._registry.get_agent_output_schema = MagicMock(
            return_value={"type": "object"}
        )
        schema = executor._get_output_schema_for_stage(
            "test-pipeline", "test", "test-agent"
        )
        # Empty dict is falsy, so should fall back
        assert schema == {"type": "object"}

    def test_scoped_view_used_for_fallback(self) -> None:
        """When scoped_view is provided, use it for agent schema fallback."""
        pipeline = PipelineConfig(
            name="test-pipeline",
            trigger=PipelineTrigger(labels=["test"]),
            stages=[StageConfig(name="s1", category="test")],
            categories={},
        )
        executor = self._make_executor([pipeline])
        scoped_view = MagicMock()
        scoped_view.get_agent_output_schema = MagicMock(
            return_value={"type": "object", "from": "scoped"}
        )
        schema = executor._get_output_schema_for_stage(
            "test-pipeline", "test", "test-agent", scoped_view=scoped_view
        )
        assert schema is not None
        assert schema["from"] == "scoped"
        scoped_view.get_agent_output_schema.assert_called_once_with("test-agent")

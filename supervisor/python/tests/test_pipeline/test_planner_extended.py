"""Extended tests for pipeline.planner — planning phase execution and validation.

Covers:
- execute_planning_phase success path
- execute_planning_phase error handling (RetryableError, general Exception)
- Validation of planned_stages (empty, missing required categories)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aquarco_supervisor.exceptions import PipelineError, RetryableError
from aquarco_supervisor.pipeline.planner import PipelinePlanner


@pytest.fixture
def planner():
    tq = AsyncMock()
    sm = AsyncMock()
    sm.create_system_stage = AsyncMock(return_value="stage-id-1")
    sm.record_stage_executing = AsyncMock()
    sm.record_stage_failed = AsyncMock()
    sm.store_stage_output = AsyncMock()

    registry = MagicMock()
    registry.get_agents_for_category = MagicMock(return_value=["agent-1"])
    registry.get_all_agent_definitions_json = AsyncMock(return_value=[])

    invoker = AsyncMock()
    next_eo = MagicMock(return_value=1)

    return PipelinePlanner(tq, sm, registry, invoker, next_eo)


class TestExecutePlanningPhase:
    @pytest.mark.asyncio
    async def test_success_returns_planned_stages(self, planner):
        planned = [
            {"category": "analyze", "agents": ["analyze-agent"], "parallel": False, "validation": []},
        ]
        planner._invoker.execute_agent = AsyncMock(
            return_value={"planned_stages": planned}
        )
        result = await planner.execute_planning_phase(
            "task-1", "feature-pipeline",
            [{"category": "analyze", "required": True}],
            {},
        )
        assert result == planned
        planner._sm.create_system_stage.assert_awaited_once()
        planner._sm.record_stage_executing.assert_awaited_once()
        planner._sm.store_stage_output.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_planned_stages_raises(self, planner):
        planner._invoker.execute_agent = AsyncMock(
            return_value={"planned_stages": []}
        )
        with pytest.raises(PipelineError, match="empty planned_stages"):
            await planner.execute_planning_phase(
                "task-1", "feature-pipeline",
                [{"category": "analyze"}],
                {},
            )

    @pytest.mark.asyncio
    async def test_missing_required_category_raises(self, planner):
        planner._invoker.execute_agent = AsyncMock(
            return_value={"planned_stages": [
                {"category": "analyze", "agents": ["a"]},
            ]}
        )
        with pytest.raises(PipelineError, match="implement"):
            await planner.execute_planning_phase(
                "task-1", "feature-pipeline",
                [
                    {"category": "analyze", "required": True},
                    {"category": "implement", "required": True},
                ],
                {},
            )

    @pytest.mark.asyncio
    async def test_retryable_error_records_failure_and_reraises(self, planner):
        planner._invoker.execute_agent = AsyncMock(
            side_effect=RetryableError("rate limited")
        )
        with pytest.raises(RetryableError):
            await planner.execute_planning_phase(
                "task-1", "pipeline", [{"category": "analyze"}], {},
            )
        planner._sm.record_stage_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_general_exception_wraps_in_pipeline_error(self, planner):
        planner._invoker.execute_agent = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        with pytest.raises(PipelineError, match="Planning phase failed"):
            await planner.execute_planning_phase(
                "task-1", "pipeline", [{"category": "analyze"}], {},
            )
        planner._sm.record_stage_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_optional_category_not_required(self, planner):
        """Non-required categories don't need planned agents."""
        planner._invoker.execute_agent = AsyncMock(
            return_value={"planned_stages": [
                {"category": "analyze", "agents": ["a"]},
            ]}
        )
        # "docs" is not required, so missing from planned_stages is OK
        result = await planner.execute_planning_phase(
            "task-1", "pipeline",
            [
                {"category": "analyze", "required": True},
                {"category": "docs", "required": False},
            ],
            {},
        )
        assert len(result) == 1

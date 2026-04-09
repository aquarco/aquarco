"""Tests for pipeline.planner — extracted planning phase logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aquarco_supervisor.pipeline.planner import PipelinePlanner


@pytest.fixture
def planner():
    """Create a PipelinePlanner with mocked dependencies."""
    tq = MagicMock()
    sm = MagicMock()
    registry = MagicMock()
    invoker = MagicMock()
    next_eo = MagicMock(side_effect=lambda tid: 1)

    # Configure registry to return agents by category
    def get_agents(cat):
        return {
            "analyze": ["analyze-agent"],
            "design": ["design-agent"],
            "implement": ["implementation-agent"],
            "test": ["test-agent"],
            "review": ["review-agent"],
        }.get(cat, [])

    registry.get_agents_for_category = MagicMock(side_effect=get_agents)

    return PipelinePlanner(tq, sm, registry, invoker, next_eo)


class TestBuildDefaultPlan:
    def test_single_stage(self, planner):
        stages = [{"category": "analyze"}]
        plan = planner.build_default_plan(stages)
        assert len(plan) == 1
        assert plan[0]["category"] == "analyze"
        assert plan[0]["agents"] == ["analyze-agent"]
        assert plan[0]["parallel"] is False
        assert plan[0]["validation"] == []

    def test_multiple_stages(self, planner):
        stages = [
            {"category": "analyze"},
            {"category": "design"},
            {"category": "implement"},
        ]
        plan = planner.build_default_plan(stages)
        assert len(plan) == 3
        assert [s["category"] for s in plan] == ["analyze", "design", "implement"]
        assert [s["agents"] for s in plan] == [
            ["analyze-agent"],
            ["design-agent"],
            ["implementation-agent"],
        ]

    def test_empty_stages(self, planner):
        plan = planner.build_default_plan([])
        assert plan == []

    def test_unknown_category(self, planner):
        stages = [{"category": "unknown"}]
        plan = planner.build_default_plan(stages)
        assert plan[0]["agents"] == []

    def test_uses_only_first_agent(self, planner):
        """build_default_plan takes agents[:1] - only first agent."""
        planner._registry.get_agents_for_category = MagicMock(
            return_value=["agent-a", "agent-b", "agent-c"]
        )
        stages = [{"category": "test"}]
        plan = planner.build_default_plan(stages)
        assert plan[0]["agents"] == ["agent-a"]

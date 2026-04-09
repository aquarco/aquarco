"""Tests for pipeline.agent_invoker — Claude CLI invocation with max-turns continuation.

Covers:
- get_output_schema_for_stage resolution (pipeline categories > agent fallback)
- execute_agent max-turns continuation loop
- execute_agent cost accumulation
- execute_agent rate-limit detection via is_error flag
- execute_agent fallback to last successful output
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.cli.claude import ClaudeOutput
from aquarco_supervisor.database import Database
from aquarco_supervisor.exceptions import RateLimitError
from aquarco_supervisor.models import PipelineConfig
from aquarco_supervisor.pipeline.agent_invoker import AgentInvoker
from aquarco_supervisor.pipeline.agent_registry import AgentRegistry


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def mock_registry() -> MagicMock:
    r = MagicMock(spec=AgentRegistry)
    r.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    r.get_agent_timeout = MagicMock(return_value=30)
    r.get_agent_max_turns = MagicMock(return_value=20)
    r.get_agent_max_cost = MagicMock(return_value=5.0)
    r.get_agent_model = MagicMock(return_value=None)
    r.get_allowed_tools = MagicMock(return_value=[])
    r.get_denied_tools = MagicMock(return_value=[])
    r.get_agent_environment = MagicMock(return_value={})
    r.get_agent_output_schema = MagicMock(return_value=None)
    return r


@pytest.fixture
def invoker(mock_db, mock_registry, sample_pipelines):
    return AgentInvoker(mock_db, mock_registry, sample_pipelines)


# -----------------------------------------------------------------------
# get_output_schema_for_stage
# -----------------------------------------------------------------------


class TestGetOutputSchemaForStage:
    def test_returns_pipeline_category_schema(self, invoker, mock_registry):
        """Pipeline category schema takes precedence over agent-level schema."""
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
        with patch(
            "aquarco_supervisor.pipeline.agent_invoker.get_pipeline_categories",
            return_value={"analyze": schema},
        ):
            result = invoker.get_output_schema_for_stage(
                "feature-pipeline", "analyze", "analyze-agent",
            )
        assert result == schema
        mock_registry.get_agent_output_schema.assert_not_called()

    def test_falls_back_to_agent_schema(self, invoker, mock_registry):
        """When pipeline has no schema for category, fall back to agent."""
        agent_schema = {"type": "object"}
        mock_registry.get_agent_output_schema = MagicMock(return_value=agent_schema)
        with patch(
            "aquarco_supervisor.pipeline.agent_invoker.get_pipeline_categories",
            return_value={},
        ):
            result = invoker.get_output_schema_for_stage(
                "feature-pipeline", "analyze", "analyze-agent",
            )
        assert result == agent_schema

    def test_returns_none_when_no_schema(self, invoker, mock_registry):
        """Returns None when neither pipeline nor agent has a schema."""
        mock_registry.get_agent_output_schema = MagicMock(return_value=None)
        with patch(
            "aquarco_supervisor.pipeline.agent_invoker.get_pipeline_categories",
            return_value={},
        ):
            result = invoker.get_output_schema_for_stage(
                "feature-pipeline", "analyze", "analyze-agent",
            )
        assert result is None

    def test_skips_empty_pipeline_category(self, invoker, mock_registry):
        """When pipeline category exists but schema is None/empty, fall back."""
        agent_schema = {"type": "object"}
        mock_registry.get_agent_output_schema = MagicMock(return_value=agent_schema)
        with patch(
            "aquarco_supervisor.pipeline.agent_invoker.get_pipeline_categories",
            return_value={"analyze": None},
        ):
            result = invoker.get_output_schema_for_stage(
                "feature-pipeline", "analyze", "analyze-agent",
            )
        assert result == agent_schema


# -----------------------------------------------------------------------
# execute_agent — basic flow
# -----------------------------------------------------------------------


class TestExecuteAgent:
    @pytest.mark.asyncio
    async def test_basic_execution(self, invoker):
        """Normal single-iteration execution returns structured output."""
        claude_output = ClaudeOutput(
            structured={"summary": "done", "_subtype": "success"},
            raw="{}",
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=claude_output,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await invoker.execute_agent(
                "test-agent", "task-1", {"key": "val"}, 0,
                work_dir="/repos/test",
            )
        assert result["_agent_name"] == "test-agent"
        assert result["_iterations"] == 1
        assert result["summary"] == "done"

    @pytest.mark.asyncio
    async def test_requires_work_dir_or_resolver(self, invoker):
        """Raises ValueError when neither work_dir nor resolve_clone_dir provided."""
        with pytest.raises(ValueError, match="Either work_dir or resolve_clone_dir"):
            await invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
            )

    @pytest.mark.asyncio
    async def test_uses_resolve_clone_dir(self, invoker):
        """Uses resolve_clone_dir callback when work_dir not provided."""
        resolver = AsyncMock(return_value="/repos/resolved")
        claude_output = ClaudeOutput(structured={"_subtype": "success"}, raw="{}")
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=claude_output,
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            await invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                resolve_clone_dir=resolver,
            )
        resolver.assert_awaited_once_with("task-1")
        assert mock_exec.call_args.kwargs["work_dir"] == "/repos/resolved"


# -----------------------------------------------------------------------
# execute_agent — max-turns continuation
# -----------------------------------------------------------------------


class TestMaxTurnsContinuation:
    @pytest.mark.asyncio
    async def test_continues_on_max_turns(self, invoker):
        """When agent hits max_turns, continues with session_id."""
        first_output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_session_id": "sess-abc",
                "_cost_usd": 1.0,
            },
            raw="{}",
        )
        second_output = ClaudeOutput(
            structured={
                "_subtype": "success",
                "result": "final",
                "_cost_usd": 0.5,
            },
            raw="{}",
        )
        call_count = 0

        async def mock_execute(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_output
            return second_output

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            side_effect=mock_execute,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        assert result["_iterations"] == 2
        assert result["_cumulative_cost_usd"] == 1.5
        assert result["result"] == "final"

    @pytest.mark.asyncio
    async def test_stops_when_cost_exceeded(self, invoker, mock_registry):
        """Stops continuation when cumulative cost exceeds maxCost."""
        mock_registry.get_agent_max_cost = MagicMock(return_value=1.0)
        output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_session_id": "sess-xyz",
                "_cost_usd": 1.5,
            },
            raw="{}",
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=output,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        assert result["_iterations"] == 1  # Did not continue

    @pytest.mark.asyncio
    async def test_stops_when_no_session_id(self, invoker):
        """Stops continuation when max_turns output has no session_id."""
        output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_cost_usd": 0.5,
            },
            raw="{}",
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=output,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        assert result["_iterations"] == 1


# -----------------------------------------------------------------------
# execute_agent — fallback to last successful output
# -----------------------------------------------------------------------


class TestOutputFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_last_successful(self, invoker):
        """When final iteration has _no_structured_output, uses last successful."""
        first_output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_session_id": "sess-1",
                "_cost_usd": 1.0,
                "summary": "partial result",
            },
            raw="{}",
        )
        second_output = ClaudeOutput(
            structured={
                "_subtype": "success",
                "_no_structured_output": True,
                "_cost_usd": 0.5,
            },
            raw="{}",
        )
        call_count = 0

        async def mock_execute(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return first_output
            return second_output

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            side_effect=mock_execute,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        # Should fall back to first output's structured data
        assert result["summary"] == "partial result"
        assert result["_cumulative_cost_usd"] == 1.5


# -----------------------------------------------------------------------
# execute_agent — rate limit detection
# -----------------------------------------------------------------------


class TestRateLimitDetection:
    @pytest.mark.asyncio
    async def test_detects_rate_limit_in_raw_output(self, invoker):
        """Raises RateLimitError when is_error=True and rate_limit_event found."""
        rate_event = json.dumps({
            "type": "rate_limit_event",
            "rate_limit_info": {"resetsAt": "2026-04-08T12:00:00Z"},
        })
        output = ClaudeOutput(
            structured={
                "_subtype": "success",
                "_is_error": True,
                "_cost_usd": 0.5,
            },
            raw=f'{{"type":"assistant"}}\n{rate_event}',
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=output,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            with pytest.raises(RateLimitError, match="rate_limit_event"):
                await invoker.execute_agent(
                    "test-agent", "task-1", {}, 0,
                    work_dir="/repos/test",
                )


# -----------------------------------------------------------------------
# execute_agent — cost accumulation
# -----------------------------------------------------------------------


class TestCostAccumulation:
    @pytest.mark.asyncio
    async def test_accumulates_token_counts(self, invoker):
        """Cumulative token counts are tracked across iterations."""
        output = ClaudeOutput(
            structured={
                "_subtype": "success",
                "_cost_usd": 0.5,
                "_input_tokens": 1000,
                "_output_tokens": 500,
                "_cache_read_tokens": 200,
                "_cache_write_tokens": 100,
            },
            raw="{}",
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=output,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        assert result["_cumulative_cost_usd"] == 0.5
        assert result["_cumulative_input_tokens"] == 1000
        assert result["_cumulative_output_tokens"] == 500
        assert result["_cumulative_cache_read_tokens"] == 200
        assert result["_cumulative_cache_write_tokens"] == 100

    @pytest.mark.asyncio
    async def test_handles_missing_cost(self, invoker):
        """Handles output missing _cost_usd gracefully (defaults to 0)."""
        output = ClaudeOutput(
            structured={"_subtype": "success"},
            raw="{}",
        )
        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=output,
        ), patch("aquarco_supervisor.pipeline.executor.Path"):
            result = await invoker.execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
            )
        assert result["_cumulative_cost_usd"] == 0.0

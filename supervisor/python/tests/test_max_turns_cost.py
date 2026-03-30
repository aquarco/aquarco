"""Tests for configurable maxTurns/maxCost per agent and auto-resume on max_turns.

Covers:
- ScopedAgentView.get_agent_max_turns() and get_agent_max_cost()
- AgentRegistry.get_agent_max_turns() and get_agent_max_cost()
- PipelineExecutor auto-resume loop (cost guard, iteration guard, session_id missing)
- Last successful output preservation across resume iterations
- Raw outputs excluded from agent output (no bloat)
- Cost warning when _cost_usd is absent from output
- Claude CLI resume prompt includes structured output format reminder
- Claude CLI resume args construction (no --system-prompt-file, etc.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from aquarco_supervisor.cli.claude import ClaudeOutput
from aquarco_supervisor.config_overlay import ResolvedConfig, ScopedAgentView
from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.executor import PipelineExecutor
from aquarco_supervisor.task_queue import TaskQueue


# ---------------------------------------------------------------------------
# ScopedAgentView.get_agent_max_turns / get_agent_max_cost
# ---------------------------------------------------------------------------


class TestScopedAgentViewMaxTurnsCost:
    """Test new ScopedAgentView accessors for maxTurns and maxCost."""

    def _make_view(self, agents: dict[str, dict[str, Any]], tmp_path: Path) -> ScopedAgentView:
        resolved = ResolvedConfig(agents=agents, pipelines=[], prompt_dirs=[tmp_path])
        return ScopedAgentView(resolved)

    def test_get_agent_max_turns_explicit(self, tmp_path: Path) -> None:
        """Returns the configured maxTurns from spec.resources."""
        view = self._make_view(
            {"agent": {"spec": {"resources": {"maxTurns": 50}}}}, tmp_path
        )
        assert view.get_agent_max_turns("agent") == 50
        view.cleanup()

    def test_get_agent_max_turns_default(self, tmp_path: Path) -> None:
        """Returns default (30) when maxTurns is not set."""
        view = self._make_view({"agent": {"spec": {}}}, tmp_path)
        assert view.get_agent_max_turns("agent") == 30
        view.cleanup()

    def test_get_agent_max_turns_unknown_agent(self, tmp_path: Path) -> None:
        """Unknown agent gets default maxTurns."""
        view = self._make_view({}, tmp_path)
        assert view.get_agent_max_turns("nonexistent") == 30
        view.cleanup()

    def test_get_agent_max_cost_explicit(self, tmp_path: Path) -> None:
        """Returns the configured maxCost from spec.resources."""
        view = self._make_view(
            {"agent": {"spec": {"resources": {"maxCost": 10.0}}}}, tmp_path
        )
        assert view.get_agent_max_cost("agent") == 10.0
        view.cleanup()

    def test_get_agent_max_cost_default(self, tmp_path: Path) -> None:
        """Returns default ($5.0) when maxCost is not set."""
        view = self._make_view({"agent": {"spec": {}}}, tmp_path)
        assert view.get_agent_max_cost("agent") == 5.0
        view.cleanup()

    def test_get_agent_max_cost_unknown_agent(self, tmp_path: Path) -> None:
        """Unknown agent gets default maxCost."""
        view = self._make_view({}, tmp_path)
        assert view.get_agent_max_cost("nonexistent") == 5.0
        view.cleanup()

    def test_get_agent_max_turns_flat_spec(self, tmp_path: Path) -> None:
        """Works when agent dict has resources at top level (no nested spec)."""
        view = self._make_view(
            {"agent": {"resources": {"maxTurns": 100}}}, tmp_path
        )
        assert view.get_agent_max_turns("agent") == 100
        view.cleanup()

    def test_get_agent_max_cost_flat_spec(self, tmp_path: Path) -> None:
        """Works when agent dict has resources at top level (no nested spec)."""
        view = self._make_view(
            {"agent": {"resources": {"maxCost": 2.5}}}, tmp_path
        )
        assert view.get_agent_max_cost("agent") == 2.5
        view.cleanup()


# ---------------------------------------------------------------------------
# Helper: create a mock registry with all required methods
# ---------------------------------------------------------------------------


def _make_mock_registry(
    max_turns: int = 30,
    max_cost: float = 5.0,
) -> MagicMock:
    registry = MagicMock()
    registry.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
    registry.get_agent_timeout = MagicMock(return_value=30)
    registry.get_agent_max_turns = MagicMock(return_value=max_turns)
    registry.get_agent_max_cost = MagicMock(return_value=max_cost)
    registry.get_allowed_tools = MagicMock(return_value=[])
    registry.get_denied_tools = MagicMock(return_value=[])
    registry.get_agent_environment = MagicMock(return_value={})
    registry.get_agent_output_schema = MagicMock(return_value=None)
    return registry


# ---------------------------------------------------------------------------
# PipelineExecutor auto-resume loop tests
# ---------------------------------------------------------------------------


class TestExecutorAutoResume:
    """Tests for the auto-resume loop in _execute_agent."""

    @pytest.mark.asyncio
    async def test_normal_completion_no_resume(self, sample_pipelines: Any) -> None:
        """Agent completes normally without hitting max_turns — no resume."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        registry = _make_mock_registry()

        claude_output = ClaudeOutput(
            structured={"summary": "done", "_cost_usd": 0.5},
            raw='{"summary": "done"}',
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=claude_output,
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            output = await executor._execute_agent(
                "test-agent", "task-1", {}, 0, work_dir="/repos/test"
            )

        # Called exactly once (no resume)
        assert mock_exec.call_count == 1
        assert output["summary"] == "done"
        assert output["_iterations"] == 1

    @pytest.mark.asyncio
    async def test_resume_on_max_turns(self, sample_pipelines: Any) -> None:
        """Agent hits max_turns, gets resumed, then completes."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        registry = _make_mock_registry(max_cost=10.0)

        # First call: hits max_turns
        first_output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_session_id": "sess-123",
                "_cost_usd": 1.0,
            },
            raw="first raw",
        )
        # Second call: completes normally
        second_output = ClaudeOutput(
            structured={"summary": "completed", "_cost_usd": 0.5},
            raw="second raw",
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=[first_output, second_output],
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            output = await executor._execute_agent(
                "test-agent", "task-1", {}, 0, work_dir="/repos/test"
            )

        assert mock_exec.call_count == 2
        # Second call should use resume_session_id
        second_call_kwargs = mock_exec.call_args_list[1].kwargs
        assert second_call_kwargs["resume_session_id"] == "sess-123"
        assert output["summary"] == "completed"
        assert output["_iterations"] == 2
        assert output["_cumulative_cost_usd"] == 1.5

    @pytest.mark.asyncio
    async def test_cost_guard_stops_resume(self, sample_pipelines: Any) -> None:
        """Resume stops when cumulative cost exceeds maxCost."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        registry = _make_mock_registry(max_cost=2.0)

        # First call: hits max_turns, costs $1.5
        first_output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_session_id": "sess-1",
                "_cost_usd": 1.5,
            },
            raw="raw1",
        )
        # Second call: hits max_turns again, costs $1.0 (cumulative $2.5 > $2.0)
        second_output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_session_id": "sess-2",
                "_cost_usd": 1.0,
            },
            raw="raw2",
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=[first_output, second_output],
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            output = await executor._execute_agent(
                "test-agent", "task-1", {}, 0, work_dir="/repos/test"
            )

        # Stopped after 2 iterations because cumulative cost ($2.5) > max_cost ($2.0)
        assert mock_exec.call_count == 2
        assert output["_cumulative_cost_usd"] == 2.5
        assert output["_iterations"] == 2

    @pytest.mark.asyncio
    async def test_max_resume_iterations_guard(self, sample_pipelines: Any) -> None:
        """Resume stops after max_resume_iterations (10) to prevent infinite loops."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        # Very high max_cost so it doesn't interfere
        registry = _make_mock_registry(max_cost=1000.0)

        # All calls hit max_turns with zero cost (simulating broken cost reporting)
        max_turns_output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_session_id": "sess-loop",
                "_cost_usd": 0.0,
            },
            raw="raw",
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=max_turns_output,
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            output = await executor._execute_agent(
                "test-agent", "task-1", {}, 0, work_dir="/repos/test"
            )

        # Should stop at 10 iterations (max_resume_iterations)
        assert mock_exec.call_count == 10
        assert output["_iterations"] == 10

    @pytest.mark.asyncio
    async def test_no_session_id_stops_resume(self, sample_pipelines: Any) -> None:
        """When max_turns is hit but no session_id, resume loop breaks."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        registry = _make_mock_registry()

        output_no_session = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_cost_usd": 0.5,
                # No _session_id!
            },
            raw="raw",
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=output_no_session,
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            output = await executor._execute_agent(
                "test-agent", "task-1", {}, 0, work_dir="/repos/test"
            )

        # Only one call — no resume possible without session_id
        assert mock_exec.call_count == 1
        assert output["_iterations"] == 1

    @pytest.mark.asyncio
    async def test_last_successful_output_preserved(self, sample_pipelines: Any) -> None:
        """If final iteration has no structured output, falls back to last good one."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        registry = _make_mock_registry(max_cost=10.0)

        # First call: has structured output but hits max_turns
        first_output = ClaudeOutput(
            structured={
                "summary": "partial",
                "_subtype": "error_max_turns",
                "_session_id": "sess-1",
                "_cost_usd": 1.0,
            },
            raw="raw1",
        )
        # Second call: no structured output (just metadata)
        second_output = ClaudeOutput(
            structured={
                "_no_structured_output": True,
                "_cost_usd": 0.5,
            },
            raw="raw2",
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=[first_output, second_output],
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            output = await executor._execute_agent(
                "test-agent", "task-1", {}, 0, work_dir="/repos/test"
            )

        # Should fall back to the first iteration's structured output
        assert output["summary"] == "partial"
        assert output["_cumulative_cost_usd"] == 1.5

    @pytest.mark.asyncio
    async def test_no_raw_outputs_in_result(self, sample_pipelines: Any) -> None:
        """Raw outputs are not included in agent output (removed to reduce bloat)."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        registry = _make_mock_registry(max_cost=10.0)

        first_output = ClaudeOutput(
            structured={
                "_subtype": "error_max_turns",
                "_session_id": "sess-1",
                "_cost_usd": 1.0,
            },
            raw="first raw output",
        )
        second_output = ClaudeOutput(
            structured={"done": True, "_cost_usd": 0.5},
            raw="second raw output",
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            side_effect=[first_output, second_output],
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            output = await executor._execute_agent(
                "test-agent", "task-1", {}, 0, work_dir="/repos/test"
            )

        assert "_raw_outputs_all" not in output
        # _raw_output is now intentionally set for DB storage
        assert "_raw_output" in output

    @pytest.mark.asyncio
    async def test_max_turns_passed_to_execute_claude(self, sample_pipelines: Any) -> None:
        """maxTurns from registry is passed through to execute_claude."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        registry = _make_mock_registry(max_turns=50)

        claude_output = ClaudeOutput(
            structured={"ok": True, "_cost_usd": 0.1},
            raw="{}",
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=claude_output,
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            await executor._execute_agent(
                "test-agent", "task-1", {}, 0, work_dir="/repos/test"
            )

        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs["max_turns"] == 50

    @pytest.mark.asyncio
    async def test_scoped_view_max_turns_and_cost_used(self, sample_pipelines: Any) -> None:
        """When scoped_view is provided, its max_turns and max_cost are used."""
        mock_db = AsyncMock(spec=Database)
        mock_tq = AsyncMock(spec=TaskQueue)
        registry = _make_mock_registry()  # defaults: 30 turns, $5.0

        scoped_view = MagicMock()
        scoped_view.get_agent_prompt_file = MagicMock(return_value="/prompts/test.md")
        scoped_view.get_agent_timeout = MagicMock(return_value=30)
        scoped_view.get_agent_max_turns = MagicMock(return_value=75)
        scoped_view.get_agent_max_cost = MagicMock(return_value=15.0)
        scoped_view.get_allowed_tools = MagicMock(return_value=[])
        scoped_view.get_denied_tools = MagicMock(return_value=[])
        scoped_view.get_agent_environment = MagicMock(return_value={})
        scoped_view.get_agent_output_schema = MagicMock(return_value=None)

        claude_output = ClaudeOutput(
            structured={"ok": True, "_cost_usd": 0.1},
            raw="{}",
        )

        executor = PipelineExecutor(mock_db, mock_tq, registry, sample_pipelines)

        with patch(
            "aquarco_supervisor.pipeline.executor.execute_claude",
            new_callable=AsyncMock,
            return_value=claude_output,
        ) as mock_exec, patch("aquarco_supervisor.pipeline.executor.Path"):
            await executor._execute_agent(
                "test-agent", "task-1", {}, 0,
                work_dir="/repos/test",
                scoped_view=scoped_view,
            )

        # Verify scoped_view methods were called (not registry)
        scoped_view.get_agent_max_turns.assert_called_once_with("test-agent")
        scoped_view.get_agent_max_cost.assert_called_once_with("test-agent")
        registry.get_agent_max_turns.assert_not_called()
        registry.get_agent_max_cost.assert_not_called()

        # Verify max_turns was passed through
        call_kwargs = mock_exec.call_args.kwargs
        assert call_kwargs["max_turns"] == 75


# ---------------------------------------------------------------------------
# AgentRegistry.get_agent_max_turns / get_agent_max_cost
# ---------------------------------------------------------------------------


class TestAgentRegistryMaxTurnsCost:
    """Test AgentRegistry accessors for maxTurns and maxCost."""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> Any:
        from aquarco_supervisor.pipeline.agent_registry import AgentRegistry

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()

        db = AsyncMock(spec=Database)
        reg = AgentRegistry(db, str(agents_dir), str(prompts_dir))

        # Directly set internal _agents dict to avoid needing file loading
        reg._agents = {
            "fast-agent": {"resources": {"maxTurns": 15, "maxCost": 2.0}},
            "default-agent": {},
        }
        return reg

    def test_max_turns_explicit(self, registry: Any) -> None:
        assert registry.get_agent_max_turns("fast-agent") == 15

    def test_max_turns_default(self, registry: Any) -> None:
        assert registry.get_agent_max_turns("default-agent") == 30

    def test_max_turns_unknown_agent(self, registry: Any) -> None:
        assert registry.get_agent_max_turns("nonexistent") == 30

    def test_max_cost_explicit(self, registry: Any) -> None:
        assert registry.get_agent_max_cost("fast-agent") == 2.0

    def test_max_cost_default(self, registry: Any) -> None:
        assert registry.get_agent_max_cost("default-agent") == 5.0

    def test_max_cost_unknown_agent(self, registry: Any) -> None:
        assert registry.get_agent_max_cost("nonexistent") == 5.0


# ---------------------------------------------------------------------------
# Claude CLI resume prompt
# ---------------------------------------------------------------------------


class TestClaudeResumePrompt:
    """Test that resume invocation has the right prompt and args."""

    @pytest.mark.asyncio
    async def test_resume_prompt_mentions_structured_output(self) -> None:
        """The resume continuation prompt mentions structured output format."""
        from aquarco_supervisor.cli.claude import execute_claude

        prompt_file = Path("/tmp/test-prompt.md")

        with patch("pathlib.Path.exists", return_value=True), \
             patch("tempfile.mkstemp", return_value=(99, "/tmp/ctx.json")), \
             patch("os.fdopen") as mock_fdopen, \
             patch("asyncio.create_subprocess_exec") as mock_proc_create, \
             patch("pathlib.Path.unlink"), \
             patch("pathlib.Path.mkdir"), \
             patch("builtins.open", create=True) as mock_open:

            # Capture what gets written to the context file
            written_content = []
            mock_file = MagicMock()
            mock_file.write = lambda s: written_content.append(s)
            mock_file.__enter__ = lambda self: self
            mock_file.__exit__ = lambda *args: None
            mock_fdopen.return_value = mock_file

            # Make process fail so we don't need full setup
            mock_process = AsyncMock()
            mock_process.returncode = 1

            # Provide an async-iterable stdout (stream-json mode requires this)
            class _EmptyReader:
                def __aiter__(self) -> "_EmptyReader":
                    return self

                async def __anext__(self) -> bytes:
                    raise StopAsyncIteration

            mock_process.stdout = _EmptyReader()
            mock_process.kill = AsyncMock()
            mock_process.wait = AsyncMock()
            mock_proc_create.return_value = mock_process

            mock_open_file = MagicMock()
            mock_open_file.__enter__ = lambda self: self
            mock_open_file.__exit__ = lambda *args: None
            mock_open.return_value = mock_open_file

            try:
                await execute_claude(
                    prompt_file=prompt_file,
                    context={},
                    work_dir="/tmp/work",
                    resume_session_id="sess-abc",
                )
            except Exception:
                pass  # We expect an error since returncode=1

            # Verify the resume prompt mentions structured output
            full_content = "".join(written_content)
            assert "structured" in full_content.lower()
            assert "output format" in full_content.lower()

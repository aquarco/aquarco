"""Tests for stage iteration tracking, _should_iterate, and _MAX_ITERATIONS.

Covers:
- _should_iterate boundary conditions (0, 1, _MAX_ITERATIONS)
- Stage iteration counter behaviour during sequential execution
- Repeat counts and iteration stage creation
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aquarco_supervisor.database import Database
from aquarco_supervisor.pipeline.executor import PipelineExecutor, _MAX_ITERATIONS
from aquarco_supervisor.task_queue import TaskQueue


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=Database)


@pytest.fixture
def mock_tq() -> AsyncMock:
    return AsyncMock(spec=TaskQueue)


@pytest.fixture
def mock_registry() -> MagicMock:
    registry = MagicMock()
    registry.get_default_agents.return_value = {}
    registry.get_default_prompts_dir.return_value = "/prompts"
    return registry


@pytest.fixture
def executor(
    mock_db: AsyncMock, mock_tq: AsyncMock, mock_registry: MagicMock
) -> PipelineExecutor:
    return PipelineExecutor(mock_db, mock_tq, mock_registry, [])


# ---------------------------------------------------------------------------
# _should_iterate boundary tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_should_iterate_returns_false_at_max_iterations(
    executor: PipelineExecutor, mock_tq: AsyncMock
) -> None:
    """At _MAX_ITERATIONS, iteration is capped regardless of open items."""
    result = await executor._should_iterate("task-1", "review", _MAX_ITERATIONS)
    assert result is False
    # Should not even check for validation items
    mock_tq.get_open_validation_items.assert_not_called()


@pytest.mark.asyncio
async def test_should_iterate_returns_false_above_max_iterations(
    executor: PipelineExecutor, mock_tq: AsyncMock
) -> None:
    """Above _MAX_ITERATIONS, iteration is capped."""
    result = await executor._should_iterate("task-1", "review", _MAX_ITERATIONS + 1)
    assert result is False


@pytest.mark.asyncio
async def test_should_iterate_returns_true_with_open_items(
    executor: PipelineExecutor, mock_tq: AsyncMock
) -> None:
    """Returns True when there are open validation items and under max iterations."""
    mock_vi = MagicMock()
    mock_vi.id = 1
    mock_vi.description = "Fix bug"
    mock_tq.get_open_validation_items = AsyncMock(return_value=[mock_vi])

    result = await executor._should_iterate("task-1", "review", 1)
    assert result is True
    mock_tq.get_open_validation_items.assert_called_once_with("task-1", "review")


@pytest.mark.asyncio
async def test_should_iterate_returns_false_with_no_open_items(
    executor: PipelineExecutor, mock_tq: AsyncMock
) -> None:
    """Returns False when there are no open validation items."""
    mock_tq.get_open_validation_items = AsyncMock(return_value=[])

    result = await executor._should_iterate("task-1", "test", 1)
    assert result is False


@pytest.mark.asyncio
async def test_should_iterate_at_iteration_zero(
    executor: PipelineExecutor, mock_tq: AsyncMock
) -> None:
    """Iteration 0 is below max, delegates to validation items check."""
    mock_tq.get_open_validation_items = AsyncMock(return_value=[])

    result = await executor._should_iterate("task-1", "review", 0)
    assert result is False
    mock_tq.get_open_validation_items.assert_called_once()


@pytest.mark.asyncio
async def test_should_iterate_at_one_below_max(
    executor: PipelineExecutor, mock_tq: AsyncMock
) -> None:
    """At _MAX_ITERATIONS - 1, iteration is still allowed if items exist."""
    mock_vi = MagicMock()
    mock_tq.get_open_validation_items = AsyncMock(return_value=[mock_vi])

    result = await executor._should_iterate("task-1", "impl", _MAX_ITERATIONS - 1)
    assert result is True


# ---------------------------------------------------------------------------
# _MAX_ITERATIONS constant
# ---------------------------------------------------------------------------


def test_max_iterations_is_positive() -> None:
    """_MAX_ITERATIONS must be a positive integer."""
    assert _MAX_ITERATIONS > 0
    assert isinstance(_MAX_ITERATIONS, int)


def test_max_iterations_value() -> None:
    """_MAX_ITERATIONS is set to 5 as documented."""
    assert _MAX_ITERATIONS == 5

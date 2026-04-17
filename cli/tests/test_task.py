"""Tests for the shared follow_task helper in task.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import click.exceptions
import httpx
import pytest
import typer

from aquarco_cli.task import follow_task


def _make_client(side_effects):
    """Create a mock GraphQLClient with the given side_effect sequence."""
    client = MagicMock()
    client.execute.side_effect = side_effects
    return client


class TestFollowTaskTerminal:
    """follow_task stops when on_poll returns True."""

    @patch("aquarco_cli.task.time.sleep")
    def test_stops_on_callback_true(self, mock_sleep):
        client = _make_client([
            {"pipelineStatus": {"taskId": "1", "status": "COMPLETED", "stages": []}},
        ])
        callback = MagicMock(return_value=True)

        follow_task(client, "1", callback)

        callback.assert_called_once()
        ps = callback.call_args[0][0]
        assert ps["status"] == "COMPLETED"

    @patch("aquarco_cli.task.time.sleep")
    def test_polls_until_callback_returns_true(self, mock_sleep):
        client = _make_client([
            {"pipelineStatus": {"taskId": "1", "status": "EXECUTING", "stages": []}},
            {"pipelineStatus": {"taskId": "1", "status": "EXECUTING", "stages": []}},
            {"pipelineStatus": {"taskId": "1", "status": "COMPLETED", "stages": []}},
        ])
        # Return True only when COMPLETED
        callback = MagicMock(side_effect=[False, False, True])

        follow_task(client, "1", callback)

        assert callback.call_count == 3


class TestFollowTaskSafetyNet:
    """follow_task stops on terminal status even if on_poll doesn't catch it."""

    @patch("aquarco_cli.task.time.sleep")
    def test_safety_net_stops_on_terminal_status(self, mock_sleep):
        client = _make_client([
            {"pipelineStatus": {"taskId": "1", "status": "FAILED", "stages": []}},
        ])
        # Callback never returns True
        callback = MagicMock(return_value=False)

        follow_task(client, "1", callback)

        callback.assert_called_once()

    @patch("aquarco_cli.task.time.sleep")
    def test_safety_net_cancelled_status(self, mock_sleep):
        """CANCELLED is a terminal status and should stop polling."""
        client = _make_client([
            {"pipelineStatus": {"taskId": "1", "status": "CANCELLED", "stages": []}},
        ])
        callback = MagicMock(return_value=False)

        follow_task(client, "1", callback)

        callback.assert_called_once()

    @patch("aquarco_cli.task.time.sleep")
    def test_safety_net_timeout_status(self, mock_sleep):
        client = _make_client([
            {"pipelineStatus": {"taskId": "1", "status": "TIMEOUT", "stages": []}},
        ])
        callback = MagicMock(return_value=False)

        follow_task(client, "1", callback)

        callback.assert_called_once()

    @patch("aquarco_cli.task.time.sleep")
    def test_safety_net_closed_status(self, mock_sleep):
        client = _make_client([
            {"pipelineStatus": {"taskId": "1", "status": "CLOSED", "stages": []}},
        ])
        callback = MagicMock(return_value=False)

        follow_task(client, "1", callback)

        callback.assert_called_once()


class TestFollowTaskErrors:
    """Error handling in follow_task."""

    @patch("aquarco_cli.task.time.sleep")
    def test_connection_error_exits(self, mock_sleep):
        client = _make_client([httpx.ConnectError("Connection refused")])

        with pytest.raises(click.exceptions.Exit):
            follow_task(client, "1", MagicMock())

    @patch("aquarco_cli.task.time.sleep")
    def test_timeout_error_exits(self, mock_sleep):
        client = _make_client([httpx.TimeoutException("timeout")])

        with pytest.raises(click.exceptions.Exit):
            follow_task(client, "1", MagicMock())

    @patch("aquarco_cli.task.time.sleep")
    def test_circuit_breaker_after_max_errors(self, mock_sleep):
        """5 consecutive poll errors triggers circuit breaker."""
        client = _make_client([
            RuntimeError("fail 1"),
            RuntimeError("fail 2"),
            RuntimeError("fail 3"),
            RuntimeError("fail 4"),
            RuntimeError("fail 5"),
        ])

        with pytest.raises(click.exceptions.Exit):
            follow_task(client, "1", MagicMock())

    @patch("aquarco_cli.task.time.sleep")
    def test_error_counter_resets_on_success(self, mock_sleep):
        """Successful poll resets the consecutive error counter."""
        client = _make_client([
            RuntimeError("fail 1"),
            RuntimeError("fail 2"),
            # Success resets counter
            {"pipelineStatus": {"taskId": "1", "status": "EXECUTING", "stages": []}},
            RuntimeError("fail 3"),
            RuntimeError("fail 4"),
            # Success again
            {"pipelineStatus": {"taskId": "1", "status": "COMPLETED", "stages": []}},
        ])
        callback = MagicMock(side_effect=[False, False])

        follow_task(client, "1", callback)

        assert callback.call_count == 2


class TestFollowTaskNullPipelineStatus:
    """Handle null pipelineStatus gracefully."""

    @patch("aquarco_cli.task.time.sleep")
    def test_null_pipeline_status_continues_polling(self, mock_sleep):
        client = _make_client([
            {"pipelineStatus": None},
            {"pipelineStatus": {"taskId": "1", "status": "COMPLETED", "stages": []}},
        ])
        callback = MagicMock(return_value=True)

        follow_task(client, "1", callback)

        # Callback only called for non-null pipelineStatus
        callback.assert_called_once()


class TestFollowTaskKeyboardInterrupt:
    """KeyboardInterrupt is handled gracefully."""

    @patch("aquarco_cli.task.time.sleep")
    def test_keyboard_interrupt_stops_gracefully(self, mock_sleep):
        client = MagicMock()
        client.execute.side_effect = KeyboardInterrupt()

        # Should not raise
        follow_task(client, "1", MagicMock())

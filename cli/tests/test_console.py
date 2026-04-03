"""Tests for shared console helpers."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from aquarco_cli.console import handle_api_error, make_table, print_error, print_info, print_success, print_warning


class TestHandleApiError:
    """Tests for the centralized API error handler."""

    @patch("aquarco_cli.console.print_error")
    def test_connect_error_shows_friendly_message(self, mock_print):
        exc = httpx.ConnectError("Connection refused")
        handle_api_error(exc)
        mock_print.assert_called_once()
        msg = mock_print.call_args[0][0]
        assert "Cannot reach" in msg
        assert "aquarco init" in msg

    @patch("aquarco_cli.console.print_error")
    def test_timeout_error_shows_friendly_message(self, mock_print):
        exc = httpx.TimeoutException("timed out")
        handle_api_error(exc)
        mock_print.assert_called_once()
        msg = mock_print.call_args[0][0]
        assert "Cannot reach" in msg

    @patch("aquarco_cli.console.print_error")
    def test_generic_error_shows_str(self, mock_print):
        exc = ValueError("something broke")
        handle_api_error(exc)
        mock_print.assert_called_once_with("something broke")

    @patch("aquarco_cli.console.print_error")
    def test_read_timeout_shows_friendly_message(self, mock_print):
        exc = httpx.ReadTimeout("read timed out")
        handle_api_error(exc)
        msg = mock_print.call_args[0][0]
        assert "Cannot reach" in msg


class TestMakeTable:
    def test_creates_table_with_columns(self):
        table = make_table("Test Title", [("Name", "cyan"), ("Value", "")])
        assert table.title == "Test Title"
        assert len(table.columns) == 2

    def test_creates_table_with_no_columns(self):
        table = make_table("Empty", [])
        assert table.title == "Empty"
        assert len(table.columns) == 0


class TestPrintHelpers:
    @patch("aquarco_cli.console.console")
    def test_print_success(self, mock_console):
        print_success("done")
        mock_console.print.assert_called_once()
        assert "done" in mock_console.print.call_args[0][0]

    @patch("aquarco_cli.console.err_console")
    def test_print_error(self, mock_console):
        print_error("oops")
        mock_console.print.assert_called_once()
        assert "oops" in mock_console.print.call_args[0][0]

    @patch("aquarco_cli.console.err_console")
    def test_print_warning(self, mock_console):
        print_warning("careful")
        mock_console.print.assert_called_once()
        assert "careful" in mock_console.print.call_args[0][0]

    @patch("aquarco_cli.console.console")
    def test_print_info(self, mock_console):
        print_info("fyi")
        mock_console.print.assert_called_once()
        assert "fyi" in mock_console.print.call_args[0][0]

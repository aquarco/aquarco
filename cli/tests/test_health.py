"""Tests for health probes."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from aquarco_cli.health import _check_http, _check_tcp


class TestHealthProbes:
    @patch("aquarco_cli.health.httpx.get")
    def test_check_http_healthy(self, mock_get):
        mock_get.return_value = httpx.Response(200)
        result = _check_http("test", "http://localhost:8080", 8080)
        assert result.healthy is True
        assert "200" in result.detail

    @patch("aquarco_cli.health.httpx.get")
    def test_check_http_connection_refused(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("Connection refused")
        result = _check_http("test", "http://localhost:8080", 8080)
        assert result.healthy is False
        assert "Connection refused" in result.detail

    @patch("aquarco_cli.health.socket.create_connection")
    def test_check_tcp_open(self, mock_conn):
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = lambda s, *a: None
        result = _check_tcp("test", "localhost", 15432)
        assert result.healthy is True

    @patch("aquarco_cli.health.socket.create_connection")
    def test_check_tcp_refused(self, mock_conn):
        mock_conn.side_effect = OSError("Connection refused")
        result = _check_tcp("test", "localhost", 15432)
        assert result.healthy is False

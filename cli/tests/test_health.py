"""Tests for health probes."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from aquarco_cli.health import ServiceHealth, _check_http, _check_tcp, check_stack_health, print_health_table


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

    @patch("aquarco_cli.health.httpx.get")
    def test_check_http_server_error(self, mock_get):
        mock_get.return_value = httpx.Response(500)
        result = _check_http("test", "http://localhost:8080", 8080)
        assert result.healthy is False
        assert "500" in result.detail

    @patch("aquarco_cli.health.httpx.get")
    def test_check_http_400_is_healthy(self, mock_get):
        """A 400 means the server is reachable (expected for GET on GraphQL)."""
        mock_get.return_value = httpx.Response(400)
        result = _check_http("test", "http://localhost:8080/api/graphql", 8080)
        assert result.healthy is True
        assert "400" in result.detail

    @patch("aquarco_cli.health.httpx.get")
    def test_check_http_generic_exception(self, mock_get):
        mock_get.side_effect = RuntimeError("unexpected")
        result = _check_http("test", "http://localhost:8080", 8080)
        assert result.healthy is False
        assert "unexpected" in result.detail


class TestPrintHealthTable:
    def test_all_healthy_returns_true(self):
        services = [
            ServiceHealth(name="web", port=8080, healthy=True, detail="HTTP 200"),
            ServiceHealth(name="db", port=15432, healthy=True, detail="TCP open"),
        ]
        assert print_health_table(services) is True

    def test_unhealthy_returns_false(self):
        services = [
            ServiceHealth(name="web", port=8080, healthy=True, detail="HTTP 200"),
            ServiceHealth(name="api", port=4000, healthy=False, detail="Connection refused"),
        ]
        assert print_health_table(services) is False

    def test_empty_services_returns_true(self):
        assert print_health_table([]) is True


class TestCheckStackHealth:
    @patch("aquarco_cli.health._check_tcp")
    @patch("aquarco_cli.health._check_http")
    def test_check_stack_health_returns_three_services(self, mock_http, mock_tcp):
        mock_http.return_value = ServiceHealth("http", 8080, True)
        mock_tcp.return_value = ServiceHealth("tcp", 15432, True)
        results = check_stack_health()
        assert len(results) == 3
        assert mock_http.call_count == 2  # Web + API
        assert mock_tcp.call_count == 1   # PostgreSQL

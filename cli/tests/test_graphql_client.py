"""Tests for the GraphQL client."""

from __future__ import annotations

import httpx
import pytest
import respx

from aquarco_cli.graphql_client import GraphQLClient, GraphQLError


API_URL = "http://localhost:8080/api/graphql"


class TestGraphQLClient:
    def setup_method(self):
        self.client = GraphQLClient(url=API_URL, timeout=5)

    @respx.mock
    def test_execute_success(self):
        respx.post(API_URL).respond(
            json={"data": {"dashboardStats": {"totalTasks": 42}}}
        )
        result = self.client.execute("query { dashboardStats { totalTasks } }")
        assert result == {"dashboardStats": {"totalTasks": 42}}

    @respx.mock
    def test_execute_graphql_error(self):
        respx.post(API_URL).respond(
            json={"errors": [{"message": "Not found"}], "data": None}
        )
        with pytest.raises(GraphQLError, match="Not found"):
            self.client.execute("query { task(id: 999) { id } }")

    @respx.mock
    def test_execute_http_error(self):
        respx.post(API_URL).respond(status_code=500)
        with pytest.raises(httpx.HTTPStatusError):
            self.client.execute("query { dashboardStats { totalTasks } }")

    @respx.mock
    def test_execute_with_variables(self):
        route = respx.post(API_URL).respond(
            json={"data": {"task": {"id": "1", "title": "Test"}}}
        )
        result = self.client.execute(
            "query Task($id: ID!) { task(id: $id) { id title } }",
            variables={"id": "1"},
        )
        assert result["task"]["id"] == "1"
        # Verify variables were sent in the request body
        body = route.calls[0].request.content
        import json
        payload = json.loads(body)
        assert payload["variables"] == {"id": "1"}

    @respx.mock
    def test_execute_connection_error(self):
        respx.post(API_URL).mock(side_effect=httpx.ConnectError("Connection refused"))
        with pytest.raises(httpx.ConnectError):
            self.client.execute("query { dashboardStats { totalTasks } }")

    @respx.mock
    def test_execute_empty_data(self):
        """When 'data' key is missing, return empty dict."""
        respx.post(API_URL).respond(json={})
        result = self.client.execute("query { something }")
        assert result == {}

    @respx.mock
    def test_execute_no_variables_omits_key(self):
        route = respx.post(API_URL).respond(json={"data": {"ok": True}})
        self.client.execute("query { ok }")
        import json
        payload = json.loads(route.calls[0].request.content)
        assert "variables" not in payload


class TestGraphQLError:
    def test_error_message_extraction(self):
        errors = [{"message": "error one"}, {"message": "error two"}]
        exc = GraphQLError(errors)
        assert "error one" in str(exc)
        assert "error two" in str(exc)
        assert exc.errors == errors

    def test_error_without_message_key(self):
        errors = [{"code": "INTERNAL"}]
        exc = GraphQLError(errors)
        assert "INTERNAL" in str(exc)


class TestTerminalStatuses:
    def test_terminal_statuses_contains_expected_values(self):
        from aquarco_cli.graphql_client import TERMINAL_STATUSES
        assert "COMPLETED" in TERMINAL_STATUSES
        assert "FAILED" in TERMINAL_STATUSES
        assert "TIMEOUT" in TERMINAL_STATUSES
        assert "CLOSED" in TERMINAL_STATUSES
        assert "PENDING" not in TERMINAL_STATUSES
        assert "EXECUTING" not in TERMINAL_STATUSES

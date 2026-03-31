"""Shared test fixtures."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aquarco_cli.graphql_client import GraphQLClient
from aquarco_cli.vagrant import VagrantHelper


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_gql(mocker):
    """Return a mock GraphQLClient whose .execute() can be configured per test."""
    mock = mocker.MagicMock(spec=GraphQLClient)
    mocker.patch("aquarco_cli.graphql_client.GraphQLClient", return_value=mock)
    return mock


@pytest.fixture
def mock_vagrant(mocker):
    """Return a mock VagrantHelper."""
    mock = mocker.MagicMock(spec=VagrantHelper)
    mock.is_running.return_value = True
    mock.vagrant_dir = "/fake/vagrant"
    mocker.patch("aquarco_cli.vagrant.VagrantHelper", return_value=mock)
    return mock

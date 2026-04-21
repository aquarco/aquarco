"""Structural security tests for compose.prod.yml.

Validates review findings:
- Adminer (database admin UI) must NOT be in production Docker Compose.
- Only essential services should be present in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """Return the aquarco project root directory."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _load_compose(relative_path: str) -> dict:
    """Load a Docker Compose YAML file."""
    path = _project_root() / relative_path
    content = path.read_text()
    return yaml.safe_load(content)


# ===========================================================================
# Adminer removal from production
# ===========================================================================


class TestAdminerRemovedFromProd:
    """Adminer was flagged by the review agent as a security risk in prod.

    The implementation agent removed it.  These tests guard against regression.
    """

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    @pytest.fixture
    def compose_dev(self) -> dict:
        return _load_compose("docker/compose.dev.yml")

    def test_adminer_not_in_prod_services(self, compose_prod: dict) -> None:
        """Adminer must not be a service in compose.prod.yml."""
        services = compose_prod.get("services", {})
        assert "adminer" not in services, (
            "Adminer was removed from production compose because it exposes "
            "the full PostgreSQL database without authentication.  Do not add it back."
        )

    def test_adminer_still_in_dev_services(self, compose_dev: dict) -> None:
        """Adminer should still be available in the dev compose for developer convenience."""
        services = compose_dev.get("services", {})
        assert "adminer" in services, (
            "Adminer should remain in compose.dev.yml for local development."
        )

    def test_prod_compose_no_adminer_references(self, compose_prod: dict) -> None:
        """No service in prod compose should depend on adminer."""
        raw_text = (_project_root() / "docker/compose.prod.yml").read_text()
        # The word 'adminer' may appear in a comment (that's OK),
        # but should not appear as a service key or dependency.
        services = compose_prod.get("services", {})
        for svc_name, svc_def in services.items():
            depends = svc_def.get("depends_on", {})
            if isinstance(depends, list):
                assert "adminer" not in depends, (
                    f"Service '{svc_name}' depends_on adminer in prod compose"
                )
            elif isinstance(depends, dict):
                assert "adminer" not in depends, (
                    f"Service '{svc_name}' depends_on adminer in prod compose"
                )


# ===========================================================================
# Production compose — essential services only
# ===========================================================================


class TestProdComposeEssentialServicesOnly:
    """Production compose should contain only the services needed to run."""

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    # Dev-only services that must never be in production
    DEV_ONLY_SERVICES = frozenset({"adminer"})

    def test_no_dev_only_services_in_prod(self, compose_prod: dict) -> None:
        """Dev-only services must not leak into production compose."""
        services = set(compose_prod.get("services", {}).keys())
        leaked = services & self.DEV_ONLY_SERVICES
        assert not leaked, (
            f"Dev-only services found in compose.prod.yml: {leaked}. "
            "These services are not secured for production use."
        )

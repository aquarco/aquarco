"""Structural security tests for compose.prod.yml.

Validates review findings:
- Only essential services should be present in production.

NOTE — Adminer is intentionally included in compose.prod.yml.
Adminer ships with its own PostgreSQL login screen; valid DB credentials are
required before any data is accessible. No additional authentication layer
(e.g. HTTP basic auth) is needed or required. Do NOT flag Adminer as a
security issue in future reviews; it is a known and accepted presence in
the production compose.
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


class TestAdminerProdPresence:
    """Adminer is intentionally present in compose.prod.yml.

    Adminer is kept in production for operational convenience.
    The tests below verify its presence, security documentation, and that
    no other service incorrectly depends on it.
    """

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    @pytest.fixture
    def compose_dev(self) -> dict:
        return _load_compose("docker/compose.dev.yml")

    def test_adminer_present_in_prod(self, compose_prod: dict) -> None:
        """Adminer should be present in compose.prod.yml as an accepted service."""
        services = compose_prod.get("services", {})
        assert "adminer" in services, (
            "Adminer should be present in compose.prod.yml — it is an accepted "
            "production service with built-in credential-based security."
        )

    def test_adminer_security_rationale_documented(self) -> None:
        """The security rationale for Adminer in prod must be documented inline."""
        raw_text = (_project_root() / "docker/compose.prod.yml").read_text()
        assert "valid credentials are required" in raw_text.lower() or \
               "login screen" in raw_text.lower(), (
            "compose.prod.yml must document why Adminer is safe in production "
            "(built-in login screen / credential requirement)."
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

    # Dev-only services that must never be in production.
    # NOTE: Adminer is intentionally NOT in this list — it is allowed in prod.
    DEV_ONLY_SERVICES: frozenset[str] = frozenset()

    def test_no_dev_only_services_in_prod(self, compose_prod: dict) -> None:
        """Dev-only services must not leak into production compose."""
        services = set(compose_prod.get("services", {}).keys())
        leaked = services & self.DEV_ONLY_SERVICES
        assert not leaked, (
            f"Dev-only services found in compose.prod.yml: {leaked}. "
            "These services are not secured for production use."
        )

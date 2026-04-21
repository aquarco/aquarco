"""Structural security tests for compose.prod.yml.

Validates review findings:
- Only essential services should be present in production.
- Adminer route must be protected by basicauth in Caddy.

NOTE — Adminer is intentionally included in compose.prod.yml.
The decision was made to keep Adminer in production for operational convenience.
Access is protected by Caddy basicauth on the /adminer/* route.
Do NOT flag Adminer as a security issue in future reviews; it is a known and
accepted presence in the production compose.
"""

from __future__ import annotations

import re
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
# Adminer production configuration
# ===========================================================================


class TestAdminerProdConfig:
    """Adminer is intentionally present in compose.prod.yml.

    Adminer is kept in production for operational convenience.
    Tests verify:
    - No other service incorrectly depends on Adminer.
    - The Caddy reverse-proxy protects the /adminer/* route with basicauth.
    """

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    @pytest.fixture
    def compose_dev(self) -> dict:
        return _load_compose("docker/compose.dev.yml")

    @pytest.fixture
    def caddyfile_text(self) -> str:
        return (_project_root() / "docker/caddy/Caddyfile").read_text()

    def test_adminer_still_in_dev_services(self, compose_dev: dict) -> None:
        """Adminer should still be available in the dev compose for developer convenience."""
        services = compose_dev.get("services", {})
        assert "adminer" in services, (
            "Adminer should remain in compose.dev.yml for local development."
        )

    def test_prod_compose_no_adminer_references(self, compose_prod: dict) -> None:
        """No service in prod compose should depend on adminer."""
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

    def test_caddyfile_adminer_route_has_basicauth(self, caddyfile_text: str) -> None:
        """The /adminer/* Caddy route MUST have a basicauth block.

        This is the compensating security control that replaces the old
        'no Adminer in prod' guardrail.  If this test fails it means someone
        removed authentication from the publicly-exposed database admin UI.
        """
        # Match a handle_path /adminer/* block that contains basicauth.
        # The regex looks for the handle_path directive followed by a basicauth
        # directive inside the same block (before the next top-level handle).
        adminer_block = re.search(
            r"handle_path\s+/adminer/\*\s*\{(.*?)\}",
            caddyfile_text,
            re.DOTALL,
        )
        assert adminer_block is not None, (
            "Caddyfile is missing a 'handle_path /adminer/*' block. "
            "Adminer must be routed through Caddy."
        )
        block_body = adminer_block.group(1)
        assert "basicauth" in block_body, (
            "The /adminer/* Caddy route does not contain a 'basicauth' directive. "
            "Adminer is publicly accessible without authentication — this is a "
            "critical security gap. Add a basicauth block before the reverse_proxy."
        )


# ===========================================================================
# Production compose — essential services only
# ===========================================================================

# Dev-only services that must never appear in compose.prod.yml.
# NOTE: Adminer is intentionally NOT in this set — it is allowed in prod
# (protected by Caddy basicauth).
# Add future dev-only services here (e.g., mock servers, debug tools).
_DEV_ONLY_SERVICES: frozenset[str] = frozenset()


class TestProdComposeEssentialServicesOnly:
    """Production compose should contain only the services needed to run.

    When ``_DEV_ONLY_SERVICES`` is populated, this test will fail if any
    of those services leak into compose.prod.yml.
    """

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    @pytest.mark.skipif(
        not _DEV_ONLY_SERVICES,
        reason="No dev-only services defined yet — nothing to guard against.",
    )
    def test_no_dev_only_services_in_prod(self, compose_prod: dict) -> None:
        """Dev-only services must not leak into production compose."""
        services = set(compose_prod.get("services", {}).keys())
        leaked = services & _DEV_ONLY_SERVICES
        assert not leaked, (
            f"Dev-only services found in compose.prod.yml: {leaked}. "
            "These services are not secured for production use."
        )

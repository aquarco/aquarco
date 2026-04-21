"""
Cross-file consistency tests for the Adminer-in-production configuration.

Validates that the Caddyfile, compose.prod.yml, compose.yml, and compose.dev.yml
are all consistent with the decision to keep Adminer in production behind
Caddy basicauth.

These tests guard against partial changes — e.g., someone adds Adminer to prod
compose but forgets to update the Caddyfile, or removes basicauth without
updating the compose env vars.
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
    return yaml.safe_load(path.read_text())


def _read_caddyfile() -> str:
    return (_project_root() / "docker/caddy/Caddyfile").read_text()


# ===========================================================================
# Cross-file consistency: Adminer in prod
# ===========================================================================


class TestAdminerProdConsistency:
    """Ensure Adminer config is consistent across all compose files and Caddyfile."""

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    @pytest.fixture
    def compose_dev(self) -> dict:
        return _load_compose("docker/compose.dev.yml")

    @pytest.fixture
    def compose_main(self) -> dict:
        return _load_compose("docker/compose.yml")

    @pytest.fixture
    def caddyfile(self) -> str:
        return _read_caddyfile()

    # -- Adminer service parity --

    def test_adminer_image_matches_across_composes(
        self, compose_prod: dict, compose_dev: dict
    ) -> None:
        """Adminer image tag must be the same in prod and dev compose."""
        prod_image = compose_prod["services"]["adminer"]["image"]
        dev_image = compose_dev["services"]["adminer"]["image"]
        assert prod_image == dev_image, (
            f"Adminer image mismatch: prod={prod_image}, dev={dev_image}. "
            "Both should use the same Adminer version."
        )

    def test_adminer_default_server_set_in_prod(self, compose_prod: dict) -> None:
        """Adminer in prod must point to postgres as default server."""
        adminer = compose_prod["services"]["adminer"]
        env = adminer.get("environment", {})
        assert env.get("ADMINER_DEFAULT_SERVER") == "postgres", (
            "ADMINER_DEFAULT_SERVER must be 'postgres' in compose.prod.yml"
        )

    def test_adminer_restart_policy_in_prod(self, compose_prod: dict) -> None:
        """Adminer in prod must have restart: unless-stopped."""
        adminer = compose_prod["services"]["adminer"]
        assert adminer.get("restart") == "unless-stopped", (
            "Adminer in prod must use 'unless-stopped' restart policy."
        )

    # -- Caddy env vars consistency --

    def test_caddy_adminer_env_in_both_composes(
        self, compose_prod: dict, compose_main: dict
    ) -> None:
        """Both prod and main compose must pass ADMINER_AUTH env vars to Caddy."""
        for label, compose in [("prod", compose_prod), ("main", compose_main)]:
            caddy_env = compose["services"]["caddy"].get("environment", {})
            env_str = str(caddy_env)
            assert "ADMINER_AUTH_USER" in env_str, (
                f"Caddy in {label} compose missing ADMINER_AUTH_USER"
            )
            assert "ADMINER_AUTH_HASH" in env_str, (
                f"Caddy in {label} compose missing ADMINER_AUTH_HASH"
            )

    def test_prod_auth_env_stricter_than_dev(
        self, compose_prod: dict, compose_main: dict
    ) -> None:
        """Prod compose must use fail-fast syntax; main compose must use defaults."""
        prod_caddy = compose_prod["services"]["caddy"].get("environment", {})
        main_caddy = compose_main["services"]["caddy"].get("environment", {})

        # Prod: must have ? (fail-fast)
        prod_user = str(prod_caddy.get("ADMINER_AUTH_USER", ""))
        assert "?" in prod_user, (
            f"Prod ADMINER_AUTH_USER must use fail-fast syntax, got: {prod_user}"
        )

        # Main (dev): must have :- (defaults)
        main_user = str(main_caddy.get("ADMINER_AUTH_USER", ""))
        assert ":-" in main_user or "?" not in main_user, (
            f"Main compose ADMINER_AUTH_USER should provide defaults, got: {main_user}"
        )

    # -- Caddyfile structural checks --

    def test_caddyfile_basicauth_block_has_exactly_one_user(
        self, caddyfile: str
    ) -> None:
        """The basicauth block for /adminer/* must contain exactly one user entry."""
        adminer_block = re.search(
            r"handle_path /adminer/\*\s*\{([\s\S]*?)\n    \}", caddyfile
        )
        assert adminer_block, "Adminer handle_path block missing"
        block = adminer_block.group(1)
        basicauth_block = re.search(
            r"basicauth\s*\{([^}]+)\}", block
        )
        assert basicauth_block, "basicauth block missing inside /adminer/*"
        # Count non-empty lines inside basicauth block (each is a user entry)
        lines = [
            l.strip()
            for l in basicauth_block.group(1).split("\n")
            if l.strip()
        ]
        assert len(lines) == 1, (
            f"Expected exactly 1 user in basicauth block, found {len(lines)}: {lines}"
        )

    def test_caddyfile_adminer_reverse_proxy_target(self, caddyfile: str) -> None:
        """Adminer reverse_proxy must target adminer:8080 (internal Docker port)."""
        adminer_block = re.search(
            r"handle_path /adminer/\*\s*\{([\s\S]*?)\n    \}", caddyfile
        )
        assert adminer_block, "Adminer handle_path block missing"
        assert "reverse_proxy adminer:8080" in adminer_block.group(1), (
            "Adminer reverse_proxy must target adminer:8080"
        )

    # -- Adminer must NOT have direct ports in any compose --

    def test_adminer_no_ports_in_any_compose(
        self, compose_prod: dict, compose_dev: dict, compose_main: dict
    ) -> None:
        """Adminer must not expose direct ports in any compose file."""
        for label, compose in [
            ("prod", compose_prod),
            ("dev", compose_dev),
            ("main", compose_main),
        ]:
            adminer = compose["services"].get("adminer", {})
            if adminer:
                ports = adminer.get("ports", [])
                assert len(ports) == 0, (
                    f"Adminer in {label} compose must not expose direct ports: {ports}"
                )

    # -- Adminer network consistency --

    def test_adminer_on_aquarco_network_in_prod(self, compose_prod: dict) -> None:
        """Adminer in prod must be on the aquarco network for Caddy to reach it."""
        adminer = compose_prod["services"]["adminer"]
        networks = adminer.get("networks", [])
        assert "aquarco" in networks, (
            "Adminer in prod must be on 'aquarco' network"
        )

    def test_adminer_depends_on_postgres_in_all_composes(
        self, compose_prod: dict, compose_dev: dict
    ) -> None:
        """Adminer must depend on postgres in both prod and dev composes."""
        for label, compose in [("prod", compose_prod), ("dev", compose_dev)]:
            adminer = compose["services"]["adminer"]
            depends = adminer.get("depends_on", {})
            if isinstance(depends, list):
                assert "postgres" in depends, (
                    f"Adminer in {label} must depend on postgres"
                )
            elif isinstance(depends, dict):
                assert "postgres" in depends, (
                    f"Adminer in {label} must depend on postgres"
                )

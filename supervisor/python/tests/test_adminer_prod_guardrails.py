"""Guardrail tests for Adminer in production.

These tests validate the acceptance criteria from the review stage
regarding the decision to keep Adminer in compose.prod.yml:

- Adminer must be a pre-built image (no build context in prod).
- Adminer must have a proper restart policy for production.
- Caddy in prod must depend on critical services before starting.
- The basicauth block in Caddyfile must be structurally valid.
- ADMINER_AUTH_* env vars must only be on the Caddy service, not Adminer itself.
- Adminer must not have privileged or capability escalation settings.
- Dev and prod Adminer default server must both point to postgres.
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
    return Path(__file__).resolve().parent.parent.parent.parent


def _load_compose(relative_path: str) -> dict:
    path = _project_root() / relative_path
    return yaml.safe_load(path.read_text())


def _read_caddyfile() -> str:
    return (_project_root() / "docker/caddy/Caddyfile").read_text()


# ===========================================================================
# Adminer prod image and restart
# ===========================================================================


class TestAdminerProdServiceConfig:
    """Validate Adminer service configuration in compose.prod.yml."""

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    @pytest.fixture
    def compose_dev(self) -> dict:
        return _load_compose("docker/compose.yml")

    def test_adminer_prod_uses_prebuilt_image(self, compose_prod: dict) -> None:
        """Adminer in prod must use a pre-built image, not a build context.

        Production compose must never build from source — only pre-built
        images are allowed.
        """
        adminer = compose_prod["services"]["adminer"]
        assert "image" in adminer, (
            "Adminer in compose.prod.yml must specify an image (no build from source)."
        )
        assert "build" not in adminer, (
            "Adminer in compose.prod.yml must NOT have a build context. "
            "Production compose uses pre-built images only."
        )

    def test_adminer_prod_restart_policy(self, compose_prod: dict) -> None:
        """Adminer in prod must have a restart policy for reliability."""
        adminer = compose_prod["services"]["adminer"]
        restart = adminer.get("restart", "")
        assert restart in ("unless-stopped", "always"), (
            f"Adminer restart policy must be 'unless-stopped' or 'always', "
            f"got: '{restart}'"
        )

    def test_adminer_prod_no_privileged(self, compose_prod: dict) -> None:
        """Adminer must not run in privileged mode in production."""
        adminer = compose_prod["services"]["adminer"]
        assert adminer.get("privileged") is not True, (
            "Adminer must not run in privileged mode in production."
        )

    def test_adminer_prod_no_cap_add(self, compose_prod: dict) -> None:
        """Adminer must not have extra Linux capabilities in production."""
        adminer = compose_prod["services"]["adminer"]
        cap_add = adminer.get("cap_add", [])
        assert len(cap_add) == 0, (
            f"Adminer must not have cap_add in production, found: {cap_add}"
        )

    def test_adminer_default_server_points_to_postgres(
        self, compose_prod: dict
    ) -> None:
        """Adminer ADMINER_DEFAULT_SERVER must point to the postgres service."""
        adminer = compose_prod["services"]["adminer"]
        env = adminer.get("environment", {})
        default_server = env.get("ADMINER_DEFAULT_SERVER", "")
        assert default_server == "postgres", (
            f"ADMINER_DEFAULT_SERVER must be 'postgres', got: '{default_server}'"
        )

    def test_adminer_default_server_consistent_across_composes(
        self, compose_dev: dict, compose_prod: dict
    ) -> None:
        """ADMINER_DEFAULT_SERVER must be the same in dev and prod."""
        dev_env = compose_dev["services"]["adminer"].get("environment", {})
        prod_env = compose_prod["services"]["adminer"].get("environment", {})
        dev_server = dev_env.get("ADMINER_DEFAULT_SERVER", "")
        prod_server = prod_env.get("ADMINER_DEFAULT_SERVER", "")
        assert dev_server == prod_server, (
            f"ADMINER_DEFAULT_SERVER mismatch: dev='{dev_server}', prod='{prod_server}'"
        )


# ===========================================================================
# Auth env vars scoping
# ===========================================================================


class TestAdminerAuthEnvScoping:
    """ADMINER_AUTH_* env vars must only be on the Caddy service.

    The Adminer container itself should not receive the basicauth
    credentials — only Caddy needs them to enforce authentication.
    """

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    @pytest.fixture
    def compose_dev(self) -> dict:
        return _load_compose("docker/compose.yml")

    def test_adminer_service_no_auth_env_vars_prod(
        self, compose_prod: dict
    ) -> None:
        """Adminer service in prod must NOT have ADMINER_AUTH_* env vars."""
        adminer = compose_prod["services"]["adminer"]
        env = adminer.get("environment", {})
        env_keys = list(env.keys()) if isinstance(env, dict) else [str(e) for e in env]
        auth_keys = [k for k in env_keys if "ADMINER_AUTH" in k]
        assert len(auth_keys) == 0, (
            f"Adminer service must not have ADMINER_AUTH_* env vars "
            f"(those belong on the Caddy service), found: {auth_keys}"
        )

    def test_adminer_service_no_auth_env_vars_dev(
        self, compose_dev: dict
    ) -> None:
        """Adminer service in dev must NOT have ADMINER_AUTH_* env vars."""
        adminer = compose_dev["services"]["adminer"]
        env = adminer.get("environment", {})
        env_keys = list(env.keys()) if isinstance(env, dict) else [str(e) for e in env]
        auth_keys = [k for k in env_keys if "ADMINER_AUTH" in k]
        assert len(auth_keys) == 0, (
            f"Adminer service must not have ADMINER_AUTH_* env vars "
            f"(those belong on the Caddy service), found: {auth_keys}"
        )


# ===========================================================================
# Caddy prod compose config
# ===========================================================================


class TestCaddyProdConfig:
    """Validate Caddy service configuration in compose.prod.yml."""

    @pytest.fixture
    def compose_prod(self) -> dict:
        return _load_compose("docker/compose.prod.yml")

    def test_caddy_prod_depends_on_web_and_api(
        self, compose_prod: dict
    ) -> None:
        """Caddy in prod must depend on web and api services."""
        caddy = compose_prod["services"]["caddy"]
        depends = caddy.get("depends_on", {})
        dep_names = list(depends.keys()) if isinstance(depends, dict) else depends
        assert "web" in dep_names, "Caddy in prod must depend on web service"
        assert "api" in dep_names, "Caddy in prod must depend on api service"

    def test_caddy_prod_mounts_caddyfile_readonly(
        self, compose_prod: dict
    ) -> None:
        """Caddy in prod must mount the Caddyfile as read-only."""
        caddy = compose_prod["services"]["caddy"]
        volumes = [str(v) for v in caddy.get("volumes", [])]
        caddyfile_mounts = [v for v in volumes if "Caddyfile" in v]
        assert len(caddyfile_mounts) > 0, (
            "Caddy in prod must mount the Caddyfile"
        )
        assert any(":ro" in v for v in caddyfile_mounts), (
            "Caddyfile mount in prod must be read-only (:ro)"
        )

    def test_caddy_prod_exposes_8080(self, compose_prod: dict) -> None:
        """Caddy in prod must expose port 8080."""
        caddy = compose_prod["services"]["caddy"]
        ports = [str(p) for p in caddy.get("ports", [])]
        assert any("8080" in p for p in ports), (
            "Caddy in prod must expose port 8080"
        )

    def test_caddy_prod_admin_port_localhost(self, compose_prod: dict) -> None:
        """Caddy admin port in prod must be bound to localhost."""
        caddy = compose_prod["services"]["caddy"]
        ports = [str(p) for p in caddy.get("ports", [])]
        admin_ports = [p for p in ports if "2019" in p]
        for p in admin_ports:
            assert "127.0.0.1" in p, (
                f"Caddy admin port in prod must be localhost-only, found: {p}"
            )


# ===========================================================================
# Caddyfile basicauth structure
# ===========================================================================


class TestCaddyfileBasicauthStructure:
    """Validate the structural correctness of the basicauth block."""

    @pytest.fixture
    def caddyfile(self) -> str:
        return _read_caddyfile()

    def test_basicauth_block_has_credential_line(self, caddyfile: str) -> None:
        """The basicauth block must contain at least one credential line.

        A basicauth {} block with no credentials is effectively a no-op.
        """
        adminer_block = re.search(
            r"handle_path /adminer/\*\s*\{([\s\S]*?)\n    \}",
            caddyfile,
        )
        assert adminer_block, "Adminer handle_path block must exist"
        block_body = adminer_block.group(1)

        # Find the basicauth sub-block
        basicauth_block = re.search(
            r"basicauth\s*\{([\s\S]*?)\}",
            block_body,
        )
        assert basicauth_block, "basicauth block must exist inside adminer route"
        auth_body = basicauth_block.group(1).strip()
        assert len(auth_body) > 0, (
            "basicauth block must contain at least one credential line. "
            "An empty basicauth block provides no protection."
        )

    def test_basicauth_not_after_reverse_proxy(self, caddyfile: str) -> None:
        """Verify basicauth is not accidentally placed after reverse_proxy.

        This is a structural safety check — if basicauth comes after
        reverse_proxy in the Caddyfile, Caddy will forward requests
        before checking credentials.
        """
        lines = caddyfile.split("\n")
        in_adminer_block = False
        saw_reverse_proxy = False
        saw_basicauth_after_proxy = False

        for line in lines:
            stripped = line.strip()
            if "handle_path /adminer/*" in stripped:
                in_adminer_block = True
                saw_reverse_proxy = False
                continue
            if in_adminer_block:
                if stripped.startswith("reverse_proxy"):
                    saw_reverse_proxy = True
                if "basicauth" in stripped and saw_reverse_proxy:
                    saw_basicauth_after_proxy = True
                # End of outer block
                if stripped == "}" and not stripped.startswith("reverse_proxy"):
                    in_adminer_block = False

        assert not saw_basicauth_after_proxy, (
            "basicauth must NOT appear after reverse_proxy in the adminer block."
        )

    def test_adminer_route_has_reverse_proxy(self, caddyfile: str) -> None:
        """The adminer route must actually proxy to the adminer service."""
        adminer_block = re.search(
            r"handle_path /adminer/\*\s*\{([\s\S]*?)\n    \}",
            caddyfile,
        )
        assert adminer_block, "Adminer handle_path block must exist"
        assert "reverse_proxy adminer:8080" in adminer_block.group(1), (
            "Adminer route must proxy to adminer:8080"
        )

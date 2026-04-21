"""
Security-focused tests for the Caddy reverse proxy configuration.

Validates findings from the review stage:
- Admin API binding (should be localhost-only)
- Port exposure patterns
- No secrets in config files
- Network isolation patterns

Issue: https://github.com/aquarco/aquarco/issues/2
"""

import re
import yaml
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def project_root() -> Path:
    """Return the aquarco project root directory."""
    return Path(__file__).resolve().parent.parent.parent.parent


def read_file(relative_path: str) -> str:
    """Read a file relative to project root."""
    path = project_root() / relative_path
    return path.read_text()


def load_compose(relative_path: str) -> dict:
    """Load a Docker Compose YAML file."""
    content = read_file(relative_path)
    return yaml.safe_load(content)


# ===========================================================================
# Caddy Admin API Security
# ===========================================================================


class TestCaddyAdminApiSecurity:
    """Validate that the Caddy admin API is not exposed externally."""

    @pytest.fixture
    def caddyfile(self) -> str:
        return read_file("docker/caddy/Caddyfile")

    @pytest.fixture
    def compose(self) -> dict:
        return load_compose("docker/compose.yml")

    def test_admin_api_not_disabled(self, caddyfile: str):
        """Admin API should be present (needed for dynamic routing in Phase 2)."""
        # It should NOT be 'admin off' since we need it
        assert "admin off" not in caddyfile, (
            "Admin API should not be disabled — needed for Phase 2 dynamic routing"
        )

    def test_admin_port_docker_binding_localhost_only(self, compose: dict):
        """Docker must bind admin port (2019) to localhost only."""
        caddy = compose["services"]["caddy"]
        ports = caddy.get("ports", [])
        port_strs = [str(p) for p in ports]
        admin_ports = [p for p in port_strs if "2019" in p]

        for p in admin_ports:
            assert "127.0.0.1" in p, (
                f"Caddy admin port must be bound to 127.0.0.1, found: {p}. "
                "Without localhost binding, any Docker network peer could reconfigure Caddy."
            )

    def test_admin_port_not_on_all_interfaces(self, compose: dict):
        """Admin port must NOT be exposed on 0.0.0.0."""
        caddy = compose["services"]["caddy"]
        ports = caddy.get("ports", [])
        port_strs = [str(p) for p in ports]
        admin_ports = [p for p in port_strs if "2019" in p]

        for p in admin_ports:
            # Patterns like "2019:2019" or "0.0.0.0:2019:2019" expose on all interfaces
            assert not re.match(r"^(0\.0\.0\.0:)?2019:", p), (
                f"Admin API port must not be on all interfaces, found: {p}"
            )

    def test_no_secrets_in_caddyfile(self, caddyfile: str):
        """Caddyfile must not contain hardcoded secrets or tokens."""
        secret_patterns = [
            r'(?i)password\s*[=:]\s*\S+',
            r'(?i)api[_-]?key\s*[=:]\s*\S+',
            r'(?i)token\s*[=:]\s*\S+',
            r'(?i)secret\s*[=:]\s*\S+',
            r'Bearer\s+[A-Za-z0-9\-._~+/]+=*',
        ]
        for pattern in secret_patterns:
            matches = re.findall(pattern, caddyfile)
            assert len(matches) == 0, (
                f"Caddyfile may contain secrets matching pattern '{pattern}': {matches}"
            )


# ===========================================================================
# Adminer Route Authentication
# ===========================================================================


class TestAdminerRouteAuth:
    """Validate that the Adminer route is protected by basic authentication."""

    @pytest.fixture
    def caddyfile(self) -> str:
        return read_file("docker/caddy/Caddyfile")

    @pytest.fixture
    def compose_prod(self) -> dict:
        return load_compose("docker/compose.prod.yml")

    @pytest.fixture
    def compose_dev(self) -> dict:
        return load_compose("docker/compose.yml")

    def test_adminer_route_has_basicauth(self, caddyfile: str):
        """Adminer route must be protected by basicauth."""
        # Find the adminer handle_path block and verify it contains basicauth
        adminer_block = re.search(
            r"handle_path /adminer/\*\s*\{([\s\S]*?)\n    \}", caddyfile
        )
        assert adminer_block, "Adminer handle_path block must exist"
        block_content = adminer_block.group(1)
        assert "basicauth" in block_content, (
            "Adminer route must have basicauth directive. "
            "Adminer is a database admin UI and must not be publicly accessible "
            "without authentication."
        )

    def test_adminer_auth_uses_env_vars(self, caddyfile: str):
        """Adminer basicauth must use environment variables (not hardcoded creds)."""
        adminer_block = re.search(
            r"handle_path /adminer/\*\s*\{([\s\S]*?)\n    \}", caddyfile
        )
        assert adminer_block, "Adminer handle_path block must exist"
        block_content = adminer_block.group(1)
        assert "{$ADMINER_AUTH_USER}" in block_content, (
            "Adminer basicauth must use {$ADMINER_AUTH_USER} env var"
        )
        assert "{$ADMINER_AUTH_HASH}" in block_content, (
            "Adminer basicauth must use {$ADMINER_AUTH_HASH} env var"
        )

    def test_prod_compose_requires_adminer_auth_env(self, compose_prod: dict):
        """Production compose must pass ADMINER_AUTH_USER and ADMINER_AUTH_HASH to Caddy."""
        caddy = compose_prod["services"]["caddy"]
        env = caddy.get("environment", {})
        env_str = str(env)
        assert "ADMINER_AUTH_USER" in env_str, (
            "compose.prod.yml must set ADMINER_AUTH_USER for Caddy"
        )
        assert "ADMINER_AUTH_HASH" in env_str, (
            "compose.prod.yml must set ADMINER_AUTH_HASH for Caddy"
        )

    def test_dev_compose_has_adminer_auth_defaults(self, compose_dev: dict):
        """Dev compose must provide default ADMINER_AUTH_* env vars for Caddy."""
        caddy = compose_dev["services"]["caddy"]
        env = caddy.get("environment", {})
        env_str = str(env)
        assert "ADMINER_AUTH_USER" in env_str, (
            "compose.yml must set ADMINER_AUTH_USER for Caddy"
        )
        assert "ADMINER_AUTH_HASH" in env_str, (
            "compose.yml must set ADMINER_AUTH_HASH for Caddy"
        )

    def test_basicauth_before_reverse_proxy_in_adminer(self, caddyfile: str):
        """basicauth directive must appear BEFORE reverse_proxy in the adminer block.

        In Caddy, directive ordering within a block matters. The basicauth
        must execute before the reverse_proxy passes the request through.
        """
        adminer_block = re.search(
            r"handle_path /adminer/\*\s*\{([\s\S]*?)\n    \}", caddyfile
        )
        assert adminer_block, "Adminer handle_path block must exist"
        block_content = adminer_block.group(1)
        basicauth_pos = block_content.find("basicauth")
        reverse_proxy_pos = block_content.find("reverse_proxy")
        assert basicauth_pos != -1, "basicauth must be present in adminer block"
        assert reverse_proxy_pos != -1, "reverse_proxy must be present in adminer block"
        assert basicauth_pos < reverse_proxy_pos, (
            "basicauth must appear BEFORE reverse_proxy in the adminer block. "
            "If reverse_proxy comes first, requests are forwarded unauthenticated."
        )

    def test_prod_adminer_auth_env_fail_fast(self, compose_prod: dict):
        """Production compose ADMINER_AUTH env vars must use fail-fast ?-syntax.

        In production, ADMINER_AUTH_USER and ADMINER_AUTH_HASH must NOT have
        defaults — the deploy MUST fail if the operator forgets to set them.
        Docker Compose ``?`` syntax (e.g., ``${VAR:?msg}``) causes a fatal error
        on startup when the variable is missing.
        """
        caddy = compose_prod["services"]["caddy"]
        env = caddy.get("environment", {})

        user_val = str(env.get("ADMINER_AUTH_USER", ""))
        hash_val = str(env.get("ADMINER_AUTH_HASH", ""))

        assert "?" in user_val, (
            f"ADMINER_AUTH_USER in compose.prod.yml must use fail-fast syntax "
            f"(${{VAR:?msg}}), got: {user_val}. Without this, production may "
            "start with empty or default credentials."
        )
        assert "?" in hash_val, (
            f"ADMINER_AUTH_HASH in compose.prod.yml must use fail-fast syntax "
            f"(${{VAR:?msg}}), got: {hash_val}. Without this, production may "
            "start with empty or default credentials."
        )

    def test_dev_adminer_auth_env_provides_defaults(self, compose_dev: dict):
        """Dev compose ADMINER_AUTH env vars must provide safe defaults.

        Unlike production, dev compose should start without requiring the
        operator to set env vars — using ``${VAR:-default}`` syntax.
        """
        caddy = compose_dev["services"]["caddy"]
        env = caddy.get("environment", {})

        user_val = str(env.get("ADMINER_AUTH_USER", ""))
        hash_val = str(env.get("ADMINER_AUTH_HASH", ""))

        assert ":-" in user_val or "?" not in user_val, (
            f"ADMINER_AUTH_USER in dev compose should have a default value, "
            f"got: {user_val}"
        )
        assert ":-" in hash_val or "?" not in hash_val, (
            f"ADMINER_AUTH_HASH in dev compose should have a default value, "
            f"got: {hash_val}"
        )


# ===========================================================================
# Port Exposure Security
# ===========================================================================


class TestPortExposureSecurity:
    """Validate that only the Caddy proxy port is exposed externally."""

    @pytest.fixture
    def compose(self) -> dict:
        return load_compose("docker/compose.yml")

    @pytest.fixture
    def compose_dev(self) -> dict:
        return load_compose("docker/compose.dev.yml")

    @pytest.fixture
    def compose_monitoring(self) -> dict:
        return load_compose("docker/compose.monitoring.yml")

    def test_only_caddy_exposes_all_interface_port(self, compose: dict):
        """Only Caddy should have a port on all interfaces (8080)."""
        for svc_name, svc in compose.get("services", {}).items():
            ports = svc.get("ports", [])
            for p in ports:
                p_str = str(p)
                # Ports without 127.0.0.1 prefix are on all interfaces
                if "127.0.0.1" not in p_str:
                    assert svc_name == "caddy", (
                        f"Service '{svc_name}' exposes port on all interfaces: {p_str}. "
                        "Only Caddy should be externally accessible."
                    )

    def test_postgres_port_localhost_only(self, compose: dict):
        """PostgreSQL must only be accessible from localhost."""
        pg = compose["services"]["postgres"]
        ports = pg.get("ports", [])
        for p in ports:
            p_str = str(p)
            if "5432" in p_str:
                assert "127.0.0.1" in p_str, (
                    f"PostgreSQL port must be localhost-only, found: {p_str}"
                )

    def test_monitoring_no_external_ports(self, compose_monitoring: dict):
        """Monitoring services (Prometheus, Grafana) must not expose direct ports."""
        for svc_name in ["prometheus", "grafana"]:
            svc = compose_monitoring["services"].get(svc_name, {})
            ports = svc.get("ports", [])
            assert len(ports) == 0, (
                f"{svc_name} should have no direct ports (Caddy handles routing), "
                f"found: {ports}"
            )

    def test_adminer_no_external_ports(self, compose_dev: dict):
        """Adminer must not expose direct ports."""
        adminer = compose_dev["services"].get("adminer", {})
        ports = adminer.get("ports", [])
        assert len(ports) == 0, (
            f"Adminer should have no direct ports, found: {ports}"
        )

    def test_api_debug_port_localhost_only(self, compose_dev: dict):
        """API debug port (9229) in dev compose must be localhost-only."""
        api = compose_dev["services"].get("api", {})
        ports = api.get("ports", [])
        for p in ports:
            p_str = str(p)
            if "9229" in p_str:
                assert "127.0.0.1" in p_str, (
                    f"API debug port must be localhost-only, found: {p_str}"
                )


# ===========================================================================
# Network Isolation
# ===========================================================================


class TestNetworkIsolation:
    """Validate Docker network configuration for proper service isolation."""

    @pytest.fixture
    def compose(self) -> dict:
        return load_compose("docker/compose.yml")

    @pytest.fixture
    def compose_monitoring(self) -> dict:
        return load_compose("docker/compose.monitoring.yml")

    def test_main_network_is_bridge(self, compose: dict):
        """Main aquarco network must use bridge driver."""
        networks = compose.get("networks", {})
        aquarco = networks.get("aquarco", {})
        driver = aquarco.get("driver", "bridge")
        assert driver == "bridge", (
            f"Expected bridge driver for aquarco network, got: {driver}"
        )

    def test_monitoring_uses_external_network(self, compose_monitoring: dict):
        """Monitoring stack must use external aquarco network."""
        networks = compose_monitoring.get("networks", {})
        aquarco = networks.get("aquarco", {})
        assert aquarco.get("external") is True

    def test_monitoring_has_separate_default_network(self, compose_monitoring: dict):
        """Monitoring stack should have its own default network for internal comms."""
        networks = compose_monitoring.get("networks", {})
        default_net = networks.get("default", {})
        assert default_net.get("name") == "aquarco-monitoring", (
            "Monitoring stack should have its own named default network"
        )

    def test_all_main_services_on_aquarco_network(self, compose: dict):
        """All main services must be on the aquarco network."""
        for svc_name, svc in compose.get("services", {}).items():
            if svc_name == "migrations":
                # Migrations is a one-shot job, network doesn't matter as much
                continue
            networks = svc.get("networks", [])
            assert "aquarco" in networks, (
                f"Service '{svc_name}' must be on the aquarco network"
            )

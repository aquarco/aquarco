"""
Infrastructure validation tests for the Caddy reverse proxy routing layer.

Validates:
- Caddyfile syntax and routing rules
- Docker Compose configuration correctness
- Compose service topology (networks, ports, volumes)
- Vagrantfile port forwarding configuration
- Monitoring stack subpath configuration

Issue: https://github.com/aquarco/aquarco/issues/2
"""

import os
import re
import yaml
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def project_root() -> Path:
    """Return the aquarco project root directory."""
    # tests/ -> python/ -> supervisor/ -> project root
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
# Caddyfile Tests
# ===========================================================================


class TestCaddyfile:
    """Validate Caddyfile routing configuration."""

    @pytest.fixture
    def caddyfile(self) -> str:
        return read_file("docker/caddy/Caddyfile")

    def test_caddyfile_exists(self):
        path = project_root() / "docker" / "caddy" / "Caddyfile"
        assert path.exists(), "docker/caddy/Caddyfile must exist"

    def test_listens_on_port_8080(self, caddyfile: str):
        assert ":8080" in caddyfile, "Caddy must listen on port 8080"

    def test_auto_https_off(self, caddyfile: str):
        assert "auto_https off" in caddyfile, "HTTPS must be disabled for dev"

    def test_admin_api_configured(self, caddyfile: str):
        # Admin API must be present (needed for Phase 2 dynamic routing)
        assert "admin" in caddyfile, "Caddy admin API must be configured"

    def test_api_route_uses_handle_path(self, caddyfile: str):
        """API route must use handle_path to strip /api prefix."""
        assert "handle_path /api/*" in caddyfile

    def test_api_route_proxies_to_api_4000(self, caddyfile: str):
        # After handle_path /api/*, should proxy to api:4000
        api_block = re.search(
            r"handle_path /api/\*\s*\{([^}]+)\}", caddyfile
        )
        assert api_block, "API handle_path block must exist"
        assert "reverse_proxy api:4000" in api_block.group(1)

    def test_adminer_route_uses_handle_path(self, caddyfile: str):
        """Adminer route must strip /adminer prefix."""
        assert "handle_path /adminer/*" in caddyfile

    def test_adminer_route_proxies_to_adminer_8080(self, caddyfile: str):
        adminer_block = re.search(
            r"handle_path /adminer/\*\s*\{([\s\S]*?)\n    \}", caddyfile
        )
        assert adminer_block, "Adminer handle_path block must exist"
        assert "reverse_proxy adminer:8080" in adminer_block.group(1)

    def test_grafana_route_uses_handle_not_handle_path(self, caddyfile: str):
        """Grafana serves from subpath, so prefix must be preserved (handle, not handle_path)."""
        assert "handle /grafana/*" in caddyfile
        # Must NOT use handle_path for grafana
        assert "handle_path /grafana/*" not in caddyfile

    def test_grafana_route_proxies_to_grafana_3000(self, caddyfile: str):
        grafana_block = re.search(
            r"handle /grafana/\*\s*\{([^}]+)\}", caddyfile
        )
        assert grafana_block, "Grafana handle block must exist"
        assert "reverse_proxy grafana:3000" in grafana_block.group(1)

    def test_prometheus_route_uses_handle_not_handle_path(self, caddyfile: str):
        """Prometheus serves from subpath, so prefix must be preserved."""
        assert "handle /prometheus/*" in caddyfile
        assert "handle_path /prometheus/*" not in caddyfile

    def test_prometheus_route_proxies_to_prometheus_9090(self, caddyfile: str):
        prom_block = re.search(
            r"handle /prometheus/\*\s*\{([^}]+)\}", caddyfile
        )
        assert prom_block, "Prometheus handle block must exist"
        assert "reverse_proxy prometheus:9090" in prom_block.group(1)

    def test_repo_placeholder_returns_503(self, caddyfile: str):
        """Phase 2 placeholder for /repo/* must return 503."""
        assert "handle /repo/*" in caddyfile
        repo_block = re.search(r"handle /repo/\*\s*\{([^}]+)\}", caddyfile)
        assert repo_block, "/repo/* block must exist"
        assert "503" in repo_block.group(1)

    def test_default_handler_proxies_to_web(self, caddyfile: str):
        """Default catch-all must proxy to web:3000."""
        # The default handle {} block should be last and proxy to web
        default_block = re.search(r"handle\s*\{([^}]+)\}", caddyfile)
        assert default_block, "Default handle block must exist"
        assert "reverse_proxy web:3000" in default_block.group(1)

    def test_route_ordering_default_last(self, caddyfile: str):
        """Default handler must come after all specific routes."""
        lines = caddyfile.split("\n")
        # Find positions of specific routes and default
        specific_routes = []
        default_pos = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("handle_path /") or (
                stripped.startswith("handle /") and "/*" in stripped
            ):
                specific_routes.append(i)
            elif stripped == "handle {":
                default_pos = i

        assert default_pos is not None, "Default handle block must exist"
        assert all(
            pos < default_pos for pos in specific_routes
        ), "Default handler must be last"

    def test_all_expected_routes_present(self, caddyfile: str):
        """All routes from the routing map must be present."""
        expected_routes = ["/api/*", "/adminer/*", "/grafana/*", "/prometheus/*", "/repo/*"]
        for route in expected_routes:
            assert route in caddyfile, f"Route {route} must be in Caddyfile"


# ===========================================================================
# Docker Compose Tests
# ===========================================================================


class TestComposeMain:
    """Validate main docker/compose.yml configuration."""

    @pytest.fixture
    def compose(self) -> dict:
        return load_compose("docker/compose.yml")

    def test_caddy_service_exists(self, compose: dict):
        assert "caddy" in compose["services"], "Caddy service must be defined"

    def test_caddy_image(self, compose: dict):
        caddy = compose["services"]["caddy"]
        assert caddy["image"].startswith("caddy:"), "Caddy must use official caddy image"

    def test_caddy_ports_8080_exposed(self, compose: dict):
        caddy = compose["services"]["caddy"]
        ports = caddy.get("ports", [])
        port_strs = [str(p) for p in ports]
        assert any("8080:8080" in p for p in port_strs), "Caddy must expose 8080"

    def test_caddy_admin_port_localhost_only(self, compose: dict):
        caddy = compose["services"]["caddy"]
        ports = caddy.get("ports", [])
        port_strs = [str(p) for p in ports]
        admin_ports = [p for p in port_strs if "2019" in p]
        assert len(admin_ports) > 0, "Caddy admin port must be mapped"
        assert all("127.0.0.1" in p for p in admin_ports), (
            "Caddy admin port must be bound to localhost only"
        )

    def test_caddy_mounts_caddyfile(self, compose: dict):
        caddy = compose["services"]["caddy"]
        volumes = caddy.get("volumes", [])
        volume_strs = [str(v) for v in volumes]
        assert any("Caddyfile" in v for v in volume_strs), (
            "Caddy must mount Caddyfile"
        )

    def test_caddy_depends_on_web_and_api(self, compose: dict):
        caddy = compose["services"]["caddy"]
        depends = caddy.get("depends_on", {})
        if isinstance(depends, list):
            assert "web" in depends
            assert "api" in depends
        else:
            assert "web" in depends or "api" in depends

    def test_caddy_on_aquarco_network(self, compose: dict):
        caddy = compose["services"]["caddy"]
        networks = caddy.get("networks", [])
        assert "aquarco" in networks, "Caddy must be on aquarco network"

    def test_web_no_external_ports(self, compose: dict):
        """Web service should not expose ports directly — Caddy handles routing."""
        web = compose["services"]["web"]
        ports = web.get("ports", [])
        assert len(ports) == 0, (
            f"Web service should have no external ports, found: {ports}"
        )

    def test_api_port_localhost_only(self, compose: dict):
        """API port should only be accessible from localhost (debug access)."""
        api = compose["services"]["api"]
        ports = api.get("ports", [])
        port_strs = [str(p) for p in ports]
        for p in port_strs:
            if "4000" in p:
                assert "127.0.0.1" in p, (
                    f"API port must be localhost-only, found: {p}"
                )

    def test_caddy_volumes_defined(self, compose: dict):
        volumes = compose.get("volumes", {})
        assert "caddy_data" in volumes, "caddy_data volume must be defined"
        assert "caddy_config" in volumes, "caddy_config volume must be defined"

    def test_web_api_url_env_var(self, compose: dict):
        """Web service must set NEXT_PUBLIC_API_URL to /api/graphql for Caddy routing."""
        web = compose["services"]["web"]
        env = web.get("environment", {})
        api_url = env.get("NEXT_PUBLIC_API_URL", "")
        # Should contain /api/graphql (may have ${...:-} default syntax)
        assert "/api/graphql" in str(api_url), (
            f"NEXT_PUBLIC_API_URL must default to /api/graphql, got: {api_url}"
        )

    def test_caddy_restart_policy(self, compose: dict):
        caddy = compose["services"]["caddy"]
        assert caddy.get("restart") == "unless-stopped"


class TestComposeDev:
    """Validate docker/compose.dev.yml overrides."""

    @pytest.fixture
    def compose(self) -> dict:
        return load_compose("docker/compose.dev.yml")

    def test_adminer_no_external_ports(self, compose: dict):
        """Adminer should not have direct ports — Caddy routes via /adminer/*."""
        adminer = compose["services"].get("adminer", {})
        ports = adminer.get("ports", [])
        assert len(ports) == 0, (
            f"Adminer should have no external ports (Caddy handles routing), found: {ports}"
        )

    def test_adminer_on_aquarco_network(self, compose: dict):
        adminer = compose["services"].get("adminer", {})
        networks = adminer.get("networks", [])
        assert "aquarco" in networks, "Adminer must be on aquarco network"

    def test_adminer_service_exists(self, compose: dict):
        assert "adminer" in compose["services"], "Adminer service must exist in dev compose"


class TestComposeMonitoring:
    """Validate docker/compose.monitoring.yml for subpath routing."""

    @pytest.fixture
    def compose(self) -> dict:
        return load_compose("docker/compose.monitoring.yml")

    def test_aquarco_network_external(self, compose: dict):
        """Monitoring stack must declare aquarco network as external."""
        networks = compose.get("networks", {})
        aquarco_net = networks.get("aquarco", {})
        assert aquarco_net.get("external") is True, (
            "aquarco network must be declared as external"
        )

    def test_aquarco_network_name(self, compose: dict):
        """External network must use Docker Compose naming convention."""
        networks = compose.get("networks", {})
        aquarco_net = networks.get("aquarco", {})
        assert aquarco_net.get("name") == "aquarco_aquarco", (
            "External network name must be aquarco_aquarco (project_network format)"
        )

    def test_prometheus_on_aquarco_network(self, compose: dict):
        prom = compose["services"]["prometheus"]
        networks = prom.get("networks", [])
        assert "aquarco" in networks, "Prometheus must be on aquarco network for Caddy routing"

    def test_grafana_on_aquarco_network(self, compose: dict):
        grafana = compose["services"]["grafana"]
        networks = grafana.get("networks", [])
        assert "aquarco" in networks, "Grafana must be on aquarco network for Caddy routing"

    def test_prometheus_subpath_config(self, compose: dict):
        """Prometheus must be configured with --web.route-prefix=/prometheus."""
        prom = compose["services"]["prometheus"]
        command = prom.get("command", [])
        command_str = " ".join(str(c) for c in command)
        assert "--web.route-prefix=/prometheus" in command_str, (
            "Prometheus must have --web.route-prefix=/prometheus"
        )
        assert "--web.external-url=http://localhost:8080/prometheus" in command_str, (
            "Prometheus must have --web.external-url pointing to Caddy"
        )

    def test_grafana_subpath_config(self, compose: dict):
        """Grafana must be configured to serve from /grafana/ subpath."""
        grafana = compose["services"]["grafana"]
        env = grafana.get("environment", {})
        assert env.get("GF_SERVER_SERVE_FROM_SUB_PATH") == "true", (
            "Grafana must serve from subpath"
        )
        root_url = env.get("GF_SERVER_ROOT_URL", "")
        assert "/grafana/" in root_url, (
            f"Grafana root URL must contain /grafana/, got: {root_url}"
        )

    def test_grafana_healthcheck_uses_subpath(self, compose: dict):
        """Grafana healthcheck must use /grafana/api/health."""
        grafana = compose["services"]["grafana"]
        hc = grafana.get("healthcheck", {})
        test_cmd = " ".join(str(t) for t in hc.get("test", []))
        assert "/grafana/api/health" in test_cmd, (
            "Grafana healthcheck must use subpath /grafana/api/health"
        )

    def test_prometheus_healthcheck_uses_subpath(self, compose: dict):
        """Prometheus healthcheck must use /prometheus/-/healthy."""
        prom = compose["services"]["prometheus"]
        hc = prom.get("healthcheck", {})
        test_cmd = " ".join(str(t) for t in hc.get("test", []))
        assert "/prometheus/-/healthy" in test_cmd, (
            "Prometheus healthcheck must use subpath /prometheus/-/healthy"
        )

    def test_prometheus_no_direct_ports(self, compose: dict):
        """Prometheus should not expose direct ports — Caddy routes via /prometheus/*."""
        prom = compose["services"]["prometheus"]
        ports = prom.get("ports", [])
        assert len(ports) == 0, (
            f"Prometheus should have no direct ports, found: {ports}"
        )

    def test_grafana_no_direct_ports(self, compose: dict):
        """Grafana should not expose direct ports — Caddy routes via /grafana/*."""
        grafana = compose["services"]["grafana"]
        ports = grafana.get("ports", [])
        assert len(ports) == 0, (
            f"Grafana should have no direct ports, found: {ports}"
        )


# ===========================================================================
# Vagrantfile Tests
# ===========================================================================


class TestVagrantfile:
    """Validate Vagrantfile port forwarding configuration."""

    @pytest.fixture
    def vagrantfile(self) -> str:
        return read_file("vagrant/prod/Vagrantfile")

    def test_proxy_port_forwarded(self, vagrantfile: str):
        """Port 8080 (Caddy proxy) must be forwarded."""
        assert re.search(
            r'forwarded_port.*guest:\s*8080', vagrantfile
        ), "Port 8080 must be forwarded for Caddy proxy"

    def test_postgres_port_forwarded(self, vagrantfile: str):
        """PostgreSQL port must be forwarded (guest 5432 -> host 15432)."""
        assert re.search(
            r'forwarded_port.*guest:\s*5432.*host:\s*15432', vagrantfile
        ), "PostgreSQL must be forwarded as guest:5432 -> host:15432"

    def test_no_direct_api_port_forwarding(self, vagrantfile: str):
        """Direct API port 4000 should NOT be forwarded (Caddy handles it)."""
        # Active (non-commented) lines forwarding port 4000
        active_lines = [
            line for line in vagrantfile.split("\n")
            if "forwarded_port" in line
            and not line.strip().startswith("#")
        ]
        api_forwards = [l for l in active_lines if "guest: 4000" in l or "guest:4000" in l]
        assert len(api_forwards) == 0, (
            "Port 4000 should not be directly forwarded (Caddy handles API routing)"
        )

    def test_no_direct_grafana_port_forwarding(self, vagrantfile: str):
        """Grafana port 3000/13000 should NOT be forwarded."""
        active_lines = [
            line for line in vagrantfile.split("\n")
            if "forwarded_port" in line
            and not line.strip().startswith("#")
        ]
        grafana_forwards = [
            l for l in active_lines
            if 'id: "grafana"' in l or "guest: 3000," in l or "guest: 13000" in l
        ]
        assert len(grafana_forwards) == 0, (
            "Grafana ports should not be directly forwarded"
        )

    def test_no_direct_prometheus_port_forwarding(self, vagrantfile: str):
        """Prometheus port 9090 should NOT be forwarded."""
        active_lines = [
            line for line in vagrantfile.split("\n")
            if "forwarded_port" in line
            and not line.strip().startswith("#")
        ]
        prom_forwards = [l for l in active_lines if "guest: 9090" in l or "guest:9090" in l]
        assert len(prom_forwards) == 0, (
            "Prometheus port should not be directly forwarded"
        )

    def test_no_direct_adminer_port_forwarding(self, vagrantfile: str):
        """Adminer port 8081 should NOT be forwarded."""
        active_lines = [
            line for line in vagrantfile.split("\n")
            if "forwarded_port" in line
            and not line.strip().startswith("#")
        ]
        adminer_forwards = [l for l in active_lines if "guest: 8081" in l or "guest:8081" in l]
        assert len(adminer_forwards) == 0, (
            "Adminer port 8081 should not be directly forwarded"
        )

    def test_minimal_active_port_forwards(self, vagrantfile: str):
        """Only essential ports should be forwarded (proxy, postgres, tools)."""
        active_lines = [
            line for line in vagrantfile.split("\n")
            if "forwarded_port" in line
            and not line.strip().startswith("#")
        ]
        # Should have proxy (8080), postgres (15432), and claude-spend (8085)
        assert len(active_lines) <= 4, (
            f"Expected at most 4 active port forwards, found {len(active_lines)}: "
            + str(active_lines)
        )

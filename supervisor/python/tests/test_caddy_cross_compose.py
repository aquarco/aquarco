"""
Cross-compose consistency tests for Caddy routing.

Validates that the Caddyfile routing targets are consistent with
the Docker Compose service definitions across all compose files.

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
    return Path(__file__).resolve().parent.parent.parent.parent


def read_file(relative_path: str) -> str:
    path = project_root() / relative_path
    return path.read_text()


def load_compose(relative_path: str) -> dict:
    content = read_file(relative_path)
    return yaml.safe_load(content)


def extract_reverse_proxy_targets(caddyfile: str) -> list[tuple[str, str]]:
    """Extract (service_name, port) pairs from reverse_proxy directives."""
    targets = []
    for match in re.finditer(r'reverse_proxy\s+(\w+):(\d+)', caddyfile):
        targets.append((match.group(1), match.group(2)))
    return targets


def get_all_compose_services() -> set[str]:
    """Get all service names across all compose files."""
    services = set()
    for compose_file in [
        "docker/compose.yml",
        "docker/compose.dev.yml",
        "docker/compose.monitoring.yml",
    ]:
        try:
            compose = load_compose(compose_file)
            services.update(compose.get("services", {}).keys())
        except FileNotFoundError:
            pass
    return services


# ===========================================================================
# Cross-Compose Consistency
# ===========================================================================


class TestCaddyComposeConsistency:
    """Ensure Caddyfile reverse_proxy targets exist as Docker Compose services."""

    @pytest.fixture
    def caddyfile(self) -> str:
        return read_file("docker/caddy/Caddyfile")

    @pytest.fixture
    def all_services(self) -> set[str]:
        return get_all_compose_services()

    @pytest.fixture
    def proxy_targets(self, caddyfile: str) -> list[tuple[str, str]]:
        return extract_reverse_proxy_targets(caddyfile)

    def test_all_proxy_targets_are_compose_services(
        self, proxy_targets: list[tuple[str, str]], all_services: set[str]
    ):
        """Every reverse_proxy target in Caddyfile must map to a compose service."""
        for service, port in proxy_targets:
            assert service in all_services, (
                f"Caddyfile references service '{service}:{port}' but no compose file "
                f"defines a '{service}' service. Available: {sorted(all_services)}"
            )

    def test_api_target_port_matches_compose(self, caddyfile: str):
        """API reverse_proxy port must match the PORT env var in compose.yml."""
        compose = load_compose("docker/compose.yml")
        api = compose["services"]["api"]
        env = api.get("environment", {})
        port = str(env.get("PORT", "4000"))
        # Extract default from ${PORT:-4000} pattern
        port_match = re.search(r'\$\{PORT:-(\d+)\}', str(port))
        expected_port = port_match.group(1) if port_match else port

        api_block = re.search(r'handle_path /api/\*\s*\{([^}]+)\}', caddyfile)
        assert api_block, "API handle_path block must exist"
        assert f"api:{expected_port}" in api_block.group(1), (
            f"Caddyfile API proxy must target api:{expected_port}"
        )

    def test_web_target_port_3000(self, caddyfile: str):
        """Default handler must proxy to web:3000 (Next.js default port)."""
        default_block = re.search(r'handle\s*\{([^}]+)\}', caddyfile)
        assert default_block, "Default handle block must exist"
        assert "web:3000" in default_block.group(1)

    def test_grafana_proxy_port_matches_default(self, caddyfile: str):
        """Grafana reverse_proxy must target port 3000 (Grafana default)."""
        grafana_block = re.search(r'handle /grafana/\*\s*\{([^}]+)\}', caddyfile)
        assert grafana_block, "Grafana handle block must exist"
        assert "grafana:3000" in grafana_block.group(1)

    def test_prometheus_proxy_port_matches_default(self, caddyfile: str):
        """Prometheus reverse_proxy must target port 9090 (Prometheus default)."""
        prom_block = re.search(r'handle /prometheus/\*\s*\{([^}]+)\}', caddyfile)
        assert prom_block, "Prometheus handle block must exist"
        assert "prometheus:9090" in prom_block.group(1)

    def test_caddy_depends_on_critical_services(self):
        """Caddy must depend on web and api in compose.yml."""
        compose = load_compose("docker/compose.yml")
        caddy = compose["services"]["caddy"]
        depends = caddy.get("depends_on", {})
        if isinstance(depends, list):
            dep_names = depends
        else:
            dep_names = list(depends.keys())
        assert "web" in dep_names, "Caddy must depend on web service"
        assert "api" in dep_names, "Caddy must depend on api service"


class TestVagrantComposeAlignment:
    """Ensure Vagrantfile port forwarding aligns with compose port exposure."""

    @pytest.fixture
    def vagrantfile(self) -> str:
        return read_file("vagrant/Vagrantfile")

    @pytest.fixture
    def compose(self) -> dict:
        return load_compose("docker/compose.yml")

    def test_vagrant_forwards_caddy_port(self, vagrantfile: str, compose: dict):
        """Vagrantfile must forward the Caddy port that compose.yml exposes."""
        caddy = compose["services"]["caddy"]
        ports = caddy.get("ports", [])
        # Find the main proxy port (not admin)
        for p in ports:
            p_str = str(p)
            if "8080" in p_str:
                assert re.search(
                    r'forwarded_port.*guest:\s*8080', vagrantfile
                ), "Vagrant must forward the Caddy proxy port 8080"

    def test_vagrant_forwards_postgres_port(self, vagrantfile: str, compose: dict):
        """Vagrantfile must forward PostgreSQL port."""
        assert re.search(
            r'forwarded_port.*guest:\s*5432', vagrantfile
        ), "Vagrant must forward PostgreSQL port"

    def test_vagrant_does_not_forward_internal_service_ports(self, vagrantfile: str):
        """Services routed through Caddy should not have direct Vagrant port forwards."""
        active_lines = [
            line for line in vagrantfile.split("\n")
            if "forwarded_port" in line and not line.strip().startswith("#")
        ]
        # These ports should NOT be forwarded (Caddy handles them)
        internal_ports = {
            "3000": "web/grafana",
            "4000": "api",
            "9090": "prometheus",
            "8081": "adminer",
        }
        for port, service in internal_ports.items():
            port_forwards = [
                l for l in active_lines
                if f"guest: {port}" in l or f"guest:{port}" in l
            ]
            assert len(port_forwards) == 0, (
                f"Port {port} ({service}) should not be forwarded — "
                f"Caddy handles routing. Found: {port_forwards}"
            )


class TestMonitoringSubpathConsistency:
    """Validate that monitoring subpath config is consistent across
    Caddyfile, compose environment, and healthchecks."""

    @pytest.fixture
    def compose_monitoring(self) -> dict:
        return load_compose("docker/compose.monitoring.yml")

    @pytest.fixture
    def caddyfile(self) -> str:
        return read_file("docker/caddy/Caddyfile")

    def test_grafana_root_url_matches_caddy_route(
        self, compose_monitoring: dict, caddyfile: str
    ):
        """Grafana GF_SERVER_ROOT_URL path must match Caddyfile route."""
        grafana = compose_monitoring["services"]["grafana"]
        root_url = grafana["environment"].get("GF_SERVER_ROOT_URL", "")
        # Root URL should contain /grafana/
        assert "/grafana/" in root_url
        # And Caddyfile should route /grafana/*
        assert "handle /grafana/*" in caddyfile

    def test_prometheus_external_url_matches_caddy_route(
        self, compose_monitoring: dict, caddyfile: str
    ):
        """Prometheus --web.external-url must match Caddy route path."""
        prom = compose_monitoring["services"]["prometheus"]
        command = " ".join(str(c) for c in prom.get("command", []))
        # Extract external URL
        ext_url_match = re.search(r'--web\.external-url=(\S+)', command)
        assert ext_url_match, "Prometheus must have --web.external-url"
        ext_url = ext_url_match.group(1)
        assert "/prometheus" in ext_url
        # And Caddyfile should route /prometheus/*
        assert "handle /prometheus/*" in caddyfile

    def test_prometheus_route_prefix_matches_external_url(
        self, compose_monitoring: dict
    ):
        """Prometheus --web.route-prefix must be consistent with --web.external-url."""
        prom = compose_monitoring["services"]["prometheus"]
        command = " ".join(str(c) for c in prom.get("command", []))

        route_prefix_match = re.search(r'--web\.route-prefix=(\S+)', command)
        ext_url_match = re.search(r'--web\.external-url=(\S+)', command)

        assert route_prefix_match, "Prometheus must have --web.route-prefix"
        assert ext_url_match, "Prometheus must have --web.external-url"

        route_prefix = route_prefix_match.group(1)
        ext_url = ext_url_match.group(1)

        # The external URL should end with the route prefix
        assert ext_url.rstrip("/").endswith(route_prefix.rstrip("/")), (
            f"Prometheus external URL '{ext_url}' must end with route prefix '{route_prefix}'"
        )

    def test_grafana_subpath_mode_enabled(self, compose_monitoring: dict):
        """Grafana must have GF_SERVER_SERVE_FROM_SUB_PATH=true."""
        grafana = compose_monitoring["services"]["grafana"]
        env = grafana.get("environment", {})
        assert env.get("GF_SERVER_SERVE_FROM_SUB_PATH") == "true"

    def test_caddy_preserves_prefix_for_subpath_services(self, caddyfile: str):
        """Services that serve from subpath (Grafana, Prometheus) must use
        'handle' (not 'handle_path') so the prefix is preserved."""
        # Grafana and Prometheus serve from subpath — need prefix
        assert "handle /grafana/*" in caddyfile
        assert "handle /prometheus/*" in caddyfile
        # Must NOT use handle_path for these
        assert "handle_path /grafana/*" not in caddyfile
        assert "handle_path /prometheus/*" not in caddyfile

    def test_caddy_strips_prefix_for_non_subpath_services(self, caddyfile: str):
        """Services that don't serve from subpath (API, Adminer) must use
        'handle_path' so the prefix is stripped."""
        assert "handle_path /api/*" in caddyfile
        assert "handle_path /adminer/*" in caddyfile

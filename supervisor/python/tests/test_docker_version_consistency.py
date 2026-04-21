"""Docker image version consistency tests.

Validates that:
- versions.env is the single source of truth for pinned Docker image versions.
- All compose files use variable substitution with fallback defaults matching versions.env.
- PostgreSQL stays at major version 16 (no accidental major-version jumps).
- Monitoring images are pinned to specific versions (never :latest).
- No dev/prod version skew exists.

Related commit: chore: upgrade Docker images to latest fixed versions
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


def _read_file(relative_path: str) -> str:
    return (_project_root() / relative_path).read_text()


def _parse_versions_env(text: str) -> dict[str, str]:
    """Parse versions.env into a dict of VAR=VALUE pairs."""
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def versions_env() -> dict[str, str]:
    return _parse_versions_env(_read_file("docker/versions.env"))


@pytest.fixture
def compose_yml() -> dict:
    return _load_compose("docker/compose.yml")


@pytest.fixture
def compose_prod() -> dict:
    return _load_compose("docker/compose.prod.yml")


@pytest.fixture
def compose_dev() -> dict:
    return _load_compose("docker/compose.dev.yml")


@pytest.fixture
def compose_monitoring() -> dict:
    return _load_compose("docker/compose.monitoring.yml")


@pytest.fixture
def compose_yml_raw() -> str:
    return _read_file("docker/compose.yml")


@pytest.fixture
def compose_prod_raw() -> str:
    return _read_file("docker/compose.prod.yml")


@pytest.fixture
def compose_dev_raw() -> str:
    return _read_file("docker/compose.dev.yml")


@pytest.fixture
def compose_monitoring_raw() -> str:
    return _read_file("docker/compose.monitoring.yml")


# ===========================================================================
# versions.env completeness
# ===========================================================================


class TestVersionsEnvCompleteness:
    """versions.env must track every pinned Docker image used in the project."""

    EXPECTED_KEYS = [
        "AQUARCO_POSTGRES_VERSION",
        "AQUARCO_CADDY_VERSION",
        "AQUARCO_PROMETHEUS_VERSION",
        "AQUARCO_GRAFANA_VERSION",
        "AQUARCO_LOKI_VERSION",
        "AQUARCO_ADMINER_VERSION",
    ]

    def test_all_image_version_vars_present(self, versions_env: dict[str, str]) -> None:
        """Every expected image version variable must be defined in versions.env."""
        for key in self.EXPECTED_KEYS:
            assert key in versions_env, (
                f"versions.env is missing {key}. "
                "It should be the single source of truth for all Docker image versions."
            )

    def test_no_latest_tags(self, versions_env: dict[str, str]) -> None:
        """No version variable should be set to 'latest'."""
        for key in self.EXPECTED_KEYS:
            value = versions_env.get(key, "")
            assert value.lower() != "latest", (
                f"{key}={value} uses 'latest' — pin to a specific version."
            )

    def test_versions_are_nonempty(self, versions_env: dict[str, str]) -> None:
        """Every version variable must have a non-empty value."""
        for key in self.EXPECTED_KEYS:
            value = versions_env.get(key, "")
            assert value, f"{key} is empty in versions.env."


# ===========================================================================
# PostgreSQL version safety
# ===========================================================================


class TestPostgresVersionSafety:
    """PostgreSQL must stay on major version 16 across all compose files."""

    def test_versions_env_postgres_is_v16(self, versions_env: dict[str, str]) -> None:
        """versions.env must pin PostgreSQL to 16-alpine."""
        pg_version = versions_env.get("AQUARCO_POSTGRES_VERSION", "")
        assert pg_version.startswith("16"), (
            f"AQUARCO_POSTGRES_VERSION={pg_version} — expected major version 16. "
            "A major version upgrade requires a pg_upgrade migration plan."
        )

    def test_compose_yml_postgres_is_v16(self, compose_yml: dict) -> None:
        """compose.yml postgres image must use version 16 with clean variable substitution."""
        image = compose_yml["services"]["postgres"]["image"]
        assert "16" in image, (
            f"compose.yml postgres image is '{image}' — expected version 16."
        )
        # Ensure no stray characters in the variable substitution
        assert image == "postgres:${AQUARCO_POSTGRES_VERSION:-16-alpine}", (
            f"compose.yml postgres image has unexpected characters: '{image}'. "
            "Expected 'postgres:${{AQUARCO_POSTGRES_VERSION:-16-alpine}}'."
        )

    def test_compose_prod_postgres_fallback_is_v16(self, compose_prod_raw: str) -> None:
        """compose.prod.yml postgres fallback default must be 16-alpine."""
        match = re.search(
            r'image:\s*postgres:\$\{AQUARCO_POSTGRES_VERSION:-([^}]+)\}',
            compose_prod_raw,
        )
        assert match, "compose.prod.yml postgres must use variable substitution."
        fallback = match.group(1)
        assert fallback.startswith("16"), (
            f"compose.prod.yml postgres fallback is '{fallback}' — expected 16-alpine."
        )


# ===========================================================================
# Variable substitution consistency
# ===========================================================================


class TestVariableSubstitutionConsistency:
    """Compose files must use variable substitution with fallbacks matching versions.env."""

    def test_compose_prod_uses_postgres_var(self, compose_prod_raw: str) -> None:
        """compose.prod.yml postgres image must reference AQUARCO_POSTGRES_VERSION."""
        assert "AQUARCO_POSTGRES_VERSION" in compose_prod_raw, (
            "compose.prod.yml must use ${AQUARCO_POSTGRES_VERSION:-...} for postgres."
        )

    def test_compose_prod_uses_caddy_var(self, compose_prod_raw: str) -> None:
        """compose.prod.yml caddy image must reference AQUARCO_CADDY_VERSION."""
        assert "AQUARCO_CADDY_VERSION" in compose_prod_raw, (
            "compose.prod.yml must use ${AQUARCO_CADDY_VERSION:-...} for caddy."
        )

    def test_compose_prod_uses_adminer_var(self, compose_prod_raw: str) -> None:
        """compose.prod.yml adminer image must reference AQUARCO_ADMINER_VERSION."""
        assert "AQUARCO_ADMINER_VERSION" in compose_prod_raw, (
            "compose.prod.yml must use ${AQUARCO_ADMINER_VERSION:-...} for adminer."
        )

    def test_compose_dev_uses_adminer_var(self, compose_dev_raw: str) -> None:
        """compose.dev.yml adminer image must reference AQUARCO_ADMINER_VERSION."""
        assert "AQUARCO_ADMINER_VERSION" in compose_dev_raw, (
            "compose.dev.yml must use ${AQUARCO_ADMINER_VERSION:-...} for adminer."
        )

    def test_compose_monitoring_uses_prometheus_var(self, compose_monitoring_raw: str) -> None:
        """compose.monitoring.yml must reference AQUARCO_PROMETHEUS_VERSION."""
        assert "AQUARCO_PROMETHEUS_VERSION" in compose_monitoring_raw

    def test_compose_monitoring_uses_grafana_var(self, compose_monitoring_raw: str) -> None:
        """compose.monitoring.yml must reference AQUARCO_GRAFANA_VERSION."""
        assert "AQUARCO_GRAFANA_VERSION" in compose_monitoring_raw

    def test_compose_monitoring_uses_loki_var(self, compose_monitoring_raw: str) -> None:
        """compose.monitoring.yml must reference AQUARCO_LOKI_VERSION."""
        assert "AQUARCO_LOKI_VERSION" in compose_monitoring_raw


class TestFallbackDefaultsMatchVersionsEnv:
    """Fallback defaults in compose files must match the values in versions.env."""

    @staticmethod
    def _extract_fallbacks(raw_text: str) -> dict[str, str]:
        """Extract all ${VAR:-default} patterns from raw compose text."""
        fallbacks = {}
        for match in re.finditer(r'\$\{(AQUARCO_\w+_VERSION):-([^}]+)\}', raw_text):
            fallbacks[match.group(1)] = match.group(2)
        return fallbacks

    def test_compose_prod_fallbacks_match(
        self, compose_prod_raw: str, versions_env: dict[str, str]
    ) -> None:
        """Every fallback default in compose.prod.yml must match versions.env."""
        fallbacks = self._extract_fallbacks(compose_prod_raw)
        for var, fallback in fallbacks.items():
            expected = versions_env.get(var)
            if expected is not None:
                assert fallback == expected, (
                    f"compose.prod.yml fallback for {var} is '{fallback}' "
                    f"but versions.env says '{expected}'."
                )

    def test_compose_monitoring_fallbacks_match(
        self, compose_monitoring_raw: str, versions_env: dict[str, str]
    ) -> None:
        """Every fallback default in compose.monitoring.yml must match versions.env."""
        fallbacks = self._extract_fallbacks(compose_monitoring_raw)
        for var, fallback in fallbacks.items():
            expected = versions_env.get(var)
            if expected is not None:
                assert fallback == expected, (
                    f"compose.monitoring.yml fallback for {var} is '{fallback}' "
                    f"but versions.env says '{expected}'."
                )

    def test_compose_dev_fallbacks_match(
        self, compose_dev_raw: str, versions_env: dict[str, str]
    ) -> None:
        """Every fallback default in compose.dev.yml must match versions.env."""
        fallbacks = self._extract_fallbacks(compose_dev_raw)
        for var, fallback in fallbacks.items():
            expected = versions_env.get(var)
            if expected is not None:
                assert fallback == expected, (
                    f"compose.dev.yml fallback for {var} is '{fallback}' "
                    f"but versions.env says '{expected}'."
                )

    def test_compose_yml_fallbacks_match(
        self, compose_yml_raw: str, versions_env: dict[str, str]
    ) -> None:
        """Every fallback default in compose.yml must match versions.env."""
        fallbacks = self._extract_fallbacks(compose_yml_raw)
        for var, fallback in fallbacks.items():
            expected = versions_env.get(var)
            if expected is not None:
                assert fallback == expected, (
                    f"compose.yml fallback for {var} is '{fallback}' "
                    f"but versions.env says '{expected}'."
                )


# ===========================================================================
# No :latest tags in compose files
# ===========================================================================


class TestNoLatestTags:
    """No compose file should use :latest for any image."""

    @pytest.mark.parametrize(
        "compose_file",
        [
            "docker/compose.yml",
            "docker/compose.prod.yml",
            "docker/compose.dev.yml",
            "docker/compose.monitoring.yml",
        ],
    )
    def test_no_latest_image_tags(self, compose_file: str) -> None:
        """Image tags must never be ':latest' — pin to specific versions."""
        raw = _read_file(compose_file)
        # Match image: foo:latest (but not ${VAR:-latest} which is a fallback)
        latest_matches = re.findall(r'image:\s*\S+:latest\b', raw)
        assert not latest_matches, (
            f"{compose_file} contains ':latest' image tags: {latest_matches}. "
            "Pin all images to specific versions."
        )


# ===========================================================================
# Monitoring images are pinned (not :latest)
# ===========================================================================


class TestMonitoringImagesPinned:
    """Monitoring images must be pinned to specific version tags."""

    def test_prometheus_pinned(self, versions_env: dict[str, str]) -> None:
        """Prometheus must be pinned to a specific version."""
        version = versions_env.get("AQUARCO_PROMETHEUS_VERSION", "")
        assert re.match(r'^v?\d+\.\d+', version), (
            f"Prometheus version '{version}' doesn't look like a semver pin."
        )

    def test_grafana_pinned(self, versions_env: dict[str, str]) -> None:
        """Grafana must be pinned to a specific version."""
        version = versions_env.get("AQUARCO_GRAFANA_VERSION", "")
        assert re.match(r'^\d+\.\d+', version), (
            f"Grafana version '{version}' doesn't look like a semver pin."
        )

    def test_loki_pinned(self, versions_env: dict[str, str]) -> None:
        """Loki must be pinned to a specific version."""
        version = versions_env.get("AQUARCO_LOKI_VERSION", "")
        assert re.match(r'^\d+\.\d+', version), (
            f"Loki version '{version}' doesn't look like a semver pin."
        )

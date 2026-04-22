"""Docker image version consistency tests.

Validates that:
- versions.env is the single source of truth for pinned Docker image versions.
- All compose files use variable substitution with fallback defaults matching versions.env.
- PostgreSQL stays at major version 18 (no accidental major-version jumps).
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
    """PostgreSQL must stay on major version 18 across all compose files."""

    def test_versions_env_postgres_is_v18(self, versions_env: dict[str, str]) -> None:
        """versions.env must pin PostgreSQL to 18-alpine."""
        pg_version = versions_env.get("AQUARCO_POSTGRES_VERSION", "")
        assert pg_version.startswith("18"), (
            f"AQUARCO_POSTGRES_VERSION={pg_version} — expected major version 18. "
            "A major version upgrade requires a pg_upgrade migration plan."
        )

    def test_compose_yml_postgres_is_v18(self, compose_yml: dict) -> None:
        """compose.yml postgres image must use version 18."""
        image = compose_yml["services"]["postgres"]["image"]
        assert "18" in image, (
            f"compose.yml postgres image is '{image}' — expected version 18."
        )

    def test_compose_prod_postgres_fallback_is_v18(self, compose_prod_raw: str) -> None:
        """compose.prod.yml postgres fallback default must start with 18."""
        match = re.search(
            r'image:\s*postgres:\$\{AQUARCO_POSTGRES_VERSION:-([^}]+)\}',
            compose_prod_raw,
        )
        assert match, "compose.prod.yml postgres must use variable substitution."
        fallback = match.group(1)
        # Major-version jumps require a pg_upgrade migration plan. Fallback
        # must track the main-stack version (currently 18). The previous
        # assertion asserted `startswith("16")` — a stale literal left over
        # from the pre-pg18 era that made this test silently wrong.
        assert fallback.startswith("18"), (
            f"compose.prod.yml postgres fallback is '{fallback}' — expected 18-alpine."
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


# ===========================================================================
# PostgreSQL volume mount layout (pg18 requirement)
# ===========================================================================


class TestPostgresVolumeMountLayout:
    """The pgdata volume mount point must match the PGDATA layout the
    configured postgres image uses.

    postgres:18 stores PGDATA at /var/lib/postgresql/<MAJOR>/docker and
    requires the host volume to be mounted at /var/lib/postgresql (NOT at
    the legacy /var/lib/postgresql/data). The main-stack compose files
    (compose.yml, compose.prod.yml) therefore use the new mount point.

    Drift between dev and prod mount points is a ship-stopper: backups
    captured on one layout cannot be restored into the other without manual
    intervention, and the `aquarco update` safety guard relies on both files
    agreeing on where PG_VERSION lives.
    """

    @staticmethod
    def _postgres_mount_path(compose: dict) -> str:
        """Return the host→container mount path for the pgdata named volume."""
        volumes = compose["services"]["postgres"].get("volumes", [])
        for entry in volumes:
            # entries are strings of the form "named_volume:/container/path[:ro]"
            if not isinstance(entry, str):
                continue
            if entry.startswith("pgdata:"):
                # drop the "pgdata:" prefix and any trailing ":ro"/":rw"
                rest = entry.split(":", 1)[1]
                return rest.split(":", 1)[0]
        raise AssertionError(
            "postgres service has no `pgdata:` named-volume mount."
        )

    def test_compose_yml_mounts_pgdata_at_postgres_root(
        self, compose_yml: dict
    ) -> None:
        """dev compose must mount pgdata at /var/lib/postgresql (pg18 layout)."""
        mount = self._postgres_mount_path(compose_yml)
        assert mount == "/var/lib/postgresql", (
            f"compose.yml pgdata mount is '{mount}' — expected "
            "'/var/lib/postgresql' for pg18 layout. The legacy "
            "'/var/lib/postgresql/data' mount will cause pg18 to initialise "
            "a fresh empty cluster and orphan existing data."
        )

    def test_compose_prod_mounts_pgdata_at_postgres_root(
        self, compose_prod: dict
    ) -> None:
        """prod compose must mount pgdata at /var/lib/postgresql (pg18 layout)."""
        mount = self._postgres_mount_path(compose_prod)
        assert mount == "/var/lib/postgresql", (
            f"compose.prod.yml pgdata mount is '{mount}' — expected "
            "'/var/lib/postgresql' for pg18 layout."
        )

    def test_dev_and_prod_mount_points_agree(
        self, compose_yml: dict, compose_prod: dict
    ) -> None:
        """dev and prod compose files must not drift on the pgdata mount path.

        Backups captured on one layout cannot be restored into the other, and
        the pre-flight check in `get_postgres_version_mismatch()` assumes both
        files agree.
        """
        dev_mount = self._postgres_mount_path(compose_yml)
        prod_mount = self._postgres_mount_path(compose_prod)
        assert dev_mount == prod_mount, (
            f"compose.yml mounts pgdata at '{dev_mount}' but "
            f"compose.prod.yml mounts it at '{prod_mount}'. The two must "
            "stay in sync so backup/restore and the version-mismatch "
            "pre-flight check work identically on dev and prod VMs."
        )


# ===========================================================================
# PGDATA is pinned explicitly (not inherited from image default)
# ===========================================================================


class TestPostgresPgdataExplicit:
    """PGDATA must be set explicitly on the postgres service so the on-disk
    data path is pinned by config rather than inherited from an implicit
    image default.

    Rationale: `postgres:18-alpine` defaults PGDATA to
    `/var/lib/postgresql/18/docker`, but relying on that implicit default
    means a future image bump could silently relocate the data path and
    break backup/restore, the `aquarco update` pre-flight check, and any
    ops muscle memory. Pinning PGDATA in compose makes the behaviour
    deterministic and reviewable.
    """

    @staticmethod
    def _postgres_pgdata(compose: dict) -> str | None:
        """Return the explicit PGDATA env value for the postgres service, or
        None if not set. Strips Compose variable substitution syntax so the
        default value (`${VAR:-DEFAULT}` → `DEFAULT`) is returned.
        """
        env = compose["services"]["postgres"].get("environment", {})
        # environment may be dict or list-of-strings
        if isinstance(env, list):
            for entry in env:
                if isinstance(entry, str) and entry.startswith("PGDATA="):
                    return entry.split("=", 1)[1]
            return None
        value = env.get("PGDATA")
        if value is None:
            return None
        # Accept either a plain string or a ${VAR:-default} form; extract
        # the default so the test pins the value actually used in the
        # common case.
        match = re.match(r'^\$\{[^:}]+:-([^}]+)\}$', str(value))
        if match:
            return match.group(1)
        return str(value)

    def test_compose_yml_sets_pgdata_explicitly(
        self, compose_yml: dict
    ) -> None:
        """dev compose must pin PGDATA explicitly."""
        pgdata = self._postgres_pgdata(compose_yml)
        assert pgdata is not None, (
            "compose.yml postgres service has no explicit PGDATA env var. "
            "PGDATA must be pinned in compose so the on-disk data path is "
            "deterministic and not subject to silent image-default changes."
        )
        assert pgdata == "/var/lib/postgresql/18/docker", (
            f"compose.yml PGDATA is '{pgdata}' — expected "
            "'/var/lib/postgresql/18/docker' to match the postgres:18-alpine "
            "image default. If postgres is being bumped, update both this "
            "test and AQUARCO_POSTGRES_VERSION together."
        )

    def test_compose_prod_sets_pgdata_explicitly(
        self, compose_prod: dict
    ) -> None:
        """prod compose must pin PGDATA explicitly."""
        pgdata = self._postgres_pgdata(compose_prod)
        assert pgdata is not None, (
            "compose.prod.yml postgres service has no explicit PGDATA env var."
        )
        assert pgdata == "/var/lib/postgresql/18/docker", (
            f"compose.prod.yml PGDATA is '{pgdata}' — expected "
            "'/var/lib/postgresql/18/docker'."
        )

    def test_dev_and_prod_pgdata_agree(
        self, compose_yml: dict, compose_prod: dict
    ) -> None:
        """dev and prod must pin PGDATA to the same path.

        Backups/restores straddle both environments; the PG_VERSION-read
        path in `get_postgres_version_mismatch()` also assumes agreement.
        """
        dev_pgdata = self._postgres_pgdata(compose_yml)
        prod_pgdata = self._postgres_pgdata(compose_prod)
        assert dev_pgdata == prod_pgdata, (
            f"compose.yml PGDATA='{dev_pgdata}' but "
            f"compose.prod.yml PGDATA='{prod_pgdata}'. The two must agree."
        )


# ===========================================================================
# Compose variable interpolations must be plain ASCII
# ===========================================================================


class TestPerRepoTemplatePostgresCoupling:
    """The per-repo sandbox template (``supervisor/templates/docker-compose.repo.yml.tmpl``)
    intentionally stays on postgres:16-alpine with the legacy PGDATA layout
    (``pgdata:/var/lib/postgresql/data``) while the main stack moves to
    postgres:18-alpine with ``pgdata:/var/lib/postgresql``.

    This is a fragile coupling: the mount path and postgres major must stay
    in lockstep, otherwise a future maintainer bumping one without the other
    will silently orphan data on next boot (pg18 expects PGDATA under a
    versioned subdir, pg≤16 expects it at the volume root).

    These tests lock in the invariant so a bump of the postgres image in this
    template forces the mount path to be updated in the same commit (and
    vice versa).
    """

    TEMPLATE_PATH = "supervisor/templates/docker-compose.repo.yml.tmpl"

    @staticmethod
    def _postgres_image(text: str) -> str:
        """Extract the image tag used by the postgres service in a compose-file
        template. Returns the raw ``image:`` value (e.g. ``postgres:16-alpine``).
        """
        # The template file is not valid YAML because of ``__PLACEHOLDER__``
        # tokens and sed-style comments, so we parse it as text rather than
        # feeding it through yaml.safe_load.
        match = re.search(
            r'^\s*postgres:\s*\n(?:[^\n]*\n)*?\s*image:\s*(\S+)',
            text,
            re.MULTILINE,
        )
        assert match, (
            "could not locate a postgres service `image:` directive in "
            f"{TestPerRepoTemplatePostgresCoupling.TEMPLATE_PATH}"
        )
        return match.group(1)

    @staticmethod
    def _postgres_mount(text: str) -> str:
        """Extract the ``pgdata:<path>`` mount path used by the postgres service
        in the per-repo template.
        """
        match = re.search(r'-\s*pgdata:(\S+)', text)
        assert match, (
            "could not locate a `pgdata:` volume mount in "
            f"{TestPerRepoTemplatePostgresCoupling.TEMPLATE_PATH}"
        )
        # strip any trailing `:ro`/`:rw` modifier
        return match.group(1).split(":", 1)[0]

    def test_template_uses_postgres_16(self) -> None:
        """Per-repo template must stay on postgres:16-alpine until the mount
        path is also updated to the pg18 layout. If this assertion fails it
        likely means someone bumped the image without updating the mount —
        see the test below for the coupled invariant.
        """
        raw = _read_file(self.TEMPLATE_PATH)
        image = self._postgres_image(raw)
        assert image == "postgres:16-alpine", (
            f"per-repo template postgres image is '{image}' — expected "
            "'postgres:16-alpine'. If this image is being bumped, the "
            "`pgdata:` mount path in the same file MUST be updated to "
            "'/var/lib/postgresql' to match the pg18 layout (see the main "
            "stack compose.yml / compose.prod.yml for the pattern)."
        )

    def test_template_uses_legacy_pgdata_mount(self) -> None:
        """Per-repo template must mount pgdata at the legacy
        ``/var/lib/postgresql/data`` path because it runs pg16. If this
        assertion fails the template has drifted — either the mount was
        moved without bumping the image (data-loss regression on next boot)
        or the image was bumped without this test being updated.
        """
        raw = _read_file(self.TEMPLATE_PATH)
        mount = self._postgres_mount(raw)
        assert mount == "/var/lib/postgresql/data", (
            f"per-repo template pgdata mount is '{mount}' — expected "
            "'/var/lib/postgresql/data' to match the pg16 legacy PGDATA "
            "layout. Mount path and postgres major MUST change together: "
            "pg≤16 → '/var/lib/postgresql/data'; pg≥18 → '/var/lib/postgresql'."
        )

    def test_template_image_and_mount_are_coupled(self) -> None:
        """Structurally pin the image/mount coupling rule so any future edit
        that breaks the pairing fails loudly rather than causing silent
        data loss on next ``docker compose up``.

        Rule (same as the main stack):
          - image major ≤ 16  →  mount at ``/var/lib/postgresql/data``
          - image major ≥ 18  →  mount at ``/var/lib/postgresql``
        """
        raw = _read_file(self.TEMPLATE_PATH)
        image = self._postgres_image(raw)
        mount = self._postgres_mount(raw)

        major_match = re.search(r'postgres:(\d+)', image)
        assert major_match, (
            f"postgres image '{image}' does not match the expected "
            "'postgres:<MAJOR>[-suffix]' pattern."
        )
        major = int(major_match.group(1))

        if major <= 16:
            assert mount == "/var/lib/postgresql/data", (
                f"postgres:{major} uses the legacy PGDATA layout but the "
                f"template mounts pgdata at '{mount}'. The pg≤16 mount must "
                "be '/var/lib/postgresql/data'."
            )
        elif major >= 18:
            assert mount == "/var/lib/postgresql", (
                f"postgres:{major} uses the versioned PGDATA layout but the "
                f"template mounts pgdata at '{mount}'. The pg≥18 mount must "
                "be '/var/lib/postgresql' (the image stores PGDATA at "
                "'/var/lib/postgresql/<MAJOR>/docker' — the volume must be "
                "mounted one level up)."
            )
        else:
            pytest.fail(
                f"postgres:{major} is in an untested range (17). The pg17 "
                "PGDATA layout needs to be verified before this test can pass."
            )


class TestComposeVariableInterpolationsAreAscii:
    """Variable *names* inside `${...}` interpolations must be plain ASCII.

    Lesson learned from a stray `◊` (U+25CA) lozenge that slipped into
    `${◊AQUARCO_POSTGRES_VERSION:-18-alpine}` and would have silently broken
    the default-tag fallback: Docker Compose does not substitute a variable
    whose name contains a non-ASCII character, so the literal text would have
    ended up as the image tag. Easier to catch in a lint test than at deploy
    time.

    We only check the variable-name portion (before the first `:` separator)
    because `${VAR:?message}` error messages legitimately contain non-ASCII
    prose such as em-dashes.
    """

    @pytest.mark.parametrize(
        "compose_file",
        [
            "docker/compose.yml",
            "docker/compose.prod.yml",
            "docker/compose.dev.yml",
            "docker/compose.monitoring.yml",
        ],
    )
    def test_no_non_ascii_in_variable_names(self, compose_file: str) -> None:
        raw = _read_file(compose_file)
        bad: list[tuple[int, str]] = []
        for line_no, line in enumerate(raw.splitlines(), start=1):
            for match in re.finditer(r'\$\{([^}]*)\}', line):
                inner = match.group(1)
                # Split off the optional `:-default` or `:?message` suffix.
                var_name = inner.split(":", 1)[0]
                if not var_name.isascii():
                    bad.append((line_no, match.group(0)))
        assert not bad, (
            f"{compose_file} contains non-ASCII characters in variable names "
            f"inside ${{...}} interpolations: {bad}. Docker Compose will not "
            "substitute a variable whose name contains non-ASCII characters, "
            "so the literal text would leak into the rendered value."
        )

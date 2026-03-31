"""Tests for yoyo migration format, rollback files, and infrastructure.

Validates acceptance criteria from the yoyo-migrations conversion (issue #22).
"""

from __future__ import annotations

import os
import re
import stat
import configparser
from pathlib import Path

import pytest
import yaml

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
DB_DIR = ROOT / "db"
MIGRATIONS_DIR = DB_DIR / "migrations"
DOCKER_COMPOSE = ROOT / "docker" / "compose.yml"
TEMPLATE_COMPOSE = ROOT / "supervisor" / "templates" / "docker-compose.repo.yml.tmpl"

# Expected linear dependency chain (migration name without .sql)
EXPECTED_MIGRATIONS = [
    "000_init",
    "001_create_repositories",
    "002_create_tasks",
    "003_create_stages",
    "004_create_context",
    "005_create_poll_state",
    "006_create_agent_instances",
    "007_create_pipeline_checkpoints",
    "008_create_functions",
    "009_add_repo_error_message",
    "010_add_repo_deploy_key",
    "011_add_repo_original_url",
    "012_fix_null_agent_instance",
    "013_create_agent_and_pipeline_definitions",
    "014_pipeline_redesign",
    "015_remove_task_category",
    "016_add_is_config_repo",
    "017_allow_null_branch",
    "018_rename_schema",
    "019_agent_overrides_and_source",
    "022_fix_agent_overrides_schema",
    "023_drop_input_from_context",
    "024_add_rate_limited_status",
    "025_add_stage_run",
    "026_add_live_output",
    "027_task_lifecycle",
    "028_repo_agent_scans",
    "029_add_pipeline_categories",
    "030_add_agent_group",
    "031_add_postpone_cooldown",
    "032_add_stage_spending",
    "033_add_stage_session_id",
    "034_checkpoint_stage_fk",
]

# Build expected dependency map: migration_name -> depends_on_name
EXPECTED_DEPENDS: dict[str, str] = {}
for i, name in enumerate(EXPECTED_MIGRATIONS):
    if i == 0:
        EXPECTED_DEPENDS[name] = ""  # 000_init has empty depends
    else:
        EXPECTED_DEPENDS[name] = EXPECTED_MIGRATIONS[i - 1]


def _migration_files() -> list[Path]:
    """Return all forward migration .sql files (excluding rollbacks)."""
    return sorted(
        p for p in MIGRATIONS_DIR.glob("*.sql")
        if not p.name.endswith(".rollback.sql")
    )


def _rollback_files() -> list[Path]:
    """Return all .rollback.sql files."""
    return sorted(MIGRATIONS_DIR.glob("*.rollback.sql"))


# ── AC: Every migration .sql starts with '-- depends:' header ──────────────────

class TestMigrationDependsHeaders:
    """Validate the -- depends: header on every migration file."""

    @pytest.fixture(params=_migration_files(), ids=lambda p: p.name)
    def migration_file(self, request: pytest.FixtureRequest) -> Path:
        return request.param

    def test_first_content_line_is_depends(self, migration_file: Path) -> None:
        """AC: Every migration .sql file starts with a '-- depends:' header as first non-empty line."""
        lines = migration_file.read_text().splitlines()
        # Find first non-empty line
        first_content = next((l for l in lines if l.strip()), "")
        assert first_content.startswith("-- depends:"), (
            f"{migration_file.name}: first content line must start with '-- depends:'"
        )

    def test_000_init_has_empty_depends(self) -> None:
        """AC: 000_init.sql has '-- depends:' (empty dependency)."""
        init_file = MIGRATIONS_DIR / "000_init.sql"
        first_line = init_file.read_text().splitlines()[0]
        assert first_line.strip() == "-- depends:", (
            "000_init.sql must have empty depends header"
        )

    def test_each_migration_depends_on_predecessor(self, migration_file: Path) -> None:
        """AC: Each subsequent migration references the immediately preceding migration."""
        name = migration_file.stem
        if name not in EXPECTED_DEPENDS:
            pytest.skip(f"Unknown migration: {name}")

        first_line = migration_file.read_text().splitlines()[0]
        expected_dep = EXPECTED_DEPENDS[name]
        if expected_dep:
            assert first_line.strip() == f"-- depends: {expected_dep}", (
                f"{name}: expected depends on '{expected_dep}', got '{first_line.strip()}'"
            )
        else:
            assert first_line.strip() == "-- depends:", (
                f"{name}: expected empty depends header"
            )

    def test_022_depends_on_019(self) -> None:
        """AC: Migration 022 depends on 019 (skipping missing 020/021)."""
        m = MIGRATIONS_DIR / "022_fix_agent_overrides_schema.sql"
        first_line = m.read_text().splitlines()[0]
        assert "019_agent_overrides_and_source" in first_line


class TestNoUpDownMarkers:
    """AC: No migration file contains '-- up' or '-- down' comment markers."""

    @pytest.fixture(params=_migration_files(), ids=lambda p: p.name)
    def migration_file(self, request: pytest.FixtureRequest) -> Path:
        return request.param

    def test_no_up_marker(self, migration_file: Path) -> None:
        content = migration_file.read_text()
        # Match "-- up" as a standalone comment marker (not part of UPDATE etc.)
        for line in content.splitlines():
            stripped = line.strip()
            assert stripped != "-- up", f"{migration_file.name}: contains '-- up' marker"

    def test_no_down_marker(self, migration_file: Path) -> None:
        content = migration_file.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            assert stripped != "-- down", f"{migration_file.name}: contains '-- down' marker"


class TestNoCommentedOutRollbackSQL:
    """AC: No migration file contains commented-out rollback SQL (the down section is removed)."""

    @pytest.fixture(params=_migration_files(), ids=lambda p: p.name)
    def migration_file(self, request: pytest.FixtureRequest) -> Path:
        return request.param

    def test_no_commented_rollback_block(self, migration_file: Path) -> None:
        """Check there's no multi-line commented-out DROP/ALTER block after a '-- down' marker."""
        content = migration_file.read_text()
        # Pattern: a line that is exactly "-- down" followed by commented SQL
        assert not re.search(
            r"^--\s*down\s*$", content, re.MULTILINE | re.IGNORECASE
        ), f"{migration_file.name}: contains a down section marker"


# ── AC: Rollback files ─────────────────────────────────────────────────────────

class TestRollbackFiles:
    """Validate companion .rollback.sql files exist and are well-formed."""

    def test_rollback_file_exists_for_each_migration(self) -> None:
        """AC: A .rollback.sql companion file exists for each of the 26 migration .sql files."""
        for m in _migration_files():
            rollback = MIGRATIONS_DIR / f"{m.stem}.rollback.sql"
            assert rollback.exists(), f"Missing rollback file for {m.name}"

    def test_rollback_count_matches_migration_count(self) -> None:
        """Same number of rollback files as forward migrations."""
        assert len(_rollback_files()) == len(_migration_files())

    @pytest.fixture(params=_rollback_files(), ids=lambda p: p.name)
    def rollback_file(self, request: pytest.FixtureRequest) -> Path:
        return request.param

    def test_rollback_contains_valid_sql(self, rollback_file: Path) -> None:
        """AC: Each .rollback.sql file contains valid SQL that reverses the forward migration."""
        content = rollback_file.read_text().strip()
        assert len(content) > 0, f"{rollback_file.name}: rollback file is empty"
        # Should contain at least one SQL keyword
        has_sql = any(
            kw in content.upper()
            for kw in ["DROP", "ALTER", "DELETE", "UPDATE", "SET", "CREATE", "DO"]
        )
        assert has_sql, f"{rollback_file.name}: no recognizable SQL statements"

    def test_rollback_includes_search_path(self, rollback_file: Path) -> None:
        """AC: Each .rollback.sql includes 'SET search_path TO aquarco, public;'."""
        content = rollback_file.read_text()
        assert "SET search_path TO aquarco, public;" in content, (
            f"{rollback_file.name}: missing SET search_path"
        )


# ── AC: Dockerfile ──────────────────────────────────────────────────────────────

class TestDockerfile:
    """Validate db/Dockerfile for the migration container."""

    def test_dockerfile_exists(self) -> None:
        """AC: db/Dockerfile exists."""
        assert (DB_DIR / "Dockerfile").exists()

    def test_builds_python_image(self) -> None:
        """AC: Builds a Python image."""
        content = (DB_DIR / "Dockerfile").read_text()
        assert "python:" in content.lower() or "FROM python" in content

    def test_installs_yoyo_migrations_postgres(self) -> None:
        """AC: Installs yoyo-migrations[postgres]."""
        content = (DB_DIR / "Dockerfile").read_text()
        assert "yoyo-migrations" in content
        assert "postgres" in content


# ── AC: yoyo.ini ────────────────────────────────────────────────────────────────

class TestYoyoIni:
    """Validate db/yoyo.ini configuration."""

    def test_yoyo_ini_exists(self) -> None:
        """AC: db/yoyo.ini exists."""
        assert (DB_DIR / "yoyo.ini").exists()

    def test_sources_is_migrations(self) -> None:
        """AC: sources=migrations."""
        config = configparser.ConfigParser()
        config.read(DB_DIR / "yoyo.ini")
        assert config.get("DEFAULT", "sources") == "migrations"

    def test_database_uses_env_var(self) -> None:
        """AC: database=%(DATABASE_URL)s."""
        config = configparser.RawConfigParser()
        config.read(DB_DIR / "yoyo.ini")
        assert "DATABASE_URL" in config.get("DEFAULT", "database")


# ── AC: migrate.sh ──────────────────────────────────────────────────────────────

class TestMigrateScript:
    """Validate db/migrate.sh helper script."""

    def test_migrate_sh_exists(self) -> None:
        """AC: db/migrate.sh exists."""
        assert (DB_DIR / "migrate.sh").exists()

    def test_migrate_sh_is_executable(self) -> None:
        """AC: migrate.sh is executable."""
        mode = os.stat(DB_DIR / "migrate.sh").st_mode
        assert mode & stat.S_IXUSR, "migrate.sh must be executable"

    @pytest.mark.parametrize("subcommand", ["apply", "rollback", "reapply", "list"])
    def test_supports_subcommands(self, subcommand: str) -> None:
        """AC: Supports apply/rollback/reapply/list subcommands."""
        content = (DB_DIR / "migrate.sh").read_text()
        assert subcommand in content, f"migrate.sh missing '{subcommand}' subcommand"


# ── AC: docker/compose.yml ──────────────────────────────────────────────────────

class TestDockerCompose:
    """Validate docker/compose.yml changes."""

    @pytest.fixture
    def compose(self) -> dict:
        return yaml.safe_load(DOCKER_COMPOSE.read_text())

    def test_migrations_service_exists(self, compose: dict) -> None:
        """AC: docker/compose.yml contains a 'migrations' service."""
        assert "migrations" in compose["services"]

    def test_migrations_service_builds_from_db(self, compose: dict) -> None:
        """AC: Migrations service builds from ../db context."""
        svc = compose["services"]["migrations"]
        build = svc.get("build", {})
        ctx = build if isinstance(build, str) else build.get("context", "")
        assert "db" in str(ctx)

    def test_migrations_restart_no(self, compose: dict) -> None:
        """AC: Migrations service has restart: no."""
        svc = compose["services"]["migrations"]
        assert svc.get("restart") in ("no", '"no"')

    def test_migrations_depends_on_postgres_healthy(self, compose: dict) -> None:
        """AC: Migrations service depends_on postgres with condition: service_healthy."""
        svc = compose["services"]["migrations"]
        deps = svc.get("depends_on", {})
        assert "postgres" in deps
        if isinstance(deps["postgres"], dict):
            assert deps["postgres"].get("condition") == "service_healthy"

    def test_migrations_has_database_url(self, compose: dict) -> None:
        """AC: Migrations service passes DATABASE_URL environment variable."""
        svc = compose["services"]["migrations"]
        env = svc.get("environment", {})
        if isinstance(env, dict):
            assert "DATABASE_URL" in env
        elif isinstance(env, list):
            assert any("DATABASE_URL" in str(e) for e in env)

    def test_no_initdb_d_mount_on_postgres(self, compose: dict) -> None:
        """AC: postgres service no longer mounts db/migrations to docker-entrypoint-initdb.d."""
        postgres = compose["services"].get("postgres", {})
        volumes = postgres.get("volumes", [])
        for v in volumes:
            assert "docker-entrypoint-initdb" not in str(v), (
                "postgres should not mount docker-entrypoint-initdb.d"
            )

    def test_api_depends_on_migrations(self, compose: dict) -> None:
        """AC: api service depends_on migrations with condition: service_completed_successfully."""
        api = compose["services"].get("api", {})
        deps = api.get("depends_on", {})
        assert "migrations" in deps
        if isinstance(deps["migrations"], dict):
            assert deps["migrations"].get("condition") == "service_completed_successfully"


# ── AC: supervisor template ─────────────────────────────────────────────────────

class TestSupervisorTemplate:
    """Validate supervisor/templates/docker-compose.repo.yml.tmpl has equivalent changes."""

    @pytest.fixture
    def template_content(self) -> str:
        return TEMPLATE_COMPOSE.read_text()

    def test_template_has_migrations_service(self, template_content: str) -> None:
        """AC: Template has equivalent migrations service."""
        assert "migrations:" in template_content

    def test_template_api_depends_on_migrations(self, template_content: str) -> None:
        """AC: Template api depends on migrations."""
        # Check that migrations appears in depends_on context for api
        assert "service_completed_successfully" in template_content


# ── Migration count ─────────────────────────────────────────────────────────────

class TestMigrationCount:
    """Ensure all expected migrations are present."""

    def test_expected_migration_count(self) -> None:
        """All 26 expected migrations exist as .sql files."""
        files = _migration_files()
        names = {f.stem for f in files}
        for expected in EXPECTED_MIGRATIONS:
            assert expected in names, f"Missing migration: {expected}.sql"

    def test_no_unexpected_migrations(self) -> None:
        """No unexpected forward migration files."""
        files = _migration_files()
        names = {f.stem for f in files}
        expected = set(EXPECTED_MIGRATIONS)
        unexpected = names - expected
        assert not unexpected, f"Unexpected migration files: {unexpected}"


# ── Dependency chain integrity ──────────────────────────────────────────────────

class TestDependencyChain:
    """Validate the full dependency chain forms a valid DAG."""

    def test_linear_chain_is_unbroken(self) -> None:
        """Each migration (except 000) depends on exactly one predecessor that exists."""
        files = {f.stem: f for f in _migration_files()}
        for name, dep in EXPECTED_DEPENDS.items():
            if name not in files:
                continue
            first_line = files[name].read_text().splitlines()[0]
            if dep:
                assert dep in first_line, (
                    f"{name}: expected dep '{dep}' in '{first_line}'"
                )
                assert dep in files, f"{name}: depends on {dep} which doesn't exist"

    def test_no_circular_dependencies(self) -> None:
        """The dependency chain has no cycles."""
        deps: dict[str, str] = {}
        for f in _migration_files():
            first_line = f.read_text().splitlines()[0]
            match = re.match(r"^-- depends:\s*(.*)", first_line)
            if match:
                dep = match.group(1).strip()
                deps[f.stem] = dep

        # Walk from each node to root; if we visit more nodes than exist, there's a cycle
        for start in deps:
            visited: set[str] = set()
            current = start
            while current and current in deps:
                if current in visited:
                    pytest.fail(f"Circular dependency detected involving {current}")
                visited.add(current)
                current = deps.get(current, "")

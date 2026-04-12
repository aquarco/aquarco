"""Tests for yoyo migration format, rollback files, and infrastructure.

Validates acceptance criteria from the yoyo-migrations conversion (issue #22)
and the migration consolidation (issue #110).
"""

from __future__ import annotations

import os
import re
import stat
import configparser
from pathlib import Path

import pytest
import yaml

# -- Paths --------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
DB_DIR = ROOT / "db"
MIGRATIONS_DIR = DB_DIR / "migrations"
DOCKER_COMPOSE = ROOT / "docker" / "compose.yml"
TEMPLATE_COMPOSE = ROOT / "supervisor" / "templates" / "docker-compose.repo.yml.tmpl"

# After consolidation there is a single canonical migration.
EXPECTED_MIGRATIONS = [
    "000_consolidated_init",
    "001_add_git_flow_config",
    "002_drop_pipeline_trigger_config",
]


def _migration_files() -> list[Path]:
    """Return all forward migration .sql files (excluding rollbacks and archive/)."""
    return sorted(
        p for p in MIGRATIONS_DIR.glob("*.sql")
        if not p.name.endswith(".rollback.sql")
    )


def _rollback_files() -> list[Path]:
    """Return all .rollback.sql files (excluding archive/)."""
    return sorted(MIGRATIONS_DIR.glob("*.rollback.sql"))


# -- AC: Every migration .sql starts with '-- depends:' header ----------------

class TestMigrationDependsHeaders:
    """Validate the -- depends: header on every migration file."""

    @pytest.fixture(params=_migration_files(), ids=lambda p: p.name)
    def migration_file(self, request: pytest.FixtureRequest) -> Path:
        return request.param

    def test_first_content_line_is_depends(self, migration_file: Path) -> None:
        """AC: Every migration .sql file starts with a '-- depends:' header as first non-empty line."""
        lines = migration_file.read_text().splitlines()
        first_content = next((l for l in lines if l.strip()), "")
        assert first_content.startswith("-- depends:"), (
            f"{migration_file.name}: first content line must start with '-- depends:'"
        )

    def test_consolidated_init_has_empty_depends(self) -> None:
        """AC: 000_consolidated_init.sql has '-- depends:' (empty dependency)."""
        init_file = MIGRATIONS_DIR / "000_consolidated_init.sql"
        first_line = init_file.read_text().splitlines()[0]
        assert first_line.strip() == "-- depends:", (
            "000_consolidated_init.sql must have empty depends header"
        )


class TestNoUpDownMarkers:
    """AC: No migration file contains '-- up' or '-- down' comment markers."""

    @pytest.fixture(params=_migration_files(), ids=lambda p: p.name)
    def migration_file(self, request: pytest.FixtureRequest) -> Path:
        return request.param

    def test_no_up_marker(self, migration_file: Path) -> None:
        content = migration_file.read_text()
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
        assert not re.search(
            r"^--\s*down\s*$", content, re.MULTILINE | re.IGNORECASE
        ), f"{migration_file.name}: contains a down section marker"


# -- AC: Rollback files -------------------------------------------------------

class TestRollbackFiles:
    """Validate companion .rollback.sql files exist and are well-formed."""

    def test_rollback_file_exists_for_each_migration(self) -> None:
        """AC: A .rollback.sql companion file exists for each migration .sql file."""
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


# -- AC: Dockerfile ------------------------------------------------------------

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


# -- AC: yoyo.ini -------------------------------------------------------------

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


# -- AC: migrate.sh ------------------------------------------------------------

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

    def test_has_consolidated_guard(self) -> None:
        """AC: migrate.sh contains a pre-flight guard for existing deployments."""
        content = (DB_DIR / "migrate.sh").read_text()
        assert "000_consolidated_init" in content, (
            "migrate.sh must contain a guard referencing 000_consolidated_init"
        )


# -- AC: docker/compose.yml ---------------------------------------------------

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


# -- AC: supervisor template ---------------------------------------------------

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
        assert "service_completed_successfully" in template_content


# -- Migration count -----------------------------------------------------------

class TestMigrationCount:
    """Ensure all expected migrations are present."""

    def test_expected_migration_count(self) -> None:
        """All expected migrations exist as .sql files."""
        files = _migration_files()
        names = {f.stem for f in files}
        assert len(names) == 1, f"Expected 1 migration, found {len(names)}: {names}"
        for expected in EXPECTED_MIGRATIONS:
            assert expected in names, f"Missing migration: {expected}.sql"

    def test_no_unexpected_migrations(self) -> None:
        """No unexpected forward migration files."""
        files = _migration_files()
        names = {f.stem for f in files}
        expected = set(EXPECTED_MIGRATIONS)
        unexpected = names - expected
        assert not unexpected, f"Unexpected migration files: {unexpected}"


# -- Dependency chain integrity ------------------------------------------------

class TestDependencyChain:
    """Validate the full dependency chain forms a valid DAG."""

    def test_single_root_migration(self) -> None:
        """With a single consolidated migration, the chain is trivially valid."""
        files = _migration_files()
        assert len(files) == 1
        first_line = files[0].read_text().splitlines()[0]
        assert first_line.strip() == "-- depends:", (
            "Root migration must have empty depends"
        )

    def test_no_circular_dependencies(self) -> None:
        """The dependency chain has no cycles."""
        deps: dict[str, str] = {}
        for f in _migration_files():
            first_line = f.read_text().splitlines()[0]
            match = re.match(r"^-- depends:\s*(.*)", first_line)
            if match:
                dep = match.group(1).strip()
                deps[f.stem] = dep

        for start in deps:
            visited: set[str] = set()
            current = start
            while current and current in deps:
                if current in visited:
                    pytest.fail(f"Circular dependency detected involving {current}")
                visited.add(current)
                current = deps.get(current, "")


# -- Archive integrity ---------------------------------------------------------

class TestArchive:
    """Validate that old migrations are preserved in the archive directory."""

    ARCHIVE_DIR = MIGRATIONS_DIR / "archive"

    def test_archive_directory_exists(self) -> None:
        """AC: db/migrations/archive/ exists."""
        assert self.ARCHIVE_DIR.is_dir()

    def test_archive_contains_old_migrations(self) -> None:
        """AC: All 88 files (44 forward + 44 rollback) exist under archive/."""
        files = list(self.ARCHIVE_DIR.glob("*.sql"))
        assert len(files) == 86, (
            f"Expected 86 archived files (44+2 dups share prefix 040), found {len(files)}"
        )

    def test_archive_has_init_and_last(self) -> None:
        """AC: Archive contains both the first and last original migrations."""
        assert (self.ARCHIVE_DIR / "000_init.sql").exists()
        assert (self.ARCHIVE_DIR / "043_fix_get_task_context.sql").exists()
        assert (self.ARCHIVE_DIR / "000_init.rollback.sql").exists()
        assert (self.ARCHIVE_DIR / "043_fix_get_task_context.rollback.sql").exists()

    def test_archive_not_discovered_by_glob(self) -> None:
        """AC: Archive files are not discovered by MIGRATIONS_DIR.glob('*.sql')."""
        root_files = {p.name for p in MIGRATIONS_DIR.glob("*.sql")}
        assert "000_init.sql" not in root_files, (
            "000_init.sql should be in archive/, not in migrations root"
        )


# -- Consolidated init content -------------------------------------------------

class TestConsolidatedInitContent:
    """Validate the consolidated init script creates the expected schema objects."""

    @pytest.fixture
    def init_content(self) -> str:
        return (MIGRATIONS_DIR / "000_consolidated_init.sql").read_text()

    EXPECTED_TABLES = [
        "repositories",
        "tasks",
        "stages",
        "context",
        "poll_state",
        "agent_instances",
        "agent_definitions",
        "pipeline_definitions",
        "agent_overrides",
        "validation_items",
        "supervisor_state",
    ]

    @pytest.mark.parametrize("table", EXPECTED_TABLES)
    def test_creates_table(self, init_content: str, table: str) -> None:
        """AC: The consolidated init creates all 11 living tables."""
        assert f"CREATE TABLE {table}" in init_content, (
            f"Missing CREATE TABLE for {table}"
        )

    def test_creates_aquarco_schema(self, init_content: str) -> None:
        """AC: Creates the aquarco schema."""
        assert "CREATE SCHEMA" in init_content
        assert "aquarco" in init_content

    def test_creates_pgcrypto(self, init_content: str) -> None:
        """AC: Enables pgcrypto extension."""
        assert "pgcrypto" in init_content

    def test_creates_get_task_context(self, init_content: str) -> None:
        """AC: Creates get_task_context() function."""
        assert "CREATE OR REPLACE FUNCTION get_task_context" in init_content

    def test_creates_update_updated_at(self, init_content: str) -> None:
        """AC: Creates update_updated_at() trigger function."""
        assert "CREATE OR REPLACE FUNCTION update_updated_at" in init_content

    def test_seeds_supervisor_state(self, init_content: str) -> None:
        """AC: Seeds drain_mode row in supervisor_state."""
        assert "drain_mode" in init_content
        assert "INSERT INTO supervisor_state" in init_content

    def test_no_dropped_tables(self, init_content: str) -> None:
        """AC: Does not create pipeline_checkpoints or repo_agent_scans (net-zero tables)."""
        assert "pipeline_checkpoints" not in init_content
        assert "repo_agent_scans" not in init_content

    def test_no_dropped_columns(self, init_content: str) -> None:
        """AC: get_task_context() does not reference dropped columns."""
        # These columns were dropped in migration 035
        fn_start = init_content.index("CREATE OR REPLACE FUNCTION get_task_context")
        fn_section = init_content[fn_start:]
        assert "t.phase" not in fn_section
        assert "t.current_stage" not in fn_section
        assert "t.assigned_agent" not in fn_section
        assert "t.category" not in fn_section

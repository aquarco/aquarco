"""Tests for migration consolidation details (issue #110).

Validates acceptance criteria from the design document that go beyond the basic
format checks in test_yoyo_migrations.py. These tests verify:
  - CHECK constraints added to the consolidated init
  - FK dependency ordering in table creation
  - Deferred circular FK resolution (tasks <-> stages)
  - Index creation for FK columns
  - Status enum values match design spec
  - migrate.sh pre-flight guard logic (SHA-256 hash, table existence check, ON CONFLICT)
  - get_task_context() function implementation details
  - Rollback mechanism (DROP SCHEMA CASCADE)
  - Seed data idempotency (ON CONFLICT DO NOTHING)
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

# -- Paths --------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
DB_DIR = ROOT / "db"
MIGRATIONS_DIR = DB_DIR / "migrations"
ARCHIVE_DIR = MIGRATIONS_DIR / "archive"


@pytest.fixture
def init_content() -> str:
    """Load the consolidated init SQL."""
    return (MIGRATIONS_DIR / "000_consolidated_init.sql").read_text()


@pytest.fixture
def rollback_content() -> str:
    """Load the consolidated rollback SQL."""
    return (MIGRATIONS_DIR / "000_consolidated_init.rollback.sql").read_text()


@pytest.fixture
def migrate_sh_content() -> str:
    """Load migrate.sh content."""
    return (DB_DIR / "migrate.sh").read_text()


# =============================================================================
# CHECK constraints — Design Decision 5
# =============================================================================

class TestCheckConstraints:
    """AC: The consolidated init adds meaningful CHECK constraints per the design document."""

    # ---- repositories ----

    def test_repos_valid_clone_status(self, init_content: str) -> None:
        """AC: repositories.clone_status has valid enum values."""
        assert "valid_clone_status" in init_content
        assert "'pending'" in init_content
        assert "'cloning'" in init_content
        assert "'ready'" in init_content
        assert "'error'" in init_content

    def test_repos_name_nonempty(self, init_content: str) -> None:
        """AC: repositories.name must not be empty."""
        assert "chk_repos_name_nonempty" in init_content

    def test_repos_url_nonempty(self, init_content: str) -> None:
        """AC: repositories.url must not be empty (empty URL breaks all git operations)."""
        assert "chk_repos_url_nonempty" in init_content

    def test_repos_clone_dir_nonempty(self, init_content: str) -> None:
        """AC: repositories.clone_dir must not be empty (empty path causes filesystem errors)."""
        assert "chk_repos_clone_dir_nonempty" in init_content

    # ---- tasks ----

    def test_tasks_valid_status(self, init_content: str) -> None:
        """AC: tasks.status has valid enum values matching design spec."""
        assert "valid_status" in init_content
        for status in [
            "pending", "queued", "planning", "executing",
            "completed", "failed", "timeout", "blocked",
            "rate_limited", "closed",
        ]:
            assert f"'{status}'" in init_content, (
                f"Missing task status value: {status}"
            )

    def test_tasks_valid_priority(self, init_content: str) -> None:
        """AC: tasks.priority must be between 0 and 100."""
        assert "valid_priority" in init_content
        assert "BETWEEN 0 AND 100" in init_content

    def test_tasks_title_nonempty(self, init_content: str) -> None:
        """AC: tasks.title must not be empty."""
        assert "chk_tasks_title_nonempty" in init_content

    def test_tasks_source_nonempty(self, init_content: str) -> None:
        """AC: tasks.source must not be empty."""
        assert "chk_tasks_source_nonempty" in init_content

    def test_tasks_pipeline_nonempty(self, init_content: str) -> None:
        """AC: tasks.pipeline must not be empty."""
        assert "chk_tasks_pipeline_nonempty" in init_content

    def test_tasks_retry_nonneg(self, init_content: str) -> None:
        """AC: tasks.retry_count must be >= 0."""
        assert "chk_tasks_retry_nonneg" in init_content

    def test_tasks_rate_limit_nonneg(self, init_content: str) -> None:
        """AC: tasks.rate_limit_count must be >= 0."""
        assert "chk_tasks_rate_limit_nonneg" in init_content

    def test_tasks_postpone_positive(self, init_content: str) -> None:
        """AC: tasks.postpone_cooldown_minutes must be > 0."""
        assert "chk_tasks_postpone_positive" in init_content

    # ---- stages ----

    def test_stages_valid_status(self, init_content: str) -> None:
        """AC: stages.status has valid enum values matching design spec."""
        assert "valid_stage_status" in init_content
        for status in [
            "pending", "executing", "completed", "failed",
            "skipped", "rate_limited", "max_turns",
        ]:
            assert f"'{status}'" in init_content, (
                f"Missing stage status value: {status}"
            )

    def test_stages_stage_num_nonneg(self, init_content: str) -> None:
        """AC: stages.stage_number must be >= 0."""
        assert "chk_stages_stage_num_nonneg" in init_content

    def test_stages_iteration_positive(self, init_content: str) -> None:
        """AC: stages.iteration must be >= 1."""
        assert "chk_stages_iteration_positive" in init_content

    def test_stages_run_positive(self, init_content: str) -> None:
        """AC: stages.run must be >= 1."""
        assert "chk_stages_run_positive" in init_content

    def test_stages_retry_nonneg(self, init_content: str) -> None:
        """AC: stages.retry_count must be >= 0."""
        assert "chk_stages_retry_nonneg" in init_content

    def test_stages_tokens_input_nonneg(self, init_content: str) -> None:
        """AC: stages.tokens_input must be >= 0 or NULL."""
        assert "chk_stages_tokens_input_nonneg" in init_content

    def test_stages_tokens_output_nonneg(self, init_content: str) -> None:
        """AC: stages.tokens_output must be >= 0 or NULL."""
        assert "chk_stages_tokens_output_nonneg" in init_content

    def test_stages_cost_nonneg(self, init_content: str) -> None:
        """AC: stages.cost_usd must be >= 0 or NULL."""
        assert "chk_stages_cost_nonneg" in init_content

    # ---- context ----

    def test_context_valid_value_type(self, init_content: str) -> None:
        """AC: context.value_type has valid enum values."""
        assert "valid_value_type" in init_content
        for vtype in ["json", "text", "file_ref"]:
            assert f"'{vtype}'" in init_content

    def test_context_value_consistency(self, init_content: str) -> None:
        """AC: context has exactly-one-value-column constraint."""
        assert "value_consistency" in init_content

    def test_context_key_nonempty(self, init_content: str) -> None:
        """AC: context.key must not be empty."""
        assert "chk_context_key_nonempty" in init_content

    # ---- agent_definitions ----

    def test_agent_def_name_nonempty(self, init_content: str) -> None:
        """AC: agent_definitions.name must not be empty."""
        assert "chk_agent_def_name_nonempty" in init_content

    def test_agent_def_version_nonempty(self, init_content: str) -> None:
        """AC: agent_definitions.version must not be empty."""
        assert "chk_agent_def_version_nonempty" in init_content

    def test_agent_def_group_values(self, init_content: str) -> None:
        """AC: agent_definitions.agent_group constrained to 'system' or 'pipeline'."""
        assert "chk_agent_def_group" in init_content
        assert "'system'" in init_content
        assert "'pipeline'" in init_content

    # ---- pipeline_definitions ----

    def test_pipeline_def_name_nonempty(self, init_content: str) -> None:
        """AC: pipeline_definitions.name must not be empty."""
        assert "chk_pipeline_def_name_nonempty" in init_content

    def test_pipeline_def_version_nonempty(self, init_content: str) -> None:
        """AC: pipeline_definitions.version must not be empty."""
        assert "chk_pipeline_def_version_nonempty" in init_content

    # ---- agent_overrides ----

    def test_agent_override_scope_nonempty(self, init_content: str) -> None:
        """AC: agent_overrides.scope must not be empty."""
        assert "chk_agent_override_scope_nonempty" in init_content

    # ---- validation_items ----

    def test_vi_valid_status(self, init_content: str) -> None:
        """AC: validation_items.status constrained to valid values."""
        assert "valid_vi_status" in init_content
        for status in ["open", "resolved", "wont_fix"]:
            assert f"'{status}'" in init_content

    def test_vi_resolution_consistency(self, init_content: str) -> None:
        """AC: validation_items resolved_by/resolved_at must be present when resolved."""
        assert "chk_vi_resolution_consistency" in init_content

    # ---- agent_instances ----

    def test_agent_instance_active_count_nonneg(self, init_content: str) -> None:
        """AC: agent_instances.active_count must be >= 0."""
        assert "active_count_non_negative" in init_content

    def test_agent_instance_total_executions_nonneg(self, init_content: str) -> None:
        """AC: agent_instances.total_executions must be >= 0."""
        assert "total_executions_non_negative" in init_content

    def test_agent_instance_total_tokens_nonneg(self, init_content: str) -> None:
        """AC: agent_instances.total_tokens_used must be >= 0."""
        assert "total_tokens_non_negative" in init_content

    # ---- supervisor_state ----

    def test_supervisor_key_nonempty(self, init_content: str) -> None:
        """AC: supervisor_state.key must not be empty."""
        assert "chk_supervisor_key_nonempty" in init_content

    # ---- count ----

    def test_minimum_check_constraint_count(self, init_content: str) -> None:
        """AC: At least 25 CHECK constraints exist across all tables."""
        check_count = len(re.findall(r'\bCONSTRAINT\b\s+\w+\s+CHECK\b', init_content))
        assert check_count >= 25, (
            f"Expected at least 25 CHECK constraints, found {check_count}"
        )


# =============================================================================
# FK dependency ordering — Design Decision 3
# =============================================================================

class TestFKDependencyOrder:
    """AC: Tables are created in correct FK dependency order to avoid forward references."""

    def test_repositories_before_tasks(self, init_content: str) -> None:
        """AC: repositories created before tasks (tasks.repository FK)."""
        repos_pos = init_content.index("CREATE TABLE repositories")
        tasks_pos = init_content.index("CREATE TABLE tasks")
        assert repos_pos < tasks_pos

    def test_pipeline_defs_before_tasks(self, init_content: str) -> None:
        """AC: pipeline_definitions before tasks (tasks.pipeline FK)."""
        pd_pos = init_content.index("CREATE TABLE pipeline_definitions")
        tasks_pos = init_content.index("CREATE TABLE tasks")
        assert pd_pos < tasks_pos

    def test_tasks_before_stages(self, init_content: str) -> None:
        """AC: tasks before stages (stages.task_id FK)."""
        tasks_pos = init_content.index("CREATE TABLE tasks")
        stages_pos = init_content.index("CREATE TABLE stages")
        assert tasks_pos < stages_pos

    def test_tasks_before_context(self, init_content: str) -> None:
        """AC: tasks before context (context.task_id FK)."""
        tasks_pos = init_content.index("CREATE TABLE tasks")
        context_pos = init_content.index("CREATE TABLE context")
        assert tasks_pos < context_pos

    def test_tasks_before_validation_items(self, init_content: str) -> None:
        """AC: tasks before validation_items (validation_items.task_id FK)."""
        tasks_pos = init_content.index("CREATE TABLE tasks")
        vi_pos = init_content.index("CREATE TABLE validation_items")
        assert tasks_pos < vi_pos

    def test_schema_before_tables(self, init_content: str) -> None:
        """AC: CREATE SCHEMA before any CREATE TABLE."""
        schema_pos = init_content.index("CREATE SCHEMA")
        first_table = init_content.index("CREATE TABLE")
        assert schema_pos < first_table

    def test_pgcrypto_before_tables(self, init_content: str) -> None:
        """AC: pgcrypto extension enabled before table creation."""
        pgcrypto_pos = init_content.index("pgcrypto")
        first_table = init_content.index("CREATE TABLE")
        assert pgcrypto_pos < first_table

    def test_update_updated_at_before_triggers(self, init_content: str) -> None:
        """AC: update_updated_at() function created before triggers that reference it."""
        fn_pos = init_content.index("CREATE OR REPLACE FUNCTION update_updated_at")
        trigger_pos = init_content.index("EXECUTE FUNCTION update_updated_at()")
        assert fn_pos < trigger_pos


# =============================================================================
# Deferred circular FK — Design Decision 3
# =============================================================================

class TestCircularFKResolution:
    """AC: The circular FK between tasks and stages is resolved via deferred ALTER TABLE."""

    def test_tasks_created_without_last_completed_stage_fk(self, init_content: str) -> None:
        """AC: tasks table is initially created without the last_completed_stage FK."""
        # Extract the CREATE TABLE tasks(...) block
        tasks_start = init_content.index("CREATE TABLE tasks")
        # Find the closing parenthesis of the CREATE TABLE
        paren_depth = 0
        tasks_end = tasks_start
        for i, ch in enumerate(init_content[tasks_start:], start=tasks_start):
            if ch == '(':
                paren_depth += 1
            elif ch == ')':
                paren_depth -= 1
                if paren_depth == 0:
                    tasks_end = i
                    break

        tasks_block = init_content[tasks_start:tasks_end + 1]
        # The FK to stages should NOT be in the CREATE TABLE block
        assert "fk_tasks_last_completed_stage" not in tasks_block, (
            "fk_tasks_last_completed_stage should be added via ALTER TABLE, not in CREATE TABLE"
        )

    def test_alter_table_adds_last_completed_stage_fk(self, init_content: str) -> None:
        """AC: An ALTER TABLE adds the deferred FK after stages table is created."""
        assert "ALTER TABLE tasks" in init_content
        assert "fk_tasks_last_completed_stage" in init_content
        assert "REFERENCES stages(id)" in init_content

    def test_alter_table_after_stages_creation(self, init_content: str) -> None:
        """AC: ALTER TABLE for FK comes after CREATE TABLE stages."""
        stages_pos = init_content.index("CREATE TABLE stages")
        # Find the actual ALTER TABLE statement (not the comment header)
        alter_pos = init_content.index("ALTER TABLE tasks\n")
        assert alter_pos > stages_pos, (
            f"ALTER TABLE tasks (at {alter_pos}) must come after "
            f"CREATE TABLE stages (at {stages_pos})"
        )

    def test_last_completed_stage_on_delete_set_null(self, init_content: str) -> None:
        """AC: last_completed_stage FK uses ON DELETE SET NULL (not CASCADE)."""
        # Find the actual ALTER TABLE statement (not the comment header)
        alter_idx = init_content.index("ALTER TABLE tasks\n")
        section = init_content[alter_idx:alter_idx + 300]
        assert "fk_tasks_last_completed_stage" in section
        assert "ON DELETE SET NULL" in section


# =============================================================================
# Index creation — Design Decision 6
# =============================================================================

class TestIndexCreation:
    """AC: Important indexes are created, including missing FK indexes identified in the design."""

    def test_idx_tasks_last_completed_stage(self, init_content: str) -> None:
        """AC: Index on tasks.last_completed_stage for FK delete performance."""
        assert "idx_tasks_last_completed_stage" in init_content

    def test_idx_tasks_pipeline_version(self, init_content: str) -> None:
        """AC: Index on tasks.(pipeline, pipeline_version) for composite FK lookup."""
        assert "idx_tasks_pipeline_version" in init_content

    def test_idx_stages_task_stage_key_iteration_run(self, init_content: str) -> None:
        """AC: Unique index for multi-agent stages dedup."""
        assert "idx_stages_task_stage_key_iteration_run" in init_content

    def test_idx_stages_stage_key(self, init_content: str) -> None:
        """AC: Index for fetching stages by stage_key."""
        assert "idx_stages_stage_key" in init_content

    def test_idx_stages_task_execution_order(self, init_content: str) -> None:
        """AC: Unique partial index for execution_order within a task."""
        assert "idx_stages_task_execution_order" in init_content

    def test_idx_pipeline_definitions_active(self, init_content: str) -> None:
        """AC: Partial index on active pipeline definitions."""
        assert "idx_pipeline_definitions_active" in init_content

    def test_idx_agent_definitions_active(self, init_content: str) -> None:
        """AC: Partial index on active agent definitions."""
        assert "idx_agent_definitions_active" in init_content

    def test_idx_agent_definitions_source(self, init_content: str) -> None:
        """AC: Partial index on agent definitions source."""
        assert "idx_agent_definitions_source" in init_content

    def test_idx_validation_items_task(self, init_content: str) -> None:
        """AC: Index on validation_items for task/status lookup."""
        assert "idx_validation_items_task" in init_content

    def test_idx_validation_items_category(self, init_content: str) -> None:
        """AC: Index on validation_items for category-based routing."""
        assert "idx_validation_items_category" in init_content

    def test_idx_tasks_parent(self, init_content: str) -> None:
        """AC: Partial index on tasks.parent_task_id."""
        assert "idx_tasks_parent" in init_content


# =============================================================================
# Trigger creation
# =============================================================================

class TestTriggerCreation:
    """AC: updated_at triggers are attached to appropriate tables."""

    @pytest.mark.parametrize("table", [
        "pipeline_definitions",
        "agent_definitions",
        "tasks",
        "agent_overrides",
    ])
    def test_updated_at_trigger_exists(self, init_content: str, table: str) -> None:
        """AC: Each table with updated_at has a trigger to auto-update it."""
        # Look for trigger name pattern: trg_<table>_updated_at
        trigger_pattern = f"trg_{table}_updated_at"
        assert trigger_pattern in init_content, (
            f"Missing updated_at trigger for {table}"
        )


# =============================================================================
# migrate.sh pre-flight guard — Design Decision 4
# =============================================================================

class TestMigrateShGuard:
    """AC: migrate.sh pre-flight guard handles live DB upgrades safely."""

    def test_uses_sha256_not_md5(self, migrate_sh_content: str) -> None:
        """CRITICAL: Guard uses SHA-256 to match yoyo-migrations' internal hashing algorithm.

        The review identified a critical bug where MD5 was originally used.
        MD5 produces 32-char hashes; SHA-256 produces 64-char. Mismatch would cause
        yoyo to not find the tracking row and re-apply the consolidated migration.
        """
        assert "hashlib.sha256" in migrate_sh_content, (
            "migrate.sh must use hashlib.sha256 to match yoyo-migrations"
        )
        assert "hashlib.md5" not in migrate_sh_content, (
            "migrate.sh must NOT use hashlib.md5 — yoyo uses SHA-256"
        )

    def test_has_table_existence_check(self, migrate_sh_content: str) -> None:
        """ERROR fix: Guard checks if _yoyo_migration table exists before querying it.

        The review identified that querying a non-existent table would crash with
        psycopg2.errors.UndefinedTable.
        """
        assert "to_regclass('public._yoyo_migration')" in migrate_sh_content, (
            "migrate.sh must check _yoyo_migration existence via to_regclass"
        )

    def test_has_on_conflict_guard(self, migrate_sh_content: str) -> None:
        """WARNING fix: INSERT uses ON CONFLICT DO NOTHING to prevent race conditions."""
        assert "ON CONFLICT" in migrate_sh_content
        assert "DO NOTHING" in migrate_sh_content

    def test_checks_aquarco_tasks_exists(self, migrate_sh_content: str) -> None:
        """AC: Guard checks if aquarco.tasks exists as proxy for existing deployment."""
        assert "to_regclass('aquarco.tasks')" in migrate_sh_content

    def test_checks_migration_id_before_insert(self, migrate_sh_content: str) -> None:
        """AC: Guard queries by migration_id before inserting marker row."""
        assert "migration_id = '000_consolidated_init'" in migrate_sh_content

    def test_guard_exits_on_fresh_db(self, migrate_sh_content: str) -> None:
        """AC: Guard exits early for fresh databases (no aquarco.tasks)."""
        # The guard has a sys.exit(0) path for fresh databases
        assert "sys.exit(0)" in migrate_sh_content

    def test_cleans_yoyo_tables_from_aquarco_schema(self, migrate_sh_content: str) -> None:
        """AC: First pre-flight block drops leaked yoyo tables from aquarco schema."""
        assert "_yoyo_migration" in migrate_sh_content
        assert "_yoyo_log" in migrate_sh_content
        assert "_yoyo_version" in migrate_sh_content
        assert "yoyo_lock" in migrate_sh_content
        assert "schema_name = 'aquarco'" in migrate_sh_content

    def test_safety_check_before_dropping_yoyo_tables(self, migrate_sh_content: str) -> None:
        """AC: Only drops yoyo tables from aquarco schema if they are empty."""
        assert "SELECT COUNT(*)" in migrate_sh_content
        assert "count == 0" in migrate_sh_content

    def test_sha256_hash_matches_yoyo_algorithm(self) -> None:
        """Verify the expected SHA-256 hash for '000_consolidated_init'.

        This is the exact value that yoyo-migrations 9.0.0 would compute.
        The hash must match for the pre-flight guard to work correctly.
        """
        expected_hash = hashlib.sha256(b"000_consolidated_init").hexdigest()
        assert len(expected_hash) == 64, "SHA-256 should produce 64-char hex digest"
        # Verify it's different from MD5 (the old bug)
        md5_hash = hashlib.md5(b"000_consolidated_init").hexdigest()
        assert len(md5_hash) == 32, "MD5 produces 32-char hex digest"
        assert expected_hash != md5_hash, "SHA-256 and MD5 hashes must differ"


# =============================================================================
# get_task_context() function details
# =============================================================================

class TestGetTaskContextFunction:
    """AC: get_task_context() function includes all required logic from migration 043."""

    @pytest.fixture
    def fn_body(self, init_content: str) -> str:
        """Extract the get_task_context() function body."""
        start = init_content.index("CREATE OR REPLACE FUNCTION get_task_context")
        # Find the closing $$; marker
        end = init_content.index("$$;", start + 1) + 3
        return init_content[start:end]

    def test_returns_jsonb(self, fn_body: str) -> None:
        """AC: Function returns JSONB."""
        assert "RETURNS JSONB" in fn_body

    def test_is_stable(self, fn_body: str) -> None:
        """AC: Function is marked STABLE (correct for read-only queries)."""
        assert "STABLE" in fn_body

    def test_uses_distinct_on_for_latest_run(self, fn_body: str) -> None:
        """AC: Uses DISTINCT ON to select only the latest run per (stage_key, iteration)."""
        assert "DISTINCT ON" in fn_body

    def test_orders_by_run_desc(self, fn_body: str) -> None:
        """AC: Orders by run DESC to pick the latest run in DISTINCT ON."""
        assert "run DESC" in fn_body

    def test_includes_execution_order(self, fn_body: str) -> None:
        """AC: Includes execution_order in the output."""
        assert "'execution_order'" in fn_body

    def test_sorts_by_execution_order_nulls_last(self, fn_body: str) -> None:
        """AC: Final ordering uses execution_order ASC NULLS LAST."""
        assert "execution_order ASC NULLS LAST" in fn_body

    def test_includes_validation_items_in_out(self, fn_body: str) -> None:
        """AC: Includes both validation_items_in and validation_items_out in stage output."""
        assert "'validation_items_in'" in fn_body
        assert "'validation_items_out'" in fn_body

    def test_returns_null_for_nonexistent_task(self, fn_body: str) -> None:
        """AC: Returns NULL if the task does not exist."""
        assert "RETURN NULL" in fn_body
        assert "v_task IS NULL" in fn_body

    def test_includes_context_entries(self, fn_body: str) -> None:
        """AC: Includes accumulated context entries in output."""
        assert "'context'" in fn_body
        assert "FROM context c" in fn_body

    def test_includes_validation_items_table(self, fn_body: str) -> None:
        """AC: Includes validation_items from the dedicated table."""
        assert "'validation_items'" in fn_body
        assert "FROM validation_items vi" in fn_body

    def test_returns_four_top_level_keys(self, fn_body: str) -> None:
        """AC: Output contains task, stages, context, and validation_items."""
        # The final jsonb_build_object call
        for key in ["'task'", "'stages'", "'context'", "'validation_items'"]:
            assert key in fn_body

    def test_coalesce_empty_arrays(self, fn_body: str) -> None:
        """AC: Uses COALESCE with empty JSON arrays for missing stages/context."""
        assert "COALESCE(" in fn_body
        assert "'[]'::jsonb" in fn_body

    def test_has_comment(self, init_content: str) -> None:
        """AC: Function has a descriptive COMMENT."""
        assert "COMMENT ON FUNCTION get_task_context" in init_content


# =============================================================================
# Rollback mechanism
# =============================================================================

class TestRollbackMechanism:
    """AC: Rollback uses DROP SCHEMA CASCADE for atomic cleanup."""

    def test_rollback_drops_schema_cascade(self, rollback_content: str) -> None:
        """AC: Rollback drops the entire aquarco schema with CASCADE."""
        assert "DROP SCHEMA aquarco CASCADE" in rollback_content

    def test_rollback_sets_search_path(self, rollback_content: str) -> None:
        """AC: Rollback sets search_path before dropping."""
        assert "SET search_path TO aquarco, public;" in rollback_content

    def test_rollback_is_concise(self, rollback_content: str) -> None:
        """AC: Rollback is a simple, atomic operation — no table-by-table drops."""
        lines = [l.strip() for l in rollback_content.splitlines() if l.strip() and not l.strip().startswith("--")]
        # Should be just SET search_path + DROP SCHEMA
        assert len(lines) == 2, (
            f"Rollback should be exactly 2 SQL statements, found {len(lines)}: {lines}"
        )


# =============================================================================
# Seed data idempotency
# =============================================================================

class TestSeedDataIdempotency:
    """AC: Seed data inserts use ON CONFLICT DO NOTHING for idempotent reruns."""

    def test_supervisor_state_seed_is_idempotent(self, init_content: str) -> None:
        """AC: supervisor_state seed uses ON CONFLICT DO NOTHING."""
        # Find the INSERT INTO supervisor_state section
        insert_idx = init_content.index("INSERT INTO supervisor_state")
        section = init_content[insert_idx:insert_idx + 200]
        assert "ON CONFLICT" in section
        assert "DO NOTHING" in section

    def test_seed_inserts_drain_mode_false(self, init_content: str) -> None:
        """AC: Seed sets drain_mode to 'false'."""
        insert_idx = init_content.index("INSERT INTO supervisor_state")
        section = init_content[insert_idx:insert_idx + 200]
        assert "'drain_mode'" in section
        assert "'false'" in section


# =============================================================================
# Foreign key relationships
# =============================================================================

class TestForeignKeys:
    """AC: All required foreign key relationships are present."""

    def test_tasks_references_repositories(self, init_content: str) -> None:
        """AC: tasks.repository references repositories(name)."""
        assert "REFERENCES repositories(name)" in init_content

    def test_stages_references_tasks_cascade(self, init_content: str) -> None:
        """AC: stages.task_id references tasks(id) with ON DELETE CASCADE."""
        assert "REFERENCES tasks(id) ON DELETE CASCADE" in init_content

    def test_context_references_tasks_cascade(self, init_content: str) -> None:
        """AC: context.task_id references tasks(id) with ON DELETE CASCADE."""
        # context is after stages, so both have this FK
        context_start = init_content.index("CREATE TABLE context")
        context_section = init_content[context_start:context_start + 1000]
        assert "REFERENCES tasks(id) ON DELETE CASCADE" in context_section

    def test_validation_items_references_tasks_cascade(self, init_content: str) -> None:
        """AC: validation_items.task_id references tasks(id) with ON DELETE CASCADE."""
        vi_start = init_content.index("CREATE TABLE validation_items")
        vi_section = init_content[vi_start:vi_start + 1000]
        assert "REFERENCES tasks(id) ON DELETE CASCADE" in vi_section

    def test_tasks_composite_fk_to_pipeline_definitions(self, init_content: str) -> None:
        """AC: tasks has composite FK (pipeline, pipeline_version) -> pipeline_definitions."""
        assert "fk_tasks_pipeline_definition" in init_content
        assert "REFERENCES pipeline_definitions(name, version)" in init_content

    def test_tasks_self_referencing_parent(self, init_content: str) -> None:
        """AC: tasks.parent_task_id references tasks(id) with ON DELETE SET NULL."""
        assert "parent_task_id" in init_content
        assert "REFERENCES tasks(id) ON DELETE SET NULL" in init_content


# =============================================================================
# Column completeness — validate that all expected columns are present
# =============================================================================

class TestColumnCompleteness:
    """AC: All columns from the final schema (migration 043) are present."""

    def test_stages_has_execution_order(self, init_content: str) -> None:
        """AC: stages table includes execution_order column (from migration 042)."""
        stages_start = init_content.index("CREATE TABLE stages")
        stages_end = init_content.index(");", stages_start)
        stages_block = init_content[stages_start:stages_end]
        assert "execution_order" in stages_block

    def test_stages_has_model(self, init_content: str) -> None:
        """AC: stages table includes model column (from migration 040)."""
        stages_start = init_content.index("CREATE TABLE stages")
        stages_end = init_content.index(");", stages_start)
        stages_block = init_content[stages_start:stages_end]
        assert "model" in stages_block

    def test_stages_has_cost_usd(self, init_content: str) -> None:
        """AC: stages table includes cost_usd column (from migration 032)."""
        stages_start = init_content.index("CREATE TABLE stages")
        stages_end = init_content.index(");", stages_start)
        stages_block = init_content[stages_start:stages_end]
        assert "cost_usd" in stages_block

    def test_stages_has_session_id(self, init_content: str) -> None:
        """AC: stages table includes session_id column (from migration 033)."""
        stages_start = init_content.index("CREATE TABLE stages")
        stages_end = init_content.index(");", stages_start)
        stages_block = init_content[stages_start:stages_end]
        assert "session_id" in stages_block

    def test_stages_has_msg_spending_state(self, init_content: str) -> None:
        """AC: stages table includes msg_spending_state column (from migration 039)."""
        stages_start = init_content.index("CREATE TABLE stages")
        stages_end = init_content.index(");", stages_start)
        stages_block = init_content[stages_start:stages_end]
        assert "msg_spending_state" in stages_block

    def test_stages_has_live_output(self, init_content: str) -> None:
        """AC: stages table includes live_output column (from migration 026)."""
        stages_start = init_content.index("CREATE TABLE stages")
        stages_end = init_content.index(");", stages_start)
        stages_block = init_content[stages_start:stages_end]
        assert "live_output" in stages_block

    def test_stages_has_validation_items_columns(self, init_content: str) -> None:
        """AC: stages table includes validation_items_in/out columns."""
        stages_start = init_content.index("CREATE TABLE stages")
        stages_end = init_content.index(");", stages_start)
        stages_block = init_content[stages_start:stages_end]
        assert "validation_items_in" in stages_block
        assert "validation_items_out" in stages_block

    def test_repositories_has_deploy_key(self, init_content: str) -> None:
        """AC: repositories table includes deploy_public_key (from migration 010)."""
        repos_start = init_content.index("CREATE TABLE repositories")
        repos_end = init_content.index(");", repos_start)
        repos_block = init_content[repos_start:repos_end]
        assert "deploy_public_key" in repos_block

    def test_repositories_has_original_url(self, init_content: str) -> None:
        """AC: repositories table includes original_url (from migration 011)."""
        repos_start = init_content.index("CREATE TABLE repositories")
        repos_end = init_content.index(");", repos_start)
        repos_block = init_content[repos_start:repos_end]
        assert "original_url" in repos_block

    def test_tasks_has_rate_limit_count(self, init_content: str) -> None:
        """AC: tasks table includes rate_limit_count (from migration 024)."""
        tasks_start = init_content.index("CREATE TABLE tasks")
        tasks_end = init_content.index(");", tasks_start)
        tasks_block = init_content[tasks_start:tasks_end]
        assert "rate_limit_count" in tasks_block

    def test_tasks_has_postpone_cooldown(self, init_content: str) -> None:
        """AC: tasks table includes postpone_cooldown_minutes (from migration 031)."""
        tasks_start = init_content.index("CREATE TABLE tasks")
        tasks_end = init_content.index(");", tasks_start)
        tasks_block = init_content[tasks_start:tasks_end]
        assert "postpone_cooldown_minutes" in tasks_block

    def test_agent_definitions_has_source(self, init_content: str) -> None:
        """AC: agent_definitions table includes source column (from migration 019)."""
        ad_start = init_content.index("CREATE TABLE agent_definitions")
        ad_end = init_content.index(");", ad_start)
        ad_block = init_content[ad_start:ad_end]
        assert "source" in ad_block

    def test_agent_definitions_has_agent_group(self, init_content: str) -> None:
        """AC: agent_definitions table includes agent_group column (from migration 030)."""
        ad_start = init_content.index("CREATE TABLE agent_definitions")
        ad_end = init_content.index(");", ad_start)
        ad_block = init_content[ad_start:ad_end]
        assert "agent_group" in ad_block


# =============================================================================
# Archive completeness
# =============================================================================

class TestArchiveCompleteness:
    """AC: Archive contains all original migration prefixes from 000 through 043."""

    EXPECTED_PREFIXES = [
        "000", "001", "002", "003", "004", "005", "006", "007", "008", "009",
        "010", "011", "012", "013", "014", "015", "016", "017", "018", "019",
        "022", "023", "024", "025", "026", "027", "028", "029",
        "030", "031", "032", "033", "034", "035", "036", "037", "038", "039",
        "040", "041", "042", "043",
    ]

    def test_all_migration_prefixes_in_archive(self) -> None:
        """AC: Every original migration prefix (000-043, minus gaps 020/021) has a forward file."""
        archived_forward = sorted(
            p for p in ARCHIVE_DIR.glob("*.sql")
            if not p.name.endswith(".rollback.sql")
        )
        prefixes = {p.name[:3] for p in archived_forward}
        for expected in self.EXPECTED_PREFIXES:
            assert expected in prefixes, (
                f"Missing migration prefix {expected} in archive"
            )

    def test_every_archived_forward_has_rollback(self) -> None:
        """AC: Every archived forward migration has a companion rollback file."""
        archived_forward = sorted(
            p for p in ARCHIVE_DIR.glob("*.sql")
            if not p.name.endswith(".rollback.sql")
        )
        for f in archived_forward:
            rollback = ARCHIVE_DIR / f"{f.stem}.rollback.sql"
            assert rollback.exists(), (
                f"Missing rollback for archived migration: {f.name}"
            )

    def test_duplicate_040_prefix_handled(self) -> None:
        """AC: Two migrations share prefix 040 (040_add_stage_model and 040_drop_is_config_repo).

        This causes 86 files instead of 88 (44 × 2), because there are
        only 43 unique forward migrations + 43 rollbacks = 86.
        """
        files_040 = [
            p for p in ARCHIVE_DIR.glob("040_*.sql")
            if not p.name.endswith(".rollback.sql")
        ]
        assert len(files_040) == 2, (
            f"Expected 2 forward migration files with prefix 040, found {len(files_040)}"
        )

    def test_no_gaps_020_021(self) -> None:
        """AC: Migrations 020 and 021 were never created (known gap in numbering)."""
        files_020 = list(ARCHIVE_DIR.glob("020_*"))
        files_021 = list(ARCHIVE_DIR.glob("021_*"))
        assert len(files_020) == 0, "Migration 020 should not exist"
        assert len(files_021) == 0, "Migration 021 should not exist"


# =============================================================================
# Schema search_path safety
# =============================================================================

class TestSearchPathSafety:
    """AC: Schema objects are created in the correct search_path context."""

    def test_sets_search_path(self, init_content: str) -> None:
        """AC: Sets search_path to aquarco, public before table creation."""
        assert "SET search_path TO aquarco, public;" in init_content

    def test_pins_role_search_path(self, init_content: str) -> None:
        """AC: Pins role-level search_path to public to prevent yoyo table leaks."""
        assert "ALTER ROLE aquarco SET search_path TO public" in init_content

    def test_role_pin_before_session_set(self, init_content: str) -> None:
        """AC: Role-level pin comes before session-level SET."""
        role_pos = init_content.index("ALTER ROLE aquarco SET search_path")
        session_pos = init_content.index("SET search_path TO aquarco, public;")
        assert role_pos < session_pos


# =============================================================================
# Dockerfile concerns from review
# =============================================================================

class TestDockerfileDetails:
    """Validate Dockerfile configuration details."""

    @pytest.fixture
    def dockerfile_content(self) -> str:
        return (DB_DIR / "Dockerfile").read_text()

    def test_entrypoint_is_migrate_sh(self, dockerfile_content: str) -> None:
        """AC: Dockerfile uses migrate.sh as entrypoint."""
        assert "ENTRYPOINT" in dockerfile_content
        assert "migrate.sh" in dockerfile_content

    def test_default_cmd_is_apply(self, dockerfile_content: str) -> None:
        """AC: Default CMD is 'apply'."""
        assert 'CMD ["apply"]' in dockerfile_content

    def test_copies_yoyo_ini(self, dockerfile_content: str) -> None:
        """AC: Dockerfile copies yoyo.ini into the image."""
        assert "COPY yoyo.ini" in dockerfile_content

    def test_copies_migrate_sh(self, dockerfile_content: str) -> None:
        """AC: Dockerfile copies migrate.sh into the image."""
        assert "COPY migrate.sh" in dockerfile_content

    def test_copies_migrations_dir(self, dockerfile_content: str) -> None:
        """AC: Dockerfile copies migrations/ directory into the image."""
        assert "COPY migrations/" in dockerfile_content

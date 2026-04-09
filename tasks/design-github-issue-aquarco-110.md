# Design: Consolidate Migrations (Issue #110)

**Task ID:** github-issue-aquarco-110  
**Date:** 2026-04-08  
**Pipeline Stage:** design (stage 1)

---

## Problem Summary

The database has accumulated 44 incremental migration files (000–043) created during the pre-release phase. These represent a historical record of schema evolution but are unwieldy for fresh deployments, hard to audit for correctness, and do not encode the current schema clearly. The goal is to:

1. Collapse all 44 migrations into a single canonical `000_consolidated_init.sql`
2. Add meaningful database-level constraints that were deferred during iterative development
3. Verify all living tables are actually referenced in application code (no unused tables)
4. Ensure existing deployed databases can be safely upgraded to the consolidated baseline

**Analysis finding:** All 11 living tables (`repositories`, `tasks`, `stages`, `context`, `poll_state`, `agent_instances`, `agent_definitions`, `pipeline_definitions`, `agent_overrides`, `validation_items`, `supervisor_state`) are referenced in application code or the `get_task_context()` SQL function. No tables need to be removed.

---

## Design Decisions

### Decision 1: Archive, Don't Delete Old Migrations

Old migration files (`000_init` through `043_fix_get_task_context`) are moved to `db/migrations/archive/`. They are:
- Retained as historical reference
- Excluded from yoyo's migration discovery (yoyo scans only `migrations/`, not subdirectories)
- Not deleted, allowing post-hoc audit or rollback archaeology

**Alternative rejected:** Permanently deleting old files — loses institutional history with no benefit.

### Decision 2: Single New Migration `000_consolidated_init`

The consolidated file uses `-- depends:` (empty) so yoyo treats it as a root migration. All object creation uses **no** `IF NOT EXISTS` clauses — this is a clean-install script, not an idempotent one.

### Decision 3: Circular FK Resolution via Deferred ALTER TABLE

`tasks.last_completed_stage → stages(id)` and `stages.task_id → tasks(id)` form a circular reference. The consolidated init creates `tasks` first without the `last_completed_stage` FK, then creates `stages`, then adds the FK via `ALTER TABLE tasks ADD CONSTRAINT` at the end of the script.

### Decision 4: Live DB Upgrade via migrate.sh Guard

For databases already running migrations 000–043, `migrate.sh` gains a pre-flight guard: if the `aquarco.tasks` table exists (indicating an existing installation) but yoyo does not have a record of `000_consolidated_init`, the guard inserts a `_yoyo_migration` row marking it as already applied. This prevents the consolidated init from running against a live database.

### Decision 5: New Meaningful Constraints

The following CHECK constraints are added to the consolidated init. They were omitted from individual migrations due to the iterative nature of pre-release development. All are safe for fresh databases (no existing data to violate them).

| Table | Constraint Name | Expression | Rationale |
|-------|----------------|------------|-----------|
| `repositories` | `chk_repos_url_nonempty` | `url <> ''` | Empty URL breaks all git operations |
| `repositories` | `chk_repos_clone_dir_nonempty` | `clone_dir <> ''` | Empty path causes filesystem errors |
| `repositories` | `chk_repos_name_slug` | `name ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]*$'` | Names used as path components |
| `tasks` | `chk_tasks_id_nonempty` | `id <> ''` | Empty IDs break all lookups |
| `tasks` | `chk_tasks_title_nonempty` | `title <> ''` | Title is required for human readability |
| `tasks` | `chk_tasks_retry_count_nonneg` | `retry_count >= 0` | Logical invariant |
| `tasks` | `chk_tasks_rate_limit_nonneg` | `rate_limit_count >= 0` | Logical invariant |
| `tasks` | `chk_tasks_cooldown_positive` | `postpone_cooldown_minutes > 0` | Zero cooldown is meaningless |
| `stages` | `chk_stages_stage_num_nonneg` | `stage_number >= 0` | 0-based index cannot be negative |
| `stages` | `chk_stages_run_positive` | `run >= 1` | Run numbers are 1-based |
| `stages` | `chk_stages_iteration_positive` | `iteration >= 1` | Iteration numbers are 1-based |
| `stages` | `chk_stages_retry_nonneg` | `retry_count >= 0` | Logical invariant |
| `stages` | `chk_stages_cost_nonneg` | `cost_usd >= 0 OR cost_usd IS NULL` | Cost cannot be negative |
| `context` | `chk_context_key_nonempty` | `key <> ''` | Empty key breaks all lookups |
| `agent_definitions` | `chk_agentdef_name_nonempty` | `name <> ''` | Required identifier |
| `agent_definitions` | `chk_agentdef_version_nonempty` | `version <> ''` | Required for composite PK |
| `pipeline_definitions` | `chk_pipelinedef_name_nonempty` | `name <> ''` | Required identifier |
| `pipeline_definitions` | `chk_pipelinedef_version_nonempty` | `version <> ''` | Required for composite PK |
| `validation_items` | `chk_vi_description_nonempty` | `description <> ''` | Empty description is useless |
| `validation_items` | `chk_vi_category_nonempty` | `category <> ''` | Category drives pipeline routing |
| `validation_items` | `chk_vi_resolution_consistency` | `(status = 'open' AND resolved_by IS NULL AND resolved_at IS NULL) OR (status IN ('resolved', 'wont_fix'))` | Resolution fields must be consistent with status |
| `supervisor_state` | `chk_supervisor_key_nonempty` | `key <> ''` | Empty key breaks lookups |
| `poll_state` | `chk_poll_poller_nonempty` | `poller_name <> ''` | Required identifier |
| `agent_instances` | `chk_agentinst_name_nonempty` | `agent_name <> ''` | Required identifier |

### Decision 6: Add Missing FK Indexes

Two foreign key columns lack explicit indexes, causing sequential scans on large datasets:

- `tasks.last_completed_stage` → add `idx_tasks_last_completed_stage`
- `tasks.(pipeline, pipeline_version)` composite FK → add `idx_tasks_pipeline_version`

---

## Final Schema (as of migration 043)

### Table Creation Order

```
1. CREATE SCHEMA aquarco
2. CREATE EXTENSION pgcrypto
3. CREATE FUNCTION update_updated_at()
4. CREATE TABLE repositories
5. CREATE TABLE pipeline_definitions  (PK: name, version)
6. CREATE TABLE agent_definitions     (PK: name, version)
7. CREATE TABLE tasks                 (FK: repository, pipeline+pipeline_version — no last_completed_stage FK yet)
8. CREATE TABLE stages                (FK: task_id → tasks)
9. ALTER TABLE tasks ADD CONSTRAINT fk_tasks_last_completed_stage (circular FK resolution)
10. CREATE TABLE context              (FK: task_id → tasks)
11. CREATE TABLE poll_state
12. CREATE TABLE agent_instances
13. CREATE TABLE agent_overrides
14. CREATE TABLE validation_items     (FK: task_id → tasks)
15. CREATE TABLE supervisor_state
16. CREATE all indexes
17. CREATE all triggers
18. CREATE OR REPLACE FUNCTION get_task_context()
19. INSERT seed data (supervisor_state drain_mode)
```

### tasks Status Enum (CHECK constraint values)
`'pending', 'queued', 'planning', 'executing', 'completed', 'failed', 'timeout', 'blocked', 'rate_limited', 'closed'`

### stages Status Enum (CHECK constraint values)
`'pending', 'executing', 'completed', 'failed', 'skipped', 'rate_limited', 'max_turns'`

### context value_type Enum
`'json', 'text', 'file_ref'`

---

## File Layout After Implementation

```
db/
├── migrations/
│   ├── archive/
│   │   ├── 000_init.sql
│   │   ├── 000_init.rollback.sql
│   │   ├── ... (all 44 × 2 files)
│   │   └── 043_fix_get_task_context.rollback.sql
│   ├── 000_consolidated_init.sql          ← NEW (full schema)
│   └── 000_consolidated_init.rollback.sql ← NEW (DROP SCHEMA CASCADE)
├── tests/
│   └── test_yoyo_migrations.py            ← UPDATED
├── yoyo.ini                               ← UNCHANGED
├── migrate.sh                             ← UPDATED (live DB guard)
└── Dockerfile                             ← UNCHANGED
```

---

## `000_consolidated_init.sql` Structure

```sql
-- depends:
-- Migration: 000_consolidated_init.sql
-- Purpose: Canonical database initialization for Aquarco agent system.
--   Consolidates migrations 000-043 into a single script representing
--   the full schema as of 2026-04-08.

SET search_path TO aquarco, public;

-- 1. Schema + extensions
CREATE SCHEMA aquarco;
ALTER ROLE aquarco SET search_path TO public;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
SET search_path TO aquarco, public;

-- 2. Trigger helper
CREATE OR REPLACE FUNCTION update_updated_at() ...

-- 3. Tables (in dependency order per Decision 3)
CREATE TABLE repositories (...)
CREATE TABLE pipeline_definitions (...)
CREATE TABLE agent_definitions (...)
CREATE TABLE tasks (...)   -- no last_completed_stage FK yet
CREATE TABLE stages (...)
ALTER TABLE tasks ADD CONSTRAINT fk_tasks_last_completed_stage ...
CREATE TABLE context (...)
CREATE TABLE poll_state (...)
CREATE TABLE agent_instances (...)
CREATE TABLE agent_overrides (...)
CREATE TABLE validation_items (...)
CREATE TABLE supervisor_state (...)

-- 4. All indexes (from analysis above)
-- 5. All triggers
-- 6. get_task_context() function (exact body from migration 043)
-- 7. Seed data
INSERT INTO supervisor_state (key, value) VALUES ('drain_mode', 'false');
```

---

## `000_consolidated_init.rollback.sql` Structure

```sql
SET search_path TO aquarco, public;
DROP SCHEMA aquarco CASCADE;
```

The rollback drops the entire schema, which removes all tables, functions, triggers, and sequences atomically.

---

## `migrate.sh` Live DB Guard

Add a Python block before the yoyo invocation:

```python
# If aquarco schema has tables (existing deployment) but yoyo does not
# record 000_consolidated_init as applied, mark it applied to skip re-run.
cur.execute("SELECT to_regclass('aquarco.tasks')")
has_tasks = cur.fetchone()[0] is not None

cur.execute("SELECT 1 FROM public._yoyo_migration WHERE migration_id = '000_consolidated_init'")
already_recorded = cur.fetchone() is not None

if has_tasks and not already_recorded:
    cur.execute(
        "INSERT INTO public._yoyo_migration (migration_id, applied_at_utc, created_at_utc) "
        "VALUES ('000_consolidated_init', NOW(), NOW())"
    )
    print("Marked 000_consolidated_init as applied (existing deployment detected)")
```

---

## Test Updates (`test_yoyo_migrations.py`)

1. `EXPECTED_MIGRATIONS` list shrinks to `["000_consolidated_init"]`
2. Remove `test_022_depends_on_019` (no longer relevant)
3. Keep all structural tests (depends header, rollback exists, rollback has search_path)
4. Update `test_expected_migration_count` to expect 1
5. Update `test_no_unexpected_migrations` to exclude `archive/` subdirectory from the scan
   - Change `_migration_files()` to use `MIGRATIONS_DIR.glob("*.sql")` (not recursive) — already correct since `archive/` is a subdirectory and non-recursive glob won't descend into it
6. Remove `test_each_migration_depends_on_predecessor` chain assertions (only one migration)

---

## Assumptions

1. **PostgreSQL version ≥ 13** is assumed based on existing use of `gen_random_uuid()`, `jsonb_agg()`, partial unique indexes, and `BIGSERIAL`. All new constraints use standard SQL syntax compatible with PG13+.
2. **yoyo `_yoyo_migration` table schema**: The live DB guard assumes columns `(migration_id, applied_at_utc, created_at_utc)`. If the actual yoyo version uses different column names, the guard must be adjusted. Implementation agent should verify against the installed yoyo version.
3. **No data migration needed**: Since all new constraints are added fresh (clean install), there is no backfill step. The constraints will not apply to existing production rows (existing DBs keep their old schema via the guard in migrate.sh).
4. **`archive/` directory isolation**: yoyo's `sources = migrations` config scans the `migrations/` directory but NOT subdirectories by default. This isolates archived files cleanly.

---

## Risk Mitigations

| Risk | Mitigation |
|------|-----------|
| Live DB re-applies 000_consolidated_init | migrate.sh guard marks it as applied if `aquarco.tasks` already exists |
| Circular FK between tasks and stages | Created by ALTER TABLE after stages table exists (proven approach from migration 034) |
| get_task_context() must be migration-043 version | Copied verbatim from `043_fix_get_task_context.sql` — no mutation |
| Test suite expects 44 migrations | EXPECTED_MIGRATIONS updated to single entry; _migration_files() glob naturally excludes archive/ |
| archive/ subdirectory breaks yoyo | yoyo `sources = migrations` uses non-recursive directory scan — archive/ excluded automatically |

# Design: Convert to Yoyo Migrations

**Task:** github-issue-aquarco-22
**Issue:** https://github.com/aquarco/aquarco/issues/22
**Date:** 2026-03-25

## Summary

Replace the current `docker-entrypoint-initdb.d` migration mechanism with
[yoyo-migrations](https://ollycope.com/software/yoyo/latest/), a Python-based
database migration tool. Migrations will run in a dedicated container on every
`docker compose up`, with support for listing, rolling back, and reapplying
migrations.

## Current State

- **27 SQL files** in `db/migrations/` (000-027, with 020/021 missing)
- Each file uses a custom `-- up` / `-- down` comment convention
- The `-- down` sections are commented out and untested
- Migrations are mounted into postgres via `docker-entrypoint-initdb.d`, which
  only runs on first database initialization (empty `pgdata` volume)
- No migration state tracking table exists
- No rollback capability

## Target State

- Each migration file has a yoyo-compatible `-- depends:` header
- Each migration has a companion `*.rollback.sql` file with the reverse SQL
- A `db/Dockerfile` builds a lightweight Python image with `yoyo-migrations`
- A `migrations` service in `docker/compose.yml` runs `yoyo apply` on startup
- The postgres service no longer mounts `db/migrations` to `initdb.d`
- A helper script `db/migrate.sh` wraps common yoyo commands (list, rollback, reapply)

## Design Decisions

### 1. Yoyo SQL format (not Python)

Yoyo supports both Python and raw SQL migration files. We use **raw SQL** to
keep migrations accessible to anyone who reads SQL and to preserve the existing
`.sql` files with minimal changes.

**Yoyo SQL format requirements:**
- First line(s): `-- depends: <previous_migration_name>` (without `.sql` extension)
- For the first migration: `-- depends:`  (empty, meaning no dependency)
- The file body contains the forward ("up") SQL
- Rollback SQL lives in a sibling file: `<name>.rollback.sql`

### 2. Migration container (not sidecar, not init-container)

A dedicated `migrations` Docker service that:
- Uses a small Python Alpine image built from `db/Dockerfile`
- Depends on `postgres` being healthy
- Runs `yoyo apply --batch --no-prompt` and exits
- Is configured with `restart: "no"` so it runs once per `docker compose up`
- Other services (`api`, `web`) depend on `migrations` completing

### 3. Existing database compatibility

For databases already initialized via `initdb.d`:
- yoyo creates a `_yoyo_migration` tracking table on first run
- We use `yoyo apply --batch` which marks already-applied migrations
- Since all existing migrations use `IF NOT EXISTS` and `ADD COLUMN IF NOT EXISTS`,
  re-running them is safe — yoyo will track them going forward
- **Assumption:** On first run against an existing database, yoyo will attempt to
  apply all migrations. Because they are idempotent, this is safe. The tracking
  table then prevents future re-execution.

### 4. Database URL construction

The migration container receives `DATABASE_URL` from environment, same pattern
as the `api` service:
```
postgresql://${POSTGRES_USER:-aquarco}:${POSTGRES_PASSWORD:-aquarco}@postgres:5432/${POSTGRES_DB:-aquarco}
```

### 5. Schema search path

Each migration already includes `SET search_path TO aquarco, public;`. This
remains unchanged. The yoyo `-- depends:` header is simply prepended before
the existing SQL.

### 6. Missing migrations 020 and 021

These numbers are intentionally skipped (confirmed by gap in existing sequence).
No placeholder files will be created.

## File Changes

### New Files

| File | Purpose |
|------|---------|
| `db/Dockerfile` | Build migration container image |
| `db/yoyo.ini` | Yoyo configuration (migrations dir, database URL) |
| `db/migrate.sh` | Helper script for list/rollback/reapply |
| `db/migrations/000_init.rollback.sql` | Rollback for 000 |
| `db/migrations/001_create_repositories.rollback.sql` | Rollback for 001 |
| ... (one `.rollback.sql` per migration) | ... |
| `db/migrations/027_task_lifecycle.rollback.sql` | Rollback for 027 |

### Modified Files

| File | Change |
|------|--------|
| `db/migrations/000_init.sql` | Add `-- depends:` header, remove `-- up`/`-- down` comments and down section |
| `db/migrations/001_create_repositories.sql` | Add `-- depends: 000_init`, remove down section |
| ... (all 27 migration files) | Same pattern |
| `docker/compose.yml` | Remove initdb.d mount from postgres, add `migrations` service |
| `supervisor/templates/docker-compose.repo.yml.tmpl` | Remove initdb.d mount, add migrations service |

## Detailed Specifications

### db/Dockerfile

```dockerfile
FROM python:3.12-alpine
RUN pip install --no-cache-dir yoyo-migrations[postgres]
WORKDIR /db
COPY yoyo.ini .
COPY migrations/ migrations/
COPY migrate.sh .
RUN chmod +x migrate.sh
ENTRYPOINT ["yoyo"]
CMD ["apply", "--batch", "--no-prompt", "-c", "/db/yoyo.ini"]
```

**Note:** In production the Dockerfile is used, but for development the source
is mounted directly (matching the project's hot-reload pattern). The compose
file mounts `../db` into the container so edits on the host are picked up.

### db/yoyo.ini

```ini
[DEFAULT]
sources = migrations
database = %(DATABASE_URL)s
batch_mode = on
verbosity = 2
```

The `DATABASE_URL` environment variable is interpolated by yoyo at runtime
(yoyo supports `%(ENV_VAR)s` syntax for environment variable expansion).

### Migration file conversion pattern

**Before (e.g., `001_create_repositories.sql`):**
```sql
-- Migration: 001_create_repositories.sql
-- Purpose: ...

-- up

SET search_path TO aquarco, public;
CREATE TABLE IF NOT EXISTS repositories (...);

-- down
-- DROP TABLE IF EXISTS aquarco.repositories;
```

**After (`001_create_repositories.sql`):**
```sql
-- depends: 000_init

-- Migration: 001_create_repositories.sql
-- Purpose: ...

SET search_path TO aquarco, public;
CREATE TABLE IF NOT EXISTS repositories (...);
```

**New file (`001_create_repositories.rollback.sql`):**
```sql
SET search_path TO aquarco, public;
DROP TABLE IF EXISTS repositories;
```

### Dependency chain

```
000_init (no dependency)
  <- 001_create_repositories
  <- 002_create_tasks
  <- 003_create_stages
  <- 004_create_context
  <- 005_create_poll_state
  <- 006_create_agent_instances
  <- 007_create_pipeline_checkpoints
  <- 008_create_functions (depends on 002, 003)
  <- 009_add_repo_error_message (depends on 001)
  <- 010_add_repo_deploy_key (depends on 001)
  <- 011_add_repo_original_url (depends on 001)
  <- 012_fix_null_agent_instance (depends on 006)
  <- 013_create_agent_and_pipeline_definitions (depends on 000)
  <- 014_pipeline_redesign (depends on 002, 003, 008)
  <- 015_remove_task_category (depends on 014)
  <- 016_add_is_config_repo (depends on 001)
  <- 017_allow_null_branch (depends on 001)
  <- 018_rename_schema (depends on 000)
  <- 019_agent_overrides_and_source (depends on 013)
  <- 022_fix_agent_overrides_schema (depends on 019)
  <- 023_drop_input_from_context (depends on 004, 014)
  <- 024_add_rate_limited_status (depends on 002)
  <- 025_add_stage_run (depends on 003)
  <- 026_add_live_output (depends on 003)
  <- 027_task_lifecycle (depends on 002, 017)
```

**Simplification:** Since migrations were originally designed to run sequentially
(alphabetical order in initdb.d), we use a **linear dependency chain** — each
migration depends on the one immediately before it. This preserves the existing
execution order and avoids complex dependency graphs:

```
000_init <- 001_create_repositories <- 002_create_tasks <- ... <- 027_task_lifecycle
```

The gap at 020/021 means 022 depends on 019.

### docker/compose.yml changes

```yaml
# Remove from postgres.volumes:
#   - ../db/migrations:/docker-entrypoint-initdb.d

# Add new service:
migrations:
  build:
    context: ../db
    dockerfile: Dockerfile
  restart: "no"
  networks:
    - aquarco
  volumes:
    - ../db/migrations:/db/migrations
    - ../db/yoyo.ini:/db/yoyo.ini
  environment:
    DATABASE_URL: postgresql://${POSTGRES_USER:-aquarco}:${POSTGRES_PASSWORD:-aquarco}@postgres:5432/${POSTGRES_DB:-aquarco}
  depends_on:
    postgres:
      condition: service_healthy

# Update api.depends_on:
api:
  depends_on:
    migrations:
      condition: service_completed_successfully
```

### supervisor/templates/docker-compose.repo.yml.tmpl changes

Same pattern: remove `initdb.d` mount from postgres, add `migrations` service,
update `api` depends_on. The template uses different env var defaults
(`dev`/`dev`/`dev`).

### db/migrate.sh

```bash
#!/bin/sh
# Helper script for migration operations
# Usage: ./migrate.sh [apply|rollback|reapply|list]

set -e

CMD=${1:-apply}

case "$CMD" in
  apply)
    yoyo apply --batch --no-prompt -c /db/yoyo.ini
    ;;
  rollback)
    MIGRATION=${2:-}
    if [ -n "$MIGRATION" ]; then
      yoyo rollback --batch --no-prompt -c /db/yoyo.ini --revision "$MIGRATION"
    else
      yoyo rollback --batch --no-prompt -c /db/yoyo.ini
    fi
    ;;
  reapply)
    yoyo reapply --batch --no-prompt -c /db/yoyo.ini
    ;;
  list)
    yoyo list -c /db/yoyo.ini
    ;;
  *)
    echo "Usage: $0 {apply|rollback|reapply|list}"
    exit 1
    ;;
esac
```

## Rollback SQL Specifications

Each rollback file must precisely reverse its forward migration. Key rollback patterns:

| Migration | Rollback Strategy |
|-----------|-------------------|
| 000_init | `DROP EXTENSION IF EXISTS pgcrypto; DROP SCHEMA IF EXISTS aquarco CASCADE;` |
| 001-007 | `DROP TABLE IF EXISTS <table_name>;` |
| 008_create_functions | `DROP FUNCTION IF EXISTS ...;` |
| 009-011 | `ALTER TABLE ... DROP COLUMN IF EXISTS ...;` |
| 012 | Reverse the constraint changes |
| 013 | `DROP TABLE IF EXISTS agent_definitions; DROP TABLE IF EXISTS pipeline_definitions;` |
| 014 | Drop new columns/tables/indexes, restore old constraints |
| 015 | Re-add the `category` column and constraint |
| 016-017 | Drop columns / restore constraints |
| 018 | Rename schema back (`aquarco` -> `aifishtank`); conditional like forward |
| 019 | Drop agent_overrides table, drop source column |
| 022 | Reverse schema fixes |
| 023 | Re-add input column to context |
| 024 | Restore previous status CHECK without 'rate_limited' |
| 025-026 | Drop columns/tables |
| 027 | Drop columns, restore previous status CHECK without 'closed' |

**Important:** Rollback for 000_init (DROP SCHEMA CASCADE) is destructive and
destroys all data. The rollback file should include a prominent warning comment.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| First run on existing DB re-applies all migrations | All migrations use IF NOT EXISTS; idempotent by design |
| Rollback files untested | Acceptance criteria include rollback tests |
| Migration container blocks startup | `restart: "no"` + `service_completed_successfully` ensures clean failure |
| yoyo tracking table in wrong schema | yoyo creates `_yoyo_migration` in the default (public) schema; this is fine |
| vboxsf polling for migration files | Migrations only run at container start, not watched; no polling needed |

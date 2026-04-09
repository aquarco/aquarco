#!/bin/sh
set -e

# Helper script for yoyo migration operations.
# Usage: migrate.sh [apply|rollback|reapply|list] [extra yoyo args...]

# ── Pre-flight: remove yoyo tracking tables from the aquarco schema ──
# Yoyo must find its _yoyo_migration table in the public schema.
# If a previous run leaked SET search_path into the session, yoyo may
# have created empty tracking tables inside the aquarco schema. These
# shadow the real ones in public and cause yoyo to re-apply everything.
# This block drops them ONLY if they are empty (safety check).
python3 - "$DATABASE_URL" <<'PYEOF'
import sys, os, psycopg2

url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATABASE_URL", "")

YOYO_TABLES = ["_yoyo_migration", "_yoyo_log", "_yoyo_version", "yoyo_lock"]

conn = psycopg2.connect(url)
conn.autocommit = True
cur = conn.cursor()

# Check if aquarco schema exists
cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'aquarco'")
if not cur.fetchone():
    sys.exit(0)

for table in YOYO_TABLES:
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'aquarco' AND table_name = %s",
        (table,)
    )
    if not cur.fetchone():
        continue
    # Safety: only drop if empty
    cur.execute(f'SELECT COUNT(*) FROM aquarco."{table}"')
    count = cur.fetchone()[0]
    if count == 0:
        cur.execute(f'DROP TABLE aquarco."{table}" CASCADE')
        print(f"Dropped empty aquarco.{table}")
    else:
        print(f"WARNING: aquarco.{table} has {count} rows — skipping drop", file=sys.stderr)

cur.close()
conn.close()
PYEOF

# ── Pre-flight: mark consolidated migration on existing deployments ──
# Databases built via the old incremental migrations (000–043) already have
# the full schema. If we detect aquarco.tasks exists but yoyo has no record
# of 000_consolidated_init, we INSERT a tracking row so yoyo skips it.
python3 - "$DATABASE_URL" <<'PYEOF'
import sys, os, psycopg2, hashlib

url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATABASE_URL", "")

conn = psycopg2.connect(url)
conn.autocommit = True
cur = conn.cursor()

# Check if aquarco.tasks exists (proxy for "schema was built by old migrations")
cur.execute("SELECT to_regclass('aquarco.tasks')")
if cur.fetchone()[0] is None:
    # Fresh database — let the consolidated init run normally
    cur.close()
    conn.close()
    sys.exit(0)

# Schema exists. Check if consolidated migration is already tracked.
# Guard: _yoyo_migration may not exist yet (e.g. database bootstrapped from a dump).
cur.execute("SELECT to_regclass('public._yoyo_migration')")
if cur.fetchone()[0] is None:
    # Yoyo tracking table doesn't exist — yoyo will create it on first run.
    # Nothing to mark; exit cleanly.
    cur.close()
    conn.close()
    sys.exit(0)

cur.execute(
    "SELECT 1 FROM public._yoyo_migration "
    "WHERE migration_id = '000_consolidated_init'"
)
if cur.fetchone():
    # Already marked — nothing to do
    cur.close()
    conn.close()
    sys.exit(0)

# Mark the consolidated migration as applied so yoyo skips it.
# Use SHA-256 to match yoyo-migrations' internal hashing algorithm.
migration_hash = hashlib.sha256(b"000_consolidated_init").hexdigest()
cur.execute(
    "INSERT INTO public._yoyo_migration (migration_hash, migration_id, applied_at_utc) "
    "VALUES (%s, %s, NOW()) "
    "ON CONFLICT (migration_hash) DO NOTHING",
    (migration_hash, "000_consolidated_init")
)
print("Marked 000_consolidated_init as applied (existing deployment detected)")

cur.close()
conn.close()
PYEOF

COMMAND="${1:-apply}"
shift 2>/dev/null || true

case "$COMMAND" in
  apply)
    echo "==> Applying pending migrations..."
    yoyo apply --batch  -c /db/yoyo.ini "$@"
    ;;
  rollback)
    echo "==> Rolling back last migration..."
    yoyo rollback --batch  -c /db/yoyo.ini "$@"
    ;;
  reapply)
    echo "==> Re-applying last migration (rollback + apply)..."
    yoyo reapply --batch  -c /db/yoyo.ini "$@"
    ;;
  list)
    echo "==> Listing migrations..."
    yoyo list -c /db/yoyo.ini "$@"
    ;;
  *)
    echo "Usage: migrate.sh [apply|rollback|reapply|list] [extra args...]"
    exit 1
    ;;
esac

echo "==> Done."

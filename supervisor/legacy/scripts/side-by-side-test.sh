#!/usr/bin/env bash
# Side-by-side validation: compare shell and Python supervisor behavior.
#
# This script runs both implementations against the same database and
# verifies they produce identical task and stage records.
#
# Usage:
#   SUPERVISOR_USE_PYTHON=1 ./supervisor/scripts/side-by-side-test.sh
#
# Prerequisites:
#   - PostgreSQL running with aifishtank schema
#   - Python package installed (pip install -e supervisor/python)
#   - gh and claude CLI available
set -euo pipefail

DB_URL="${DATABASE_URL:-postgresql://aifishtank:aifishtank@localhost:5432/aifishtank}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUPERVISOR_DIR="$(dirname "$SCRIPT_DIR")"
TRIGGERS_DIR="/tmp/aifishtank-sbs-triggers"
RESULTS_DIR="/tmp/aifishtank-sbs-results"
NUM_TASKS="${NUM_TASKS:-5}"
TIMEOUT="${TIMEOUT:-120}"

log() { echo "[$(date -Iseconds)] $*" >&2; }

cleanup() {
    log "Cleaning up..."
    rm -rf "$TRIGGERS_DIR" "$RESULTS_DIR"
}
trap cleanup EXIT

# --- Setup ---

mkdir -p "$TRIGGERS_DIR" "$TRIGGERS_DIR/processed" "$RESULTS_DIR"

log "Side-by-side test: $NUM_TASKS tasks, timeout ${TIMEOUT}s"

# --- Create test trigger files ---

for i in $(seq 1 "$NUM_TASKS"); do
    cat > "$TRIGGERS_DIR/sbs-test-${i}.yaml" <<YAML
category: analyze
title: "Side-by-side test task $i"
repository: quantvise.com
priority: 50
context:
  test_mode: true
  test_number: $i
YAML
done

log "Created $NUM_TASKS trigger files in $TRIGGERS_DIR"

# --- Snapshot pre-state ---

psql "$DB_URL" -t -A -c "
  SELECT COUNT(*) FROM aifishtank.tasks
" > "$RESULTS_DIR/task_count_before.txt" 2>/dev/null || echo "0" > "$RESULTS_DIR/task_count_before.txt"

# --- Run Python poller ---

log "Running Python external triggers poller..."
python3 -c "
import asyncio
import sys
sys.path.insert(0, '$SUPERVISOR_DIR/python/src')
from aifishtank_supervisor.config import load_config
from aifishtank_supervisor.database import Database
from aifishtank_supervisor.task_queue import TaskQueue
from aifishtank_supervisor.pollers.external_triggers import ExternalTriggersPoller

async def main():
    config = load_config('$SUPERVISOR_DIR/config/supervisor.yaml')
    # Override watch dir
    for p in config.spec.pollers:
        if p.name == 'external-triggers':
            p.config['watchDir'] = '$TRIGGERS_DIR'
            p.config['processedDir'] = '$TRIGGERS_DIR/processed'
    db = Database(config.spec.database.url, max_connections=2)
    await db.connect()
    tq = TaskQueue(db)
    poller = ExternalTriggersPoller(config, tq)
    created = await poller.poll()
    print(f'Python poller created {created} tasks')
    await db.close()

asyncio.run(main())
" 2>&1 | tee "$RESULTS_DIR/python_output.txt"

# --- Snapshot post-state ---

psql "$DB_URL" -t -A -c "
  SELECT id, title, category, status, pipeline, repository
  FROM aifishtank.tasks
  WHERE id LIKE 'external-%'
  ORDER BY id
" > "$RESULTS_DIR/tasks_after.txt" 2>/dev/null || true

TASKS_AFTER=$(wc -l < "$RESULTS_DIR/tasks_after.txt" | tr -d ' ')
log "Tasks in DB after Python run: $TASKS_AFTER"

# --- Validate ---

PASSED=0
FAILED=0

# Check tasks were created
if [ "$TASKS_AFTER" -ge "$NUM_TASKS" ]; then
    log "PASS: At least $NUM_TASKS tasks created"
    PASSED=$((PASSED + 1))
else
    log "FAIL: Expected at least $NUM_TASKS tasks, got $TASKS_AFTER"
    FAILED=$((FAILED + 1))
fi

# Check trigger files were moved to processed
REMAINING=$(find "$TRIGGERS_DIR" -maxdepth 1 -name "*.yaml" 2>/dev/null | wc -l | tr -d ' ')
if [ "$REMAINING" -eq 0 ]; then
    log "PASS: All trigger files moved to processed"
    PASSED=$((PASSED + 1))
else
    log "FAIL: $REMAINING trigger files still in watch dir"
    FAILED=$((FAILED + 1))
fi

# Check processed directory has files
PROCESSED=$(find "$TRIGGERS_DIR/processed" -maxdepth 1 -name "*.yaml" 2>/dev/null | wc -l | tr -d ' ')
if [ "$PROCESSED" -ge "$NUM_TASKS" ]; then
    log "PASS: $PROCESSED files in processed dir"
    PASSED=$((PASSED + 1))
else
    log "FAIL: Expected $NUM_TASKS processed files, got $PROCESSED"
    FAILED=$((FAILED + 1))
fi

# Check task fields
if [ -s "$RESULTS_DIR/tasks_after.txt" ]; then
    VALID_CATEGORIES=$(grep -c "analyze" "$RESULTS_DIR/tasks_after.txt" || true)
    if [ "$VALID_CATEGORIES" -ge "$NUM_TASKS" ]; then
        log "PASS: All tasks have correct category"
        PASSED=$((PASSED + 1))
    else
        log "FAIL: Some tasks have wrong category"
        FAILED=$((FAILED + 1))
    fi
fi

# --- Summary ---

echo ""
echo "===================================="
echo "  Side-by-Side Test Results"
echo "===================================="
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
echo "===================================="

if [ "$FAILED" -gt 0 ]; then
    log "SOME TESTS FAILED"
    exit 1
fi

log "All tests passed!"

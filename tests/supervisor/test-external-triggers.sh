#!/usr/bin/env bash
# tests/supervisor/test-external-triggers.sh
#
# Test suite for supervisor/pollers/external-triggers.sh
#
# Tests the _process_trigger_file and poll_external_triggers functions by:
#   - Sourcing the poller with stub implementations of create_task / task_exists
#     / update_poll_state so no real database is required.
#   - Creating temporary trigger files (YAML and JSON) in a temp directory.
#   - Asserting that valid files are moved to processed/, invalid files are
#     moved to processed/failed/, and that create_task is called with the
#     correct arguments.
#
# Usage:
#   bash tests/supervisor/test-external-triggers.sh
#
# Exit codes:
#   0  All tests passed
#   1  One or more tests failed
#
# Dependencies: jq, yq (v4+), bash 4+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TRIGGER_POLLER="$PROJECT_ROOT/supervisor/pollers/external-triggers.sh"

# ── Dependency checks ──────────────────────────────────────────────────────────

if ! command -v jq &>/dev/null; then
  echo "SKIP: jq not installed — cannot run external-triggers tests"
  exit 0
fi
if ! command -v yq &>/dev/null; then
  echo "SKIP: yq not installed — cannot run external-triggers tests"
  exit 0
fi

# ── Test framework ─────────────────────────────────────────────────────────────

PASS=0
FAIL=0
TOTAL=0

pass() {
  PASS=$(( PASS + 1 ))
  TOTAL=$(( TOTAL + 1 ))
  printf "  PASS  %s\n" "$1"
}

fail() {
  FAIL=$(( FAIL + 1 ))
  TOTAL=$(( TOTAL + 1 ))
  printf "  FAIL  %s\n        Reason: %s\n" "$1" "$2"
}

assert_exit_code() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$actual" -eq "$expected" ]]; then pass "$name"
  else fail "$name" "expected exit $expected, got $actual"; fi
}

assert_file_exists() {
  local name="$1" path="$2"
  if [[ -e "$path" ]]; then pass "$name"
  else fail "$name" "expected file/dir to exist: $path"; fi
}

assert_file_not_exists() {
  local name="$1" path="$2"
  if [[ ! -e "$path" ]]; then pass "$name"
  else fail "$name" "expected no file/dir at: $path"; fi
}

assert_equals() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then pass "$name"
  else fail "$name" "expected '$expected', got '$actual'"; fi
}

assert_contains() {
  local name="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then pass "$name"
  else fail "$name" "'$needle' not found in: $haystack"; fi
}

# ── Run a single file test in a subprocess ────────────────────────────────────
# Each test is executed in a subshell so that sourcing the poller and stub
# functions does not pollute subsequent tests.

run_trigger_test() {
  local test_script="$1"
  bash -c "$test_script"
}

# ── Workspace setup ────────────────────────────────────────────────────────────

WORKSPACE="$(mktemp -d)"
WATCH_DIR="$WORKSPACE/triggers"
PROCESSED_DIR="$WORKSPACE/triggers/processed"
mkdir -p "$WATCH_DIR"

cleanup() {
  rm -rf "$WORKSPACE"
}
trap cleanup EXIT

# ── Helper: count files matching glob ─────────────────────────────────────────

count_files() {
  local dir="$1" pattern="$2"
  find "$dir" -maxdepth 1 -name "$pattern" 2>/dev/null | wc -l | tr -d ' '
}

count_files_recursive() {
  local dir="$1" pattern="$2"
  find "$dir" -name "$pattern" 2>/dev/null | wc -l | tr -d ' '
}

# ── Tests ──────────────────────────────────────────────────────────────────────

echo ""
echo "external-triggers.sh — poll_external_triggers"
echo "=============================================="

# ── Test 1: Valid YAML trigger creates task and moves file to processed/ ───────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/task-001.yaml" <<YAML
category: analyze
title: Analyze the new authentication module
repository: my-repo
priority: 30
pipeline: feature-pipeline
context:
  issue_number: 42
  labels:
    - bug
YAML

# Run poll_external_triggers with stub dependencies
actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'

  # Stub: task_exists always returns 1 (task does not exist yet)
  task_exists() { return 1; }
  export -f task_exists

  # Stub: create_task always succeeds
  create_task() { return 0; }
  export -f create_task

  # Stub: update_poll_state is a no-op
  update_poll_state() { return 0; }
  export -f update_poll_state

  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "yaml-valid: poll exits 0" 0 "$actual_exit"
assert_file_not_exists "yaml-valid: trigger file removed from watch dir" "$WATCH_DIR/task-001.yaml"

# File should have been moved to processed/ with a timestamp prefix
processed_count="$(count_files "$PROCESSED_DIR" "*task-001.yaml")"
assert_equals "yaml-valid: trigger file moved to processed/" "1" "$processed_count"

# ── Test 2: Valid JSON trigger is processed ────────────────────────────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/task-002.json" <<JSON
{
  "category": "review",
  "title": "Review PR #55 for the payments module",
  "repository": "payments-service",
  "priority": 20,
  "pipeline": "pr-review-pipeline",
  "context": {
    "pr_number": 55
  }
}
JSON

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() { return 0; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "json-valid: poll exits 0" 0 "$actual_exit"
assert_file_not_exists "json-valid: trigger file removed from watch dir" "$WATCH_DIR/task-002.json"

processed_count="$(count_files "$PROCESSED_DIR" "*task-002.json")"
assert_equals "json-valid: trigger file moved to processed/" "1" "$processed_count"

# ── Test 3: Trigger with missing 'category' field moves to failed/ ─────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/bad-no-category.yaml" <<YAML
title: Task with no category
repository: my-repo
YAML

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() { return 0; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "missing-category: poll exits 0 (errors are non-fatal to poller)" 0 "$actual_exit"
assert_file_not_exists "missing-category: file removed from watch dir" "$WATCH_DIR/bad-no-category.yaml"

failed_count="$(count_files_recursive "$PROCESSED_DIR/failed" "*missing-category*bad-no-category.yaml")"
assert_equals "missing-category: file moved to processed/failed/" "1" "$failed_count"

# ── Test 4: Trigger with missing 'title' field moves to failed/ ───────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/bad-no-title.yaml" <<YAML
category: analyze
repository: my-repo
YAML

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() { return 0; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "missing-title: poll exits 0" 0 "$actual_exit"
failed_count="$(count_files_recursive "$PROCESSED_DIR/failed" "*missing-title*bad-no-title.yaml")"
assert_equals "missing-title: file moved to processed/failed/" "1" "$failed_count"

# ── Test 5: Trigger with missing 'repository' field moves to failed/ ──────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/bad-no-repo.yaml" <<YAML
category: analyze
title: Task without a repository field
YAML

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() { return 0; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "missing-repository: poll exits 0" 0 "$actual_exit"
failed_count="$(count_files_recursive "$PROCESSED_DIR/failed" "*missing-repository*bad-no-repo.yaml")"
assert_equals "missing-repository: file moved to processed/failed/" "1" "$failed_count"

# ── Test 6: Invalid category value moves to failed/ ───────────────────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/bad-category.yaml" <<YAML
category: not-a-real-category
title: Task with bad category
repository: my-repo
YAML

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() { return 0; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "invalid-category: poll exits 0" 0 "$actual_exit"
failed_count="$(count_files_recursive "$PROCESSED_DIR/failed" "*invalid-category*bad-category.yaml")"
assert_equals "invalid-category: file moved to processed/failed/" "1" "$failed_count"

# ── Test 7: All valid categories are accepted ─────────────────────────────────

for cat_name in review implementation test design docs analyze; do
  rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
  mkdir -p "$WATCH_DIR"

  cat > "$WATCH_DIR/${cat_name}-task.yaml" <<YAML
category: $cat_name
title: Task for category $cat_name
repository: some-repo
YAML

  actual_exit=0
  bash -c "
    set -euo pipefail
    export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
    export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
    task_exists() { return 1; }
    create_task() { return 0; }
    update_poll_state() { return 0; }
    export -f task_exists create_task update_poll_state
    source '$TRIGGER_POLLER'
    poll_external_triggers
  " || actual_exit=$?

  assert_exit_code "valid-category '$cat_name': poll exits 0" 0 "$actual_exit"
  processed_count="$(count_files "$PROCESSED_DIR" "*${cat_name}-task.yaml")"
  assert_equals "valid-category '$cat_name': file moved to processed/" "1" "$processed_count"
done

# ── Test 8: Idempotency — already-queued task moves file without re-creating ───

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/already-queued.yaml" <<YAML
category: analyze
title: This task was already created
repository: my-repo
YAML

CREATE_CALL_COUNT=0
actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'

  # Simulate task already exists
  task_exists() { return 0; }
  create_task() {
    # Should NOT be called — write a marker file if it is
    touch '$WORKSPACE/create_task_called'
    return 0
  }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "idempotent: poll exits 0" 0 "$actual_exit"
assert_file_not_exists "idempotent: create_task was NOT called" "$WORKSPACE/create_task_called"

processed_count="$(count_files "$PROCESSED_DIR" "*already-queued.yaml")"
assert_equals "idempotent: file still moved to processed/" "1" "$processed_count"

# ── Test 9: Malformed JSON moves to failed/ ───────────────────────────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

# Write intentionally invalid JSON
printf '{ "category": "analyze", "title": broken json }' > "$WATCH_DIR/broken.json"

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() { return 0; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "malformed-json: poll exits 0" 0 "$actual_exit"
failed_count="$(count_files_recursive "$PROCESSED_DIR/failed" "*json-parse-error*broken.json")"
assert_equals "malformed-json: file moved to processed/failed/" "1" "$failed_count"

# ── Test 10: Empty watch dir — no errors, no processed files ──────────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() { return 0; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "empty-dir: poll exits 0 with no trigger files" 0 "$actual_exit"

# ── Test 11: Non-existent watch dir — graceful return, no crash ───────────────

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='/tmp/this-directory-does-not-exist-aifishtank-test'
  export CFG_TRIGGERS_PROCESSED_DIR='/tmp/this-directory-does-not-exist-aifishtank-test/processed'
  task_exists() { return 1; }
  create_task() { return 0; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "missing-watchdir: poll exits 0 gracefully" 0 "$actual_exit"

# ── Test 12: create_task failure moves to failed/ ────────────────────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/create-fails.yaml" <<YAML
category: analyze
title: This trigger will fail on create
repository: my-repo
YAML

actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  # Simulate create_task failure
  create_task() { return 1; }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "create-fails: poll exits 0 (individual errors are non-fatal)" 0 "$actual_exit"
failed_count="$(count_files_recursive "$PROCESSED_DIR/failed" "*task-creation-failed*create-fails.yaml")"
assert_equals "create-fails: file moved to processed/failed/" "1" "$failed_count"

# ── Test 13: Pipeline auto-selected when not specified ────────────────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

# Write trigger without pipeline field
cat > "$WATCH_DIR/no-pipeline.yaml" <<YAML
category: analyze
title: No pipeline specified - should auto-select
repository: my-repo
YAML

# Capture the pipeline argument passed to create_task
PIPELINE_LOG="$WORKSPACE/pipeline-log.txt"
actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() {
    # Args: id title category source source_ref repository pipeline context
    echo \"\$7\" > '$PIPELINE_LOG'
    return 0
  }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "auto-pipeline: poll exits 0" 0 "$actual_exit"
if [[ -f "$PIPELINE_LOG" ]]; then
  selected_pipeline="$(cat "$PIPELINE_LOG" | tr -d '[:space:]')"
  assert_equals "auto-pipeline: 'analyze' category gets feature-pipeline" \
    "feature-pipeline" "$selected_pipeline"
else
  fail "auto-pipeline: pipeline log was written" "log file not found"
fi

# ── Test 14: Context enrichment includes trigger metadata ─────────────────────

rm -rf "$WATCH_DIR"/* "$PROCESSED_DIR" 2>/dev/null || true
mkdir -p "$WATCH_DIR"

cat > "$WATCH_DIR/context-check.yaml" <<YAML
category: analyze
title: Context enrichment check
repository: my-repo
labels:
  - urgent
  - backend
context:
  user_notes: "please check the auth module"
YAML

CONTEXT_LOG="$WORKSPACE/context-log.txt"
actual_exit=0
bash -c "
  set -euo pipefail
  export CFG_TRIGGERS_WATCH_DIR='$WATCH_DIR'
  export CFG_TRIGGERS_PROCESSED_DIR='$PROCESSED_DIR'
  task_exists() { return 1; }
  create_task() {
    # Arg \$8 is context_json
    echo \"\$8\" > '$CONTEXT_LOG'
    return 0
  }
  update_poll_state() { return 0; }
  export -f task_exists create_task update_poll_state
  source '$TRIGGER_POLLER'
  poll_external_triggers
" || actual_exit=$?

assert_exit_code "context-enrichment: poll exits 0" 0 "$actual_exit"
if [[ -f "$CONTEXT_LOG" ]]; then
  context_json="$(cat "$CONTEXT_LOG")"
  has_trigger_file="$(echo "$context_json" | jq 'has("_trigger_file")' 2>/dev/null || echo false)"
  assert_equals "context-enrichment: _trigger_file key present" "true" "$has_trigger_file"

  has_triggered_at="$(echo "$context_json" | jq 'has("_triggered_at")' 2>/dev/null || echo false)"
  assert_equals "context-enrichment: _triggered_at key present" "true" "$has_triggered_at"

  has_labels="$(echo "$context_json" | jq 'has("_labels")' 2>/dev/null || echo false)"
  assert_equals "context-enrichment: _labels key present" "true" "$has_labels"
else
  fail "context-enrichment: context log was written" "log file not found"
fi

# ── Summary ────────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS/$TOTAL passed, $FAIL failed"
echo ""

[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1

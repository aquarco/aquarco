#!/usr/bin/env bash
# tests/supervisor/test-validate-agent.sh
#
# Test suite for supervisor/scripts/validate-agent.sh
#
# Usage:
#   bash tests/supervisor/test-validate-agent.sh
#
# Exit codes:
#   0  All tests passed
#   1  One or more tests failed
#
# Dependencies: yq (v4+), bash 4+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VALIDATE_SCRIPT="$PROJECT_ROOT/supervisor/scripts/validate-agent.sh"

# ── Test framework ─────────────────────────────────────────────────────────────

PASS=0
FAIL=0
TOTAL=0

pass() {
  local name="$1"
  PASS=$(( PASS + 1 ))
  TOTAL=$(( TOTAL + 1 ))
  printf "  PASS  %s\n" "$name"
}

fail() {
  local name="$1"
  local reason="$2"
  FAIL=$(( FAIL + 1 ))
  TOTAL=$(( TOTAL + 1 ))
  printf "  FAIL  %s\n        Reason: %s\n" "$name" "$reason"
}

assert_exit_code() {
  local name="$1"
  local expected="$2"
  local actual="$3"
  if [[ "$actual" -eq "$expected" ]]; then
    pass "$name"
  else
    fail "$name" "expected exit $expected, got $actual"
  fi
}

# ── Temp workspace ─────────────────────────────────────────────────────────────

TMPDIR_ROOT="$(mktemp -d)"
DEFS_DIR="$TMPDIR_ROOT/definitions"
PROMPTS_DIR="$TMPDIR_ROOT/prompts"
mkdir -p "$DEFS_DIR" "$PROMPTS_DIR"

cleanup() {
  rm -rf "$TMPDIR_ROOT"
}
trap cleanup EXIT

# validate-agent.sh derives PROMPTS_DIR relative to the script's PROJECT_ROOT
# ($SCRIPT_DIR/../..) which is the real project root. We need the prompt file
# to actually exist at agents/prompts/. Use the real prompts directory by
# symlinking a test prompt file there, or use an existing prompt file.
#
# Simpler: write test definitions that reference a real existing prompt file.
REAL_PROMPTS_DIR="$PROJECT_ROOT/agents/prompts"
EXISTING_PROMPT="$(ls "$REAL_PROMPTS_DIR"/*.md 2>/dev/null | head -1 | xargs basename 2>/dev/null || echo "")"

if [[ -z "$EXISTING_PROMPT" ]]; then
  echo "SKIP: No prompt files found in $REAL_PROMPTS_DIR — cannot run tests that require a real prompt file"
  echo "      Create at least one .md file in agents/prompts/ first."
  exit 0
fi

# ── Helper: write a valid definition file ─────────────────────────────────────

write_valid_definition() {
  local path="$1"
  local name="${2:-test-agent}"
  local prompt="${3:-$EXISTING_PROMPT}"
  cat > "$path" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: $name
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $prompt
  output:
    format: task-file
YAML
}

# ── Tests ──────────────────────────────────────────────────────────────────────

echo ""
echo "validate-agent.sh"
echo "================="

# Test 1: valid definition exits 0
DEF="$TMPDIR_ROOT/valid-agent.yaml"
write_valid_definition "$DEF"
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "valid definition exits 0" 0 "$actual_exit"

# Test 2: missing apiVersion exits 1
DEF="$TMPDIR_ROOT/bad-apiversion.yaml"
cat > "$DEF" <<YAML
apiVersion: wrong.api/v99
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "wrong apiVersion exits 1" 1 "$actual_exit"

# Test 3: wrong kind exits 1
DEF="$TMPDIR_ROOT/bad-kind.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: WrongKind
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "wrong kind exits 1" 1 "$actual_exit"

# Test 4: missing metadata.name exits 1
DEF="$TMPDIR_ROOT/missing-name.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "missing metadata.name exits 1" 1 "$actual_exit"

# Test 5: invalid name format (uppercase) exits 1
DEF="$TMPDIR_ROOT/bad-name.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: BadName_With_Underscores
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "invalid metadata.name (not kebab-case) exits 1" 1 "$actual_exit"

# Test 6: invalid semver exits 1
DEF="$TMPDIR_ROOT/bad-version.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "not-semver"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "invalid semver version exits 1" 1 "$actual_exit"

# Test 7: description too short exits 1
DEF="$TMPDIR_ROOT/short-desc.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "Short"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "description < 10 chars exits 1" 1 "$actual_exit"

# Test 8: empty categories exits 1
DEF="$TMPDIR_ROOT/empty-categories.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories: []
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "empty spec.categories exits 1" 1 "$actual_exit"

# Test 9: invalid category value exits 1
DEF="$TMPDIR_ROOT/bad-category.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - invalid-category
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "invalid category value exits 1" 1 "$actual_exit"

# Test 10: all valid categories accepted
for cat in review implementation test design docs analyze; do
  DEF="$TMPDIR_ROOT/cat-${cat}.yaml"
  cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: ${cat}-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - $cat
  promptFile: $EXISTING_PROMPT
  output:
    format: task-file
YAML
  actual_exit=0
  bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
  assert_exit_code "valid category '$cat' exits 0" 0 "$actual_exit"
done

# Test 11: missing promptFile field exits 1
DEF="$TMPDIR_ROOT/no-promptfile.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "missing spec.promptFile exits 1" 1 "$actual_exit"

# Test 12: promptFile references nonexistent file exits 1
DEF="$TMPDIR_ROOT/bad-promptfile.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: this-file-does-not-exist.md
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "promptFile pointing to nonexistent file exits 1" 1 "$actual_exit"

# Test 13: invalid output format exits 1
DEF="$TMPDIR_ROOT/bad-output.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  output:
    format: not-a-valid-format
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "invalid spec.output.format exits 1" 1 "$actual_exit"

# Test 14: all valid output formats accepted
for fmt in task-file github-pr-comment commit issue none; do
  DEF="$TMPDIR_ROOT/fmt-${fmt//\//-}.yaml"
  cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  output:
    format: $fmt
YAML
  actual_exit=0
  bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
  assert_exit_code "valid output format '$fmt' exits 0" 0 "$actual_exit"
done

# Test 15: priority out of range exits 1
DEF="$TMPDIR_ROOT/bad-priority.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  priority: 999
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "priority > 100 exits 1" 1 "$actual_exit"

# Test 16: priority = 0 exits 1
DEF="$TMPDIR_ROOT/priority-zero.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  priority: 0
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "priority = 0 exits 1" 1 "$actual_exit"

# Test 17: valid priority within range exits 0
DEF="$TMPDIR_ROOT/good-priority.yaml"
cat > "$DEF" <<YAML
apiVersion: aquarco.agents/v1
kind: AgentDefinition
metadata:
  name: test-agent
  version: "1.0.0"
  description: "A test agent definition with enough characters"
spec:
  categories:
    - analyze
  promptFile: $EXISTING_PROMPT
  priority: 50
  output:
    format: task-file
YAML
actual_exit=0
bash "$VALIDATE_SCRIPT" "$DEF" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "valid priority 50 exits 0" 0 "$actual_exit"

# Test 18: calling without arguments exits 2
actual_exit=0
bash "$VALIDATE_SCRIPT" >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "no arguments exits 2" 2 "$actual_exit"

# Test 19: calling with nonexistent file exits 2
actual_exit=0
bash "$VALIDATE_SCRIPT" /nonexistent/path/agent.yaml >/dev/null 2>&1 || actual_exit=$?
assert_exit_code "nonexistent file path exits 2" 2 "$actual_exit"

# ── Summary ────────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS/$TOTAL passed, $FAIL failed"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0

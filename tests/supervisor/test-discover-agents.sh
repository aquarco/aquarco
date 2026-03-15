#!/usr/bin/env bash
# tests/supervisor/test-discover-agents.sh
#
# Test suite for supervisor/scripts/discover-agents.sh
#
# Creates a temporary workspace with valid and invalid agent definitions,
# then runs discover-agents.sh and asserts registry contents and exit codes.
#
# Usage:
#   bash tests/supervisor/test-discover-agents.sh
#
# Exit codes:
#   0  All tests passed
#   1  One or more tests failed
#
# Dependencies: yq (v4+), jq, bash 4+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DISCOVER_SCRIPT="$PROJECT_ROOT/supervisor/scripts/discover-agents.sh"

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
  if [[ "$actual" -eq "$expected" ]]; then
    pass "$name"
  else
    fail "$name" "expected exit $expected, got $actual"
  fi
}

assert_equals() {
  local name="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    pass "$name"
  else
    fail "$name" "expected '$expected', got '$actual'"
  fi
}

assert_contains() {
  local name="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    pass "$name"
  else
    fail "$name" "'$needle' not found in output"
  fi
}

assert_not_contains() {
  local name="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    fail "$name" "'$needle' was found in output but should not be"
  else
    pass "$name"
  fi
}

# ── Dependency check ───────────────────────────────────────────────────────────

if ! command -v yq &>/dev/null; then
  echo "SKIP: yq not installed — cannot run discover-agents tests"
  exit 0
fi
if ! command -v jq &>/dev/null; then
  echo "SKIP: jq not installed — cannot run discover-agents tests"
  exit 0
fi

# ── Workspace setup ────────────────────────────────────────────────────────────
# discover-agents.sh hardcodes paths relative to PROJECT_ROOT.
# We override DEFINITIONS_DIR and PROMPTS_DIR by patching the environment
# via a wrapper that replaces the path variables before running the script,
# because the script uses $PROJECT_ROOT derived from BASH_SOURCE[0].
#
# Strategy: create a shadow project tree in TMPDIR and run the script from
# a symlinked copy that points to our fake definitions/prompts directories.

WORKSPACE="$(mktemp -d)"
FAKE_ROOT="$WORKSPACE/project"
FAKE_DEFS="$FAKE_ROOT/agents/definitions"
FAKE_PROMPTS="$FAKE_ROOT/agents/prompts"
FAKE_SUPERVISOR_SCRIPTS="$FAKE_ROOT/supervisor/scripts"
REGISTRY_OUT="$WORKSPACE/registry.json"

mkdir -p "$FAKE_DEFS" "$FAKE_PROMPTS" "$FAKE_SUPERVISOR_SCRIPTS"

# Copy the real discover-agents.sh into the fake tree so PROJECT_ROOT resolves correctly
cp "$DISCOVER_SCRIPT" "$FAKE_SUPERVISOR_SCRIPTS/discover-agents.sh"

cleanup() {
  rm -rf "$WORKSPACE"
}
trap cleanup EXIT

# ── Helpers ────────────────────────────────────────────────────────────────────

write_prompt() {
  local name="$1"
  echo "# $name prompt" > "$FAKE_PROMPTS/${name}.md"
}

write_valid_def() {
  local name="$1"
  local category="${2:-analyze}"
  write_prompt "$name"
  cat > "$FAKE_DEFS/${name}.yaml" <<YAML
apiVersion: aifishtank.agents/v1
kind: AgentDefinition
metadata:
  name: $name
  version: "1.0.0"
  description: "Valid agent definition for testing purposes"
spec:
  categories:
    - $category
  priority: 10
  promptFile: ${name}.md
  output:
    format: task-file
  triggers:
    produces:
      - analysis-complete
    consumes: []
YAML
}

write_invalid_def() {
  local name="$1"
  local reason="${2:-bad-apiversion}"
  write_prompt "$name"
  case "$reason" in
    bad-apiversion)
      cat > "$FAKE_DEFS/${name}.yaml" <<YAML
apiVersion: wrong/v99
kind: AgentDefinition
metadata:
  name: $name
  version: "1.0.0"
  description: "This definition has a wrong apiVersion"
spec:
  categories:
    - analyze
  promptFile: ${name}.md
  output:
    format: task-file
YAML
      ;;
    bad-category)
      cat > "$FAKE_DEFS/${name}.yaml" <<YAML
apiVersion: aifishtank.agents/v1
kind: AgentDefinition
metadata:
  name: $name
  version: "1.0.0"
  description: "This definition has an invalid category"
spec:
  categories:
    - not-a-real-category
  promptFile: ${name}.md
  output:
    format: task-file
YAML
      ;;
    missing-prompt)
      cat > "$FAKE_DEFS/${name}.yaml" <<YAML
apiVersion: aifishtank.agents/v1
kind: AgentDefinition
metadata:
  name: $name
  version: "1.0.0"
  description: "This definition references a missing prompt file"
spec:
  categories:
    - analyze
  promptFile: this-does-not-exist.md
  output:
    format: task-file
YAML
      ;;
  esac
}

run_discover() {
  local output_path="${1:-$REGISTRY_OUT}"
  local exit_code=0
  bash "$FAKE_SUPERVISOR_SCRIPTS/discover-agents.sh" \
    --output "$output_path" 2>/dev/null || exit_code=$?
  echo "$exit_code"
}

# ── Test Suite 1: All valid agents ─────────────────────────────────────────────

echo ""
echo "discover-agents.sh — suite 1: all valid definitions"
echo "====================================================="

# Reset workspace
rm -f "$FAKE_DEFS"/*.yaml "$FAKE_PROMPTS"/*.md "$REGISTRY_OUT" 2>/dev/null || true

write_valid_def "alpha-agent" "analyze"
write_valid_def "beta-agent" "review"
write_valid_def "gamma-agent" "implementation"

actual_exit="$(run_discover)"
assert_exit_code "all-valid: exits 0" 0 "$actual_exit"

if [[ -f "$REGISTRY_OUT" ]]; then
  pass "all-valid: registry file created"

  agent_count="$(jq '.agentCount' "$REGISTRY_OUT")"
  assert_equals "all-valid: registry contains 3 agents" "3" "$agent_count"

  agent_names="$(jq -r '[.agents[].name] | sort | join(",")' "$REGISTRY_OUT")"
  assert_equals "all-valid: all agent names in registry" \
    "alpha-agent,beta-agent,gamma-agent" "$agent_names"

  schema_version="$(jq -r '.schemaVersion' "$REGISTRY_OUT")"
  assert_equals "all-valid: schemaVersion is 1.0.0" "1.0.0" "$schema_version"

  has_generated_at="$(jq 'has("generatedAt")' "$REGISTRY_OUT")"
  assert_equals "all-valid: registry has generatedAt timestamp" "true" "$has_generated_at"

  has_category_index="$(jq 'has("categoryIndex")' "$REGISTRY_OUT")"
  assert_equals "all-valid: registry has categoryIndex" "true" "$has_category_index"

  analyze_in_index="$(jq -r '.categoryIndex | has("analyze")' "$REGISTRY_OUT")"
  assert_equals "all-valid: categoryIndex contains 'analyze'" "true" "$analyze_in_index"
else
  fail "all-valid: registry file exists" "file not found at $REGISTRY_OUT"
fi

# ── Test Suite 2: Mix of valid and invalid ─────────────────────────────────────

echo ""
echo "discover-agents.sh — suite 2: mixed valid/invalid definitions"
echo "=============================================================="

rm -f "$FAKE_DEFS"/*.yaml "$FAKE_PROMPTS"/*.md "$REGISTRY_OUT" 2>/dev/null || true

write_valid_def "good-agent" "analyze"
write_invalid_def "bad-apiversion-agent" "bad-apiversion"
write_invalid_def "bad-category-agent" "bad-category"

actual_exit="$(run_discover)"
assert_exit_code "mixed: exits 1 when invalid definitions present" 1 "$actual_exit"

if [[ ! -f "$REGISTRY_OUT" ]]; then
  pass "mixed: registry NOT written when errors present"
else
  fail "mixed: registry NOT written when errors present" "registry file was written despite errors"
fi

# ── Test Suite 3: Missing prompt file ─────────────────────────────────────────

echo ""
echo "discover-agents.sh — suite 3: definition with missing prompt file"
echo "=================================================================="

rm -f "$FAKE_DEFS"/*.yaml "$FAKE_PROMPTS"/*.md "$REGISTRY_OUT" 2>/dev/null || true

write_invalid_def "missing-prompt-agent" "missing-prompt"

actual_exit="$(run_discover)"
assert_exit_code "missing-prompt: exits 1" 1 "$actual_exit"

if [[ ! -f "$REGISTRY_OUT" ]]; then
  pass "missing-prompt: registry NOT written"
else
  fail "missing-prompt: registry NOT written" "registry was written despite missing prompt"
fi

# ── Test Suite 4: Category index correctness ──────────────────────────────────

echo ""
echo "discover-agents.sh — suite 4: category index ordering"
echo "======================================================"

rm -f "$FAKE_DEFS"/*.yaml "$FAKE_PROMPTS"/*.md "$REGISTRY_OUT" 2>/dev/null || true

# Two agents in same category, different priorities — lower priority number wins
cat > "$FAKE_PROMPTS/high-pri.md" <<'MD'
# high priority agent
MD
cat > "$FAKE_PROMPTS/low-pri.md" <<'MD'
# low priority agent
MD
cat > "$FAKE_DEFS/high-pri-agent.yaml" <<YAML
apiVersion: aifishtank.agents/v1
kind: AgentDefinition
metadata:
  name: high-pri-agent
  version: "1.0.0"
  description: "High priority agent for testing priority ordering"
spec:
  categories:
    - analyze
  priority: 1
  promptFile: high-pri.md
  output:
    format: task-file
YAML
cat > "$FAKE_DEFS/low-pri-agent.yaml" <<YAML
apiVersion: aifishtank.agents/v1
kind: AgentDefinition
metadata:
  name: low-pri-agent
  version: "1.0.0"
  description: "Low priority agent for testing priority ordering"
spec:
  categories:
    - analyze
  priority: 99
  promptFile: low-pri.md
  output:
    format: task-file
YAML

actual_exit="$(run_discover)"
assert_exit_code "priority-order: exits 0" 0 "$actual_exit"

if [[ -f "$REGISTRY_OUT" ]]; then
  first_in_analyze="$(jq -r '.categoryIndex.analyze[0]' "$REGISTRY_OUT")"
  assert_equals "priority-order: highest priority agent is first in category index" \
    "high-pri-agent" "$first_in_analyze"
else
  fail "priority-order: registry file exists" "file not found"
fi

# ── Test Suite 5: Output path is written correctly ────────────────────────────

echo ""
echo "discover-agents.sh — suite 5: output path flag"
echo "================================================"

rm -f "$FAKE_DEFS"/*.yaml "$FAKE_PROMPTS"/*.md "$REGISTRY_OUT" 2>/dev/null || true

write_valid_def "solo-agent" "docs"

CUSTOM_OUT="$WORKSPACE/custom-output/registry.json"
actual_exit=0
bash "$FAKE_SUPERVISOR_SCRIPTS/discover-agents.sh" \
  --output "$CUSTOM_OUT" 2>/dev/null || actual_exit=$?

assert_exit_code "custom-output: exits 0" 0 "$actual_exit"
if [[ -f "$CUSTOM_OUT" ]]; then
  pass "custom-output: registry written to custom path"
else
  fail "custom-output: registry written to custom path" "file not found at $CUSTOM_OUT"
fi

# ── Test Suite 6: Unknown argument ────────────────────────────────────────────

echo ""
echo "discover-agents.sh — suite 6: unknown argument"
echo "================================================"

actual_exit=0
bash "$FAKE_SUPERVISOR_SCRIPTS/discover-agents.sh" --unknown-flag 2>/dev/null || actual_exit=$?
assert_exit_code "unknown-arg: unknown flag exits 1" 1 "$actual_exit"

# ── Summary ────────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS/$TOTAL passed, $FAIL failed"
echo ""

[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1

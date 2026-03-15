#!/usr/bin/env bash
# validate-agent.sh — Validates a single agent definition file.
#
# Usage:
#   ./validate-agent.sh <definition-file>
#
# Arguments:
#   definition-file    Path to the agent definition YAML file to validate
#
# Exit codes:
#   0  Definition is valid
#   1  One or more validation errors (errors printed to stderr)
#   2  Usage error or missing dependency
#
# Dependencies:
#   - yq  (https://github.com/mikefarah/yq) v4+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROMPTS_DIR="$PROJECT_ROOT/agents/prompts"

VALID_CATEGORIES="review|implementation|test|design|docs|analyze"
VALID_OUTPUT_FORMATS="task-file|github-pr-comment|commit|issue|none"

ERRORS=0

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
  grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
  exit 2
}

if [[ $# -ne 1 ]]; then
  echo "ERROR: Expected exactly one argument (definition file path)" >&2
  usage
fi

DEFINITION_FILE="$1"

if [[ ! -f "$DEFINITION_FILE" ]]; then
  echo "ERROR: File not found: $DEFINITION_FILE" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
if ! command -v yq &>/dev/null; then
  echo "ERROR: yq is required but not installed." >&2
  echo "       Install from https://github.com/mikefarah/yq" >&2
  exit 2
fi

YQ_VERSION="$(yq --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)"
YQ_MAJOR="$(echo "$YQ_VERSION" | cut -d. -f1)"
if [[ "$YQ_MAJOR" -lt 4 ]]; then
  echo "ERROR: yq version 4+ required (found: $YQ_VERSION)" >&2
  exit 2
fi

FILE="$DEFINITION_FILE"
BASENAME="$(basename "$FILE")"

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
fail() {
  echo "FAIL  $BASENAME: $*" >&2
  (( ERRORS++ )) || true
}

pass() {
  echo "OK    $BASENAME: $*"
}

check_field() {
  local field="$1"
  local value
  value="$(yq "$field" "$FILE" 2>/dev/null || true)"
  if [[ -z "$value" || "$value" == "null" ]]; then
    fail "required field '$field' is missing or empty"
    echo ""
    return 1
  fi
  echo "$value"
}

# ---------------------------------------------------------------------------
# Run checks
# ---------------------------------------------------------------------------
echo "Validating: $DEFINITION_FILE"
echo "---"

# 1. apiVersion
API_VERSION="$(yq '.apiVersion' "$FILE" 2>/dev/null || true)"
if [[ "$API_VERSION" == "aifishtank.agents/v1" ]]; then
  pass "apiVersion = $API_VERSION"
else
  fail "apiVersion must be 'aifishtank.agents/v1' (got: '$API_VERSION')"
fi

# 2. kind
KIND="$(yq '.kind' "$FILE" 2>/dev/null || true)"
if [[ "$KIND" == "AgentDefinition" ]]; then
  pass "kind = $KIND"
else
  fail "kind must be 'AgentDefinition' (got: '$KIND')"
fi

# 3. metadata.name
NAME="$(yq '.metadata.name' "$FILE" 2>/dev/null || true)"
if [[ -z "$NAME" || "$NAME" == "null" ]]; then
  fail "metadata.name is required"
elif echo "$NAME" | grep -qE '^[a-z][a-z0-9-]*$'; then
  pass "metadata.name = $NAME (valid kebab-case)"
else
  fail "metadata.name '$NAME' must match pattern ^[a-z][a-z0-9-]*$"
fi

# 4. metadata.version (semver)
VERSION="$(yq '.metadata.version' "$FILE" 2>/dev/null || true)"
if [[ -z "$VERSION" || "$VERSION" == "null" ]]; then
  fail "metadata.version is required"
elif echo "$VERSION" | grep -qE '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'; then
  pass "metadata.version = $VERSION (valid semver)"
else
  fail "metadata.version '$VERSION' is not valid semver"
fi

# 5. metadata.description (minLength 10)
DESCRIPTION="$(yq '.metadata.description' "$FILE" 2>/dev/null || true)"
if [[ -z "$DESCRIPTION" || "$DESCRIPTION" == "null" ]]; then
  fail "metadata.description is required"
elif [[ "${#DESCRIPTION}" -lt 10 ]]; then
  fail "metadata.description is too short (${#DESCRIPTION} chars, minimum 10)"
else
  pass "metadata.description present (${#DESCRIPTION} chars)"
fi

# 6. spec.categories
CATEGORIES_COUNT="$(yq '.spec.categories | length' "$FILE" 2>/dev/null || echo 0)"
if [[ "$CATEGORIES_COUNT" -lt 1 ]]; then
  fail "spec.categories must contain at least one entry"
else
  pass "spec.categories has $CATEGORIES_COUNT entry/entries"
  for (( i=0; i<CATEGORIES_COUNT; i++ )); do
    CAT="$(yq ".spec.categories[$i]" "$FILE" 2>/dev/null || true)"
    if echo "$CAT" | grep -qE "^($VALID_CATEGORIES)$"; then
      pass "  spec.categories[$i] = $CAT"
    else
      fail "  spec.categories[$i] = '$CAT' (allowed: $VALID_CATEGORIES)"
    fi
  done
fi

# 7. spec.promptFile + existence check
PROMPT_FILE="$(yq '.spec.promptFile' "$FILE" 2>/dev/null || true)"
if [[ -z "$PROMPT_FILE" || "$PROMPT_FILE" == "null" ]]; then
  fail "spec.promptFile is required"
else
  PROMPT_PATH="$PROMPTS_DIR/$PROMPT_FILE"
  if [[ -f "$PROMPT_PATH" ]]; then
    pass "spec.promptFile = $PROMPT_FILE (file exists at $PROMPT_PATH)"
  else
    fail "spec.promptFile '$PROMPT_FILE' not found at $PROMPT_PATH"
  fi
fi

# 8. spec.output.format
OUTPUT_FORMAT="$(yq '.spec.output.format' "$FILE" 2>/dev/null || true)"
if [[ -z "$OUTPUT_FORMAT" || "$OUTPUT_FORMAT" == "null" ]]; then
  fail "spec.output.format is required"
elif echo "$OUTPUT_FORMAT" | grep -qE "^($VALID_OUTPUT_FORMATS)$"; then
  pass "spec.output.format = $OUTPUT_FORMAT"
else
  fail "spec.output.format '$OUTPUT_FORMAT' is not valid (allowed: $VALID_OUTPUT_FORMATS)"
fi

# 9. spec.priority (optional, 1-100 if present)
PRIORITY="$(yq '.spec.priority' "$FILE" 2>/dev/null || true)"
if [[ -n "$PRIORITY" && "$PRIORITY" != "null" ]]; then
  if echo "$PRIORITY" | grep -qE '^[0-9]+$' && [[ "$PRIORITY" -ge 1 && "$PRIORITY" -le 100 ]]; then
    pass "spec.priority = $PRIORITY (valid)"
  else
    fail "spec.priority '$PRIORITY' must be an integer between 1 and 100"
  fi
else
  pass "spec.priority not set (default: 50)"
fi

# 10. spec.triggers (informational)
PRODUCES_COUNT="$(yq '.spec.triggers.produces | length' "$FILE" 2>/dev/null || echo 0)"
CONSUMES_COUNT="$(yq '.spec.triggers.consumes | length' "$FILE" 2>/dev/null || echo 0)"
pass "spec.triggers: produces $PRODUCES_COUNT event(s), consumes $CONSUMES_COUNT event(s)"

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
echo "---"
if [[ "$ERRORS" -eq 0 ]]; then
  echo "VALID  $BASENAME: all checks passed"
  exit 0
else
  echo "INVALID $BASENAME: $ERRORS check(s) failed" >&2
  exit 1
fi

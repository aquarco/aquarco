#!/usr/bin/env bash
# discover-agents.sh — Scans agent definitions, validates them, and builds a JSON registry.
#
# Usage:
#   ./discover-agents.sh [--output PATH] [--verbose]
#
# Options:
#   --output PATH    Write registry JSON to PATH (default: /var/lib/aquarco/agent-registry.json)
#   --verbose        Print per-agent validation details
#
# Exit codes:
#   0  All agent definitions are valid and registry was written successfully
#   1  One or more validation errors encountered (registry not written)
#
# Dependencies:
#   - yq  (https://github.com/mikefarah/yq) v4+
#   - jq  (https://stedolan.github.io/jq/)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DEFINITIONS_DIR="$PROJECT_ROOT/agents/definitions"
PROMPTS_DIR="$PROJECT_ROOT/agents/prompts"
DEFAULT_OUTPUT="/var/lib/aquarco/agent-registry.json"

OUTPUT_PATH="$DEFAULT_OUTPUT"
VERBOSE=false
ERRORS=0

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
usage() {
  grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      OUTPUT_PATH="$2"
      shift 2
      ;;
    --verbose)
      VERBOSE=true
      shift
      ;;
    --help|-h)
      usage
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------
check_dependency() {
  local cmd="$1"
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: Required dependency not found: $cmd" >&2
    echo "       Install $cmd and re-run." >&2
    exit 1
  fi
}

check_dependency yq
check_dependency jq

YQ_VERSION="$(yq --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)"
YQ_MAJOR="$(echo "$YQ_VERSION" | cut -d. -f1)"
if [[ "$YQ_MAJOR" -lt 4 ]]; then
  echo "ERROR: yq version 4+ required (found: $YQ_VERSION)" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
log_info() {
  echo "[INFO]  $*"
}

log_verbose() {
  if [[ "$VERBOSE" == "true" ]]; then
    echo "[DEBUG] $*"
  fi
}

log_error() {
  echo "[ERROR] $*" >&2
  (( ERRORS++ )) || true
}

log_ok() {
  if [[ "$VERBOSE" == "true" ]]; then
    echo "[OK]    $*"
  fi
}

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# Validate a required string field is present and non-empty.
validate_required() {
  local file="$1"
  local field="$2"
  local value
  value="$(yq "$field" "$file" 2>/dev/null || true)"
  if [[ -z "$value" || "$value" == "null" ]]; then
    log_error "$file: missing required field '$field'"
    return 1
  fi
  log_verbose "$file: $field = $value"
  echo "$value"
}

# Check a value is in a pipe-separated list of allowed values.
validate_enum() {
  local file="$1"
  local field="$2"
  local value="$3"
  local allowed="$4"   # pipe-separated: "a|b|c"
  if ! echo "$value" | grep -qE "^($allowed)$"; then
    log_error "$file: field '$field' has invalid value '$value' (allowed: $allowed)"
    return 1
  fi
  return 0
}

# Validate a kebab-case name pattern.
validate_kebab_case() {
  local file="$1"
  local value="$2"
  if ! echo "$value" | grep -qE '^[a-z][a-z0-9-]*$'; then
    log_error "$file: metadata.name '$value' must match ^[a-z][a-z0-9-]*$"
    return 1
  fi
}

# Validate a semver string (simplified).
validate_semver() {
  local file="$1"
  local value="$2"
  if ! echo "$value" | grep -qE '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'; then
    log_error "$file: metadata.version '$value' is not valid semver (e.g. 1.0.0)"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Validate a single definition file.
# Returns 0 if valid, 1 if any errors found.
# Outputs a JSON agent record on stdout if valid.
# ---------------------------------------------------------------------------
validate_definition() {
  local file="$1"
  local basename
  basename="$(basename "$file")"
  local file_errors=0

  log_verbose "Validating $basename ..."

  # ---- apiVersion ----
  local api_version
  api_version="$(yq '.apiVersion' "$file" 2>/dev/null || true)"
  if [[ "$api_version" != "aquarco.agents/v1" ]]; then
    log_error "$basename: apiVersion must be 'aquarco.agents/v1' (got: '$api_version')"
    (( file_errors++ )) || true
  fi

  # ---- kind ----
  local kind
  kind="$(yq '.kind' "$file" 2>/dev/null || true)"
  if [[ "$kind" != "AgentDefinition" ]]; then
    log_error "$basename: kind must be 'AgentDefinition' (got: '$kind')"
    (( file_errors++ )) || true
  fi

  # ---- metadata.name ----
  local name
  name="$(yq '.metadata.name' "$file" 2>/dev/null || true)"
  if [[ -z "$name" || "$name" == "null" ]]; then
    log_error "$basename: metadata.name is required"
    (( file_errors++ )) || true
  else
    validate_kebab_case "$basename" "$name" || (( file_errors++ )) || true
  fi

  # ---- metadata.version ----
  local version
  version="$(yq '.metadata.version' "$file" 2>/dev/null || true)"
  if [[ -z "$version" || "$version" == "null" ]]; then
    log_error "$basename: metadata.version is required"
    (( file_errors++ )) || true
  else
    validate_semver "$basename" "$version" || (( file_errors++ )) || true
  fi

  # ---- metadata.description ----
  local description
  description="$(yq '.metadata.description' "$file" 2>/dev/null || true)"
  if [[ -z "$description" || "$description" == "null" ]]; then
    log_error "$basename: metadata.description is required"
    (( file_errors++ )) || true
  elif [[ "${#description}" -lt 10 ]]; then
    log_error "$basename: metadata.description must be at least 10 characters"
    (( file_errors++ )) || true
  fi

  # ---- spec.categories ----
  local categories_count
  categories_count="$(yq '.spec.categories | length' "$file" 2>/dev/null || echo 0)"
  if [[ "$categories_count" -lt 1 ]]; then
    log_error "$basename: spec.categories must contain at least one entry"
    (( file_errors++ )) || true
  else
    local i
    for (( i=0; i<categories_count; i++ )); do
      local cat
      cat="$(yq ".spec.categories[$i]" "$file" 2>/dev/null || true)"
      if ! echo "$cat" | grep -qE '^(review|implementation|test|design|docs|analyze)$'; then
        log_error "$basename: spec.categories[$i] invalid value '$cat' (allowed: review|implementation|test|design|docs|analyze)"
        (( file_errors++ )) || true
      fi
    done
  fi

  # ---- spec.promptFile ----
  local prompt_file
  prompt_file="$(yq '.spec.promptFile' "$file" 2>/dev/null || true)"
  if [[ -z "$prompt_file" || "$prompt_file" == "null" ]]; then
    log_error "$basename: spec.promptFile is required"
    (( file_errors++ )) || true
  else
    local prompt_path="$PROMPTS_DIR/$prompt_file"
    if [[ ! -f "$prompt_path" ]]; then
      log_error "$basename: spec.promptFile '$prompt_file' not found at $prompt_path"
      (( file_errors++ )) || true
    else
      log_verbose "$basename: promptFile found at $prompt_path"
    fi
  fi

  # ---- spec.output.format ----
  local output_format
  output_format="$(yq '.spec.output.format' "$file" 2>/dev/null || true)"
  if [[ -z "$output_format" || "$output_format" == "null" ]]; then
    log_error "$basename: spec.output.format is required"
    (( file_errors++ )) || true
  else
    validate_enum "$basename" "spec.output.format" "$output_format" \
      "task-file|github-pr-comment|commit|issue|none" \
      || (( file_errors++ )) || true
  fi

  # ---- spec.priority (if present, must be integer 1-100) ----
  local priority
  priority="$(yq '.spec.priority' "$file" 2>/dev/null || true)"
  if [[ -n "$priority" && "$priority" != "null" ]]; then
    if ! echo "$priority" | grep -qE '^[0-9]+$' || [[ "$priority" -lt 1 || "$priority" -gt 100 ]]; then
      log_error "$basename: spec.priority '$priority' must be an integer between 1 and 100"
      (( file_errors++ )) || true
    fi
  fi

  # If any errors found in this file, return failure without emitting JSON.
  if [[ "$file_errors" -gt 0 ]]; then
    ERRORS=$(( ERRORS + file_errors ))
    return 1
  fi

  log_ok "$basename: valid"

  # ---- Emit JSON record for this agent ----
  local categories_json
  categories_json="$(yq -o=json '.spec.categories' "$file" 2>/dev/null || echo '[]')"

  local triggers_produces_json
  triggers_produces_json="$(yq -o=json '.spec.triggers.produces // []' "$file" 2>/dev/null || echo '[]')"

  local triggers_consumes_json
  triggers_consumes_json="$(yq -o=json '.spec.triggers.consumes // []' "$file" 2>/dev/null || echo '[]')"

  local capabilities_json
  capabilities_json="$(yq -o=json '.spec.capabilities // {}' "$file" 2>/dev/null || echo '{}')"

  local resources_json
  resources_json="$(yq -o=json '.spec.resources // {}' "$file" 2>/dev/null || echo '{}')"

  local labels_json
  labels_json="$(yq -o=json '.metadata.labels // {}' "$file" 2>/dev/null || echo '{}')"

  local priority_value
  priority_value="$(yq '.spec.priority // 50' "$file" 2>/dev/null || echo '50')"

  jq -n \
    --arg name "$name" \
    --arg version "$version" \
    --arg description "$description" \
    --arg promptFile "$prompt_file" \
    --arg outputFormat "$output_format" \
    --argjson priority "$priority_value" \
    --argjson categories "$categories_json" \
    --argjson produces "$triggers_produces_json" \
    --argjson consumes "$triggers_consumes_json" \
    --argjson capabilities "$capabilities_json" \
    --argjson resources "$resources_json" \
    --argjson labels "$labels_json" \
    --arg definitionFile "$(basename "$file")" \
    '{
      name: $name,
      version: $version,
      description: $description,
      promptFile: $promptFile,
      definitionFile: $definitionFile,
      categories: $categories,
      priority: $priority,
      outputFormat: $outputFormat,
      triggers: {
        produces: $produces,
        consumes: $consumes
      },
      capabilities: $capabilities,
      resources: $resources,
      labels: $labels
    }'
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  log_info "Aquarco agent discovery starting"
  log_info "Definitions directory : $DEFINITIONS_DIR"
  log_info "Prompts directory     : $PROMPTS_DIR"
  log_info "Output path           : $OUTPUT_PATH"
  echo ""

  if [[ ! -d "$DEFINITIONS_DIR" ]]; then
    echo "ERROR: Definitions directory not found: $DEFINITIONS_DIR" >&2
    exit 1
  fi

  if [[ ! -d "$PROMPTS_DIR" ]]; then
    echo "ERROR: Prompts directory not found: $PROMPTS_DIR" >&2
    exit 1
  fi

  # Collect all definition YAML files.
  local definition_files=()
  while IFS= read -r -d '' f; do
    definition_files+=("$f")
  done < <(find "$DEFINITIONS_DIR" -maxdepth 1 -name "*.yaml" -print0 | sort -z)

  if [[ "${#definition_files[@]}" -eq 0 ]]; then
    echo "ERROR: No YAML files found in $DEFINITIONS_DIR" >&2
    exit 1
  fi

  log_info "Found ${#definition_files[@]} definition file(s)"
  echo ""

  # Validate each file and collect JSON records.
  local agent_records=()
  for def_file in "${definition_files[@]}"; do
    local record
    if record="$(validate_definition "$def_file")"; then
      agent_records+=("$record")
      log_info "  [PASS] $(basename "$def_file")"
    else
      log_info "  [FAIL] $(basename "$def_file")"
    fi
  done

  echo ""

  if [[ "$ERRORS" -gt 0 ]]; then
    log_info "Validation failed: $ERRORS error(s) found. Registry not written."
    exit 1
  fi

  log_info "All ${#agent_records[@]} agent(s) valid. Building registry ..."

  # Build agents array JSON.
  local agents_json="[]"
  for record in "${agent_records[@]}"; do
    agents_json="$(echo "$agents_json" | jq --argjson rec "$record" '. + [$rec]')"
  done

  # Build category index: category -> sorted list of agent names (by priority asc).
  local category_index
  category_index="$(echo "$agents_json" | jq '
    reduce .[] as $agent (
      {};
      . as $idx |
      ($agent.categories[]) as $cat |
      .[$cat] += [{name: $agent.name, priority: $agent.priority}]
    ) |
    with_entries(
      .value |= sort_by(.priority) | .value |= map(.name)
    )
  ')"

  # Assemble final registry.
  local registry
  registry="$(jq -n \
    --arg generated_at "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
    --arg schema_version "1.0.0" \
    --argjson agents "$agents_json" \
    --argjson category_index "$category_index" \
    '{
      schemaVersion: $schema_version,
      generatedAt: $generated_at,
      agentCount: ($agents | length),
      agents: $agents,
      categoryIndex: $category_index
    }')"

  # Ensure output directory exists.
  local output_dir
  output_dir="$(dirname "$OUTPUT_PATH")"
  if [[ ! -d "$output_dir" ]]; then
    log_info "Creating output directory: $output_dir"
    mkdir -p "$output_dir"
  fi

  echo "$registry" > "$OUTPUT_PATH"

  log_info "Registry written to: $OUTPUT_PATH"
  log_info ""
  log_info "Summary:"
  echo "$registry" | jq -r '
    "  Total agents  : \(.agentCount)",
    "  Categories    : \(.categoryIndex | keys | join(", "))",
    "",
    "  Category index:",
    (.categoryIndex | to_entries[] | "    \(.key): \(.value | join(", "))")
  '
}

main "$@"

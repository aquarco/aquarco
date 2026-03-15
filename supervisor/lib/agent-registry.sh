#!/usr/bin/env bash
# supervisor/lib/agent-registry.sh
# Agent registry operations.
#
# Reads the agent-registry.json file (built by agents/schemas/scripts/discover-agents.sh)
# and provides category-based agent lookup, availability checking, and
# agent_instances table management.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/agent-registry.sh"
#   load_registry
#   agent_name="$(select_agent analyze)"

set -euo pipefail

# ── Globals ───────────────────────────────────────────────────────────────────

AGENT_REGISTRY_FILE="${AGENT_REGISTRY_FILE:-}"
AGENT_REGISTRY_JSON=""

# ── Logging shim ──────────────────────────────────────────────────────────────

_ar_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '{"ts":"%s","level":"%s","component":"agent-registry","msg":"%s"}\n' \
    "$ts" "$level" "$*" >&2
}

# ── Private: _psql ────────────────────────────────────────────────────────────
_ar_psql() {
  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?DATABASE_URL is not set}" \
    "$@"
}

# ── Public: load_registry ────────────────────────────────────────────────────
# Read the agent-registry.json file into AGENT_REGISTRY_JSON.
# Falls back to scanning CFG_AGENTS_DIR if no registry file is found.
#
# Usage: load_registry
load_registry() {
  # Resolve registry file path.
  if [[ -z "$AGENT_REGISTRY_FILE" ]]; then
    local agents_base="${CFG_AGENTS_DIR:-/home/agent/ai-fishtank/agents/definitions}"
    local parent_dir
    parent_dir="$(dirname "$agents_base")"
    AGENT_REGISTRY_FILE="${parent_dir}/schemas/agent-registry.json"
  fi

  if [[ ! -f "$AGENT_REGISTRY_FILE" ]]; then
    _ar_log "warn" "Registry file not found at $AGENT_REGISTRY_FILE; will attempt discovery"
    _discover_agents_inline
    return $?
  fi

  if ! command -v jq &>/dev/null; then
    _ar_log "error" "jq is required but not found in PATH"
    return 1
  fi

  AGENT_REGISTRY_JSON="$(< "$AGENT_REGISTRY_FILE")"

  local agent_count
  agent_count="$(echo "$AGENT_REGISTRY_JSON" | jq '.agents | length' 2>/dev/null || echo "0")"
  _ar_log "info" "Registry loaded: $agent_count agents from $AGENT_REGISTRY_FILE"

  # Ensure all agents have rows in the agent_instances table.
  _sync_agent_instances
  return 0
}

# ── Public: get_agents_for_category ──────────────────────────────────────────
# Print agent names that handle the given category, sorted by priority
# (lowest number = highest priority), one per line.
#
# Usage: get_agents_for_category "analyze"
get_agents_for_category() {
  local category="$1"

  if [[ -z "$AGENT_REGISTRY_JSON" ]]; then
    _ar_log "error" "Registry not loaded; call load_registry first"
    return 1
  fi

  echo "$AGENT_REGISTRY_JSON" \
    | jq -r --arg cat "$category" '
        .agents[]
        | select(.spec.categories[]? == $cat)
        | [(.spec.priority // 50 | tostring), .metadata.name]
        | join("\t")
      ' \
    | sort -t$'\t' -k1 -n \
    | cut -f2
}

# ── Public: select_agent ─────────────────────────────────────────────────────
# Pick the best available agent for a given category.
# Returns the agent name to stdout, or exits with code 1 if none available.
#
# An agent is "available" if its active_count < maxConcurrent (from definition).
#
# Usage: agent_name="$(select_agent analyze)"
select_agent() {
  local category="$1"

  _ar_log "info" "Selecting agent for category=$category"

  local candidates
  # Read into array to avoid subshell issues.
  mapfile -t candidates < <(get_agents_for_category "$category" 2>/dev/null)

  if [[ "${#candidates[@]}" -eq 0 ]]; then
    _ar_log "error" "No agents registered for category=$category"
    return 1
  fi

  for agent_name in "${candidates[@]}"; do
    if agent_is_available "$agent_name"; then
      _ar_log "info" "Selected agent=$agent_name for category=$category"
      echo "$agent_name"
      return 0
    else
      _ar_log "info" "Agent $agent_name is at capacity; skipping"
    fi
  done

  _ar_log "warn" "All agents for category=$category are at capacity"
  return 1
}

# ── Public: agent_is_available ───────────────────────────────────────────────
# Return 0 (true) if the agent has capacity for another execution.
# Return 1 (false) if it is at or above its maxConcurrent limit.
#
# Usage: if agent_is_available "analyze-agent"; then ...
agent_is_available() {
  local agent_name="$1"

  # Get maxConcurrent from the registry.
  local max_concurrent
  max_concurrent="$(echo "$AGENT_REGISTRY_JSON" \
    | jq -r --arg name "$agent_name" '
        .agents[]
        | select(.metadata.name == $name)
        | .spec.resources.maxConcurrent // 1
      ' 2>/dev/null || echo "1")"

  # Get active_count from the database.
  local active_count_sql
  active_count_sql="$(cat <<SQL
SET search_path TO aifishtank, public;
SELECT COALESCE(active_count, 0) FROM agent_instances WHERE agent_name = \$\$${agent_name}\$\$;
SQL
)"

  local active_count
  active_count="$(_ar_psql -c "$active_count_sql" 2>/dev/null | tr -d '[:space:]' || echo "0")"
  active_count="${active_count:-0}"

  [[ "$active_count" -lt "$max_concurrent" ]]
}

# ── Public: increment_agent_instances ────────────────────────────────────────
# Increment the active_count for an agent and update last_execution_at.
#
# Usage: increment_agent_instances "analyze-agent"
increment_agent_instances() {
  local agent_name="$1"

  _ar_log "info" "Incrementing instance count for agent=$agent_name"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
INSERT INTO agent_instances (agent_name, active_count, total_executions, last_execution_at)
VALUES (\$\$${agent_name}\$\$, 1, 1, NOW())
ON CONFLICT (agent_name) DO UPDATE
  SET active_count      = agent_instances.active_count + 1,
      total_executions  = agent_instances.total_executions + 1,
      last_execution_at = NOW();
SQL
)"

  _ar_psql -c "$sql" &>/dev/null
}

# ── Public: decrement_agent_instances ────────────────────────────────────────
# Decrement the active_count for an agent (floor at 0).
#
# Usage: decrement_agent_instances "analyze-agent"
decrement_agent_instances() {
  local agent_name="$1"

  _ar_log "info" "Decrementing instance count for agent=$agent_name"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
UPDATE agent_instances
SET    active_count = GREATEST(active_count - 1, 0)
WHERE  agent_name   = \$\$${agent_name}\$\$;
SQL
)"

  _ar_psql -c "$sql" &>/dev/null
}

# ── Public: get_agent_definition ─────────────────────────────────────────────
# Read the YAML definition file for a named agent and print its contents.
# Returns the raw YAML text to stdout.
#
# Usage: definition="$(get_agent_definition "analyze-agent")"
get_agent_definition() {
  local agent_name="$1"
  local agents_dir="${CFG_AGENTS_DIR:-/home/agent/ai-fishtank/agents/definitions}"
  local def_file="${agents_dir}/${agent_name}.yaml"

  if [[ ! -f "$def_file" ]]; then
    _ar_log "error" "Agent definition file not found: $def_file"
    return 1
  fi

  cat "$def_file"
}

# ── Public: get_agent_prompt_file ────────────────────────────────────────────
# Return the absolute path to the prompt file for the given agent.
#
# Usage: prompt_path="$(get_agent_prompt_file "analyze-agent")"
get_agent_prompt_file() {
  local agent_name="$1"
  local prompts_dir="${CFG_PROMPTS_DIR:-/home/agent/ai-fishtank/agents/prompts}"

  # Look up promptFile from the registry.
  local prompt_file
  prompt_file="$(echo "$AGENT_REGISTRY_JSON" \
    | jq -r --arg name "$agent_name" '
        .agents[]
        | select(.metadata.name == $name)
        | .spec.promptFile
      ' 2>/dev/null || echo "")"

  if [[ -z "$prompt_file" || "$prompt_file" == "null" ]]; then
    # Fall back to <agent_name>.md convention.
    prompt_file="${agent_name}.md"
  fi

  echo "${prompts_dir}/${prompt_file}"
}

# ── Public: get_agent_timeout ────────────────────────────────────────────────
# Return the timeoutMinutes for the given agent (default: 30).
#
# Usage: timeout_mins="$(get_agent_timeout "analyze-agent")"
get_agent_timeout() {
  local agent_name="$1"

  echo "$AGENT_REGISTRY_JSON" \
    | jq -r --arg name "$agent_name" '
        .agents[]
        | select(.metadata.name == $name)
        | .spec.resources.timeoutMinutes // 30
      ' 2>/dev/null || echo "30"
}

# ── Private: _sync_agent_instances ───────────────────────────────────────────
# Ensure every agent in the registry has a row in agent_instances.
# New rows start with active_count = 0.
_sync_agent_instances() {
  local agent_names
  mapfile -t agent_names < <(echo "$AGENT_REGISTRY_JSON" | jq -r '.agents[].metadata.name' 2>/dev/null)

  for agent_name in "${agent_names[@]}"; do
    local sql
    sql="$(cat <<SQL
SET search_path TO aifishtank, public;
INSERT INTO agent_instances (agent_name, active_count, total_executions)
VALUES (\$\$${agent_name}\$\$, 0, 0)
ON CONFLICT (agent_name) DO NOTHING;
SQL
)"
    _ar_psql -c "$sql" &>/dev/null || true
  done

  _ar_log "info" "Synced ${#agent_names[@]} agents to agent_instances table"
}

# ── Private: _discover_agents_inline ─────────────────────────────────────────
# Build the registry JSON inline from definition YAML files.
# Used as a fallback when no registry file exists.
_discover_agents_inline() {
  local agents_dir="${CFG_AGENTS_DIR:-/home/agent/ai-fishtank/agents/definitions}"

  if [[ ! -d "$agents_dir" ]]; then
    _ar_log "error" "Agents directory not found: $agents_dir"
    return 1
  fi

  if ! command -v yq &>/dev/null || ! command -v jq &>/dev/null; then
    _ar_log "error" "yq and jq are required for inline agent discovery"
    return 1
  fi

  _ar_log "info" "Running inline agent discovery from $agents_dir"

  local agents_json="[]"
  local def_file

  for def_file in "${agents_dir}"/*.yaml; do
    [[ -f "$def_file" ]] || continue

    local kind
    kind="$(yq '.kind' "$def_file" 2>/dev/null || echo "")"
    [[ "$kind" == "AgentDefinition" ]] || continue

    local agent_json
    agent_json="$(yq -o=json '.' "$def_file" 2>/dev/null || echo "")"
    [[ -n "$agent_json" ]] || continue

    agents_json="$(echo "$agents_json" | jq --argjson a "$agent_json" '. + [$a]')"
  done

  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  AGENT_REGISTRY_JSON="$(jq -n \
    --argjson agents "$agents_json" \
    --arg ts "$ts" \
    '{"generatedAt": $ts, "agents": $agents}')"

  local agent_count
  agent_count="$(echo "$AGENT_REGISTRY_JSON" | jq '.agents | length')"
  _ar_log "info" "Inline discovery complete: $agent_count agents found"

  _sync_agent_instances
  return 0
}

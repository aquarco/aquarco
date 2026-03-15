#!/usr/bin/env bash
# supervisor/scripts/aifishtank-status.sh
# AI Fishtank status reporting utility.
#
# Displays supervisor status, agent registry summary, task queue statistics,
# active agent instances, and recent tasks. Supports --json for machine-readable
# output consumed by dashboards and health monitors.
#
# Usage:
#   ./aifishtank-status.sh [options]
#
# Options:
#   --json         Output as a JSON document instead of human-readable text
#   --config FILE  Path to supervisor.yaml
#   --help         Show this help text

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPERVISOR_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Source libraries ──────────────────────────────────────────────────────────

# shellcheck source=../lib/config.sh
source "${SUPERVISOR_ROOT}/lib/config.sh"

# ── Globals ───────────────────────────────────────────────────────────────────

OUTPUT_JSON=false
SUPERVISOR_PID_FILE="${SUPERVISOR_PID_FILE:-/var/run/aifishtank/supervisor.pid}"

# ── Argument parsing ──────────────────────────────────────────────────────────

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --json         Output as JSON (for scripts and dashboards)
  --config FILE  Path to supervisor.yaml
  --help         Show this help text
EOF
  exit 0
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)
        OUTPUT_JSON=true
        shift
        ;;
      --config)
        export SUPERVISOR_CONFIG_FILE="$2"
        shift 2
        ;;
      --help|-h)
        usage
        ;;
      *)
        echo "Unknown argument: $1" >&2
        usage
        ;;
    esac
  done
}

# ── Private: _psql ────────────────────────────────────────────────────────────

_psql() {
  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?DATABASE_URL is not set}" \
    "$@"
}

# ── Data gathering functions ──────────────────────────────────────────────────

_get_supervisor_status() {
  local pid="" uptime="" status="stopped"

  if [[ -f "$SUPERVISOR_PID_FILE" ]]; then
    pid="$(< "$SUPERVISOR_PID_FILE")"
    if kill -0 "$pid" 2>/dev/null; then
      status="running"
      # Uptime from /proc (Linux only).
      if [[ -f "/proc/${pid}/stat" ]]; then
        local start_ticks hz elapsed_seconds
        start_ticks="$(awk '{print $22}' "/proc/${pid}/stat" 2>/dev/null || echo "")"
        hz="$(getconf CLK_TCK 2>/dev/null || echo "100")"
        if [[ -n "$start_ticks" ]]; then
          local uptime_total_seconds
          uptime_total_seconds="$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo "0")"
          elapsed_seconds=$(( uptime_total_seconds - (start_ticks / hz) ))
          local hours=$(( elapsed_seconds / 3600 ))
          local minutes=$(( (elapsed_seconds % 3600) / 60 ))
          local seconds=$(( elapsed_seconds % 60 ))
          uptime="$(printf '%dh %dm %ds' "$hours" "$minutes" "$seconds")"
        fi
      fi
    else
      status="stale-pid"
    fi
  fi

  if [[ "$OUTPUT_JSON" == "true" ]]; then
    jq -n \
      --arg status "$status" \
      --arg pid "${pid:-}" \
      --arg uptime "${uptime:-unknown}" \
      '{"status": $status, "pid": $pid, "uptime": $uptime}'
  else
    echo "Supervisor Status"
    printf "  Status : %s\n" "$status"
    printf "  PID    : %s\n" "${pid:-n/a}"
    printf "  Uptime : %s\n" "${uptime:-unknown}"
  fi
}

_get_registry_summary() {
  local agents_dir="${CFG_AGENTS_DIR:-/home/agent/ai-fishtank/agents/definitions}"
  local registry_file="${SUPERVISOR_ROOT}/../agents/schemas/agent-registry.json"

  local agent_count=0
  local categories_seen=()

  if [[ -f "$registry_file" ]]; then
    agent_count="$(jq '.agents | length' "$registry_file" 2>/dev/null || echo "0")"
    mapfile -t categories_seen < <(jq -r '.agents[].spec.categories[]' "$registry_file" 2>/dev/null | sort -u)
  elif [[ -d "$agents_dir" ]]; then
    agent_count="$(ls "${agents_dir}"/*.yaml 2>/dev/null | wc -l | tr -d ' ')"
  fi

  if [[ "$OUTPUT_JSON" == "true" ]]; then
    jq -n \
      --argjson count "$agent_count" \
      --argjson cats "$(printf '%s\n' "${categories_seen[@]:-}" | jq -R '.' | jq -s '.' 2>/dev/null || echo '[]')" \
      '{"agent_count": $count, "categories": $cats}'
  else
    echo ""
    echo "Agent Registry"
    printf "  Agents     : %d\n" "$agent_count"
    printf "  Categories : %s\n" "${categories_seen[*]:-none}"
  fi
}

_get_task_queue_stats() {
  local sql
  sql="$(cat <<'SQL'
SET search_path TO aifishtank, public;
SELECT
  status,
  COUNT(*) AS cnt
FROM tasks
GROUP BY status
ORDER BY status;
SQL
)"

  local stats
  stats="$(_psql -c "$sql" 2>/dev/null || echo "")"

  if [[ "$OUTPUT_JSON" == "true" ]]; then
    local json_obj="{}"
    while IFS='|' read -r status cnt; do
      status="$(echo "$status" | tr -d '[:space:]')"
      cnt="$(echo "$cnt" | tr -d '[:space:]')"
      [[ -n "$status" ]] || continue
      json_obj="$(echo "$json_obj" | jq --arg s "$status" --argjson c "${cnt:-0}" \
        '. + {($s): $c}')"
    done <<< "$stats"
    echo "$json_obj"
  else
    echo ""
    echo "Task Queue Stats"
    if [[ -z "$stats" ]]; then
      echo "  (no data)"
    else
      while IFS='|' read -r status cnt; do
        status="$(echo "$status" | tr -d '[:space:]')"
        cnt="$(echo "$cnt" | tr -d '[:space:]')"
        [[ -n "$status" ]] || continue
        printf "  %-12s : %s\n" "$status" "$cnt"
      done <<< "$stats"
    fi
  fi
}

_get_active_instances() {
  local sql
  sql="$(cat <<'SQL'
SET search_path TO aifishtank, public;
SELECT
  agent_name,
  active_count,
  total_executions,
  to_char(last_execution_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS last_execution_at
FROM agent_instances
WHERE active_count > 0
   OR last_execution_at IS NOT NULL
ORDER BY active_count DESC, last_execution_at DESC NULLS LAST
LIMIT 20;
SQL
)"

  local rows
  rows="$(_psql -c "$sql" 2>/dev/null || echo "")"

  if [[ "$OUTPUT_JSON" == "true" ]]; then
    local json_arr="[]"
    while IFS='|' read -r agent active total last_exec; do
      agent="$(echo "$agent" | tr -d '[:space:]')"
      active="$(echo "$active" | tr -d '[:space:]')"
      total="$(echo "$total" | tr -d '[:space:]')"
      last_exec="$(echo "$last_exec" | tr -d '[:space:]')"
      [[ -n "$agent" ]] || continue
      json_arr="$(echo "$json_arr" | jq \
        --arg a "$agent" \
        --argjson ac "${active:-0}" \
        --argjson te "${total:-0}" \
        --arg le "${last_exec:-}" \
        '. + [{"agent": $a, "active": $ac, "total_executions": $te, "last_execution_at": $le}]')"
    done <<< "$rows"
    echo "$json_arr"
  else
    echo ""
    echo "Active Agent Instances"
    if [[ -z "$rows" ]]; then
      echo "  (none)"
    else
      printf "  %-30s  %-6s  %-10s  %s\n" "AGENT" "ACTIVE" "TOTAL" "LAST EXECUTION"
      while IFS='|' read -r agent active total last_exec; do
        agent="$(echo "$agent" | tr -d '[:space:]')"
        [[ -n "$agent" ]] || continue
        printf "  %-30s  %-6s  %-10s  %s\n" \
          "$agent" \
          "$(echo "$active" | tr -d '[:space:]')" \
          "$(echo "$total" | tr -d '[:space:]')" \
          "$(echo "$last_exec" | tr -d '[:space:]')"
      done <<< "$rows"
    fi
  fi
}

_get_recent_tasks() {
  local sql
  sql="$(cat <<'SQL'
SET search_path TO aifishtank, public;
SELECT
  id,
  title,
  category,
  status,
  pipeline,
  repository,
  to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS created_at,
  to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS updated_at
FROM tasks
ORDER BY created_at DESC
LIMIT 10;
SQL
)"

  local rows
  rows="$(_psql -c "$sql" 2>/dev/null || echo "")"

  if [[ "$OUTPUT_JSON" == "true" ]]; then
    local json_arr="[]"
    while IFS='|' read -r id title category status pipeline repository created_at updated_at; do
      id="$(echo "$id" | tr -d '[:space:]')"
      [[ -n "$id" ]] || continue
      json_arr="$(echo "$json_arr" | jq \
        --arg id "$id" \
        --arg title "$(echo "$title" | xargs)" \
        --arg cat "$(echo "$category" | tr -d '[:space:]')" \
        --arg status "$(echo "$status" | tr -d '[:space:]')" \
        --arg pipeline "$(echo "$pipeline" | tr -d '[:space:]')" \
        --arg repo "$(echo "$repository" | tr -d '[:space:]')" \
        --arg created "$(echo "$created_at" | tr -d '[:space:]')" \
        --arg updated "$(echo "$updated_at" | tr -d '[:space:]')" \
        '. + [{"id":$id,"title":$title,"category":$cat,"status":$status,"pipeline":$pipeline,"repository":$repo,"created_at":$created,"updated_at":$updated}]')"
    done <<< "$rows"
    echo "$json_arr"
  else
    echo ""
    echo "Recent Tasks (last 10)"
    if [[ -z "$rows" ]]; then
      echo "  (none)"
    else
      printf "  %-40s  %-14s  %-10s  %-20s  %s\n" "ID" "STATUS" "CATEGORY" "PIPELINE" "CREATED"
      while IFS='|' read -r id title category status pipeline repository created_at updated_at; do
        id="$(echo "$id" | tr -d '[:space:]')"
        [[ -n "$id" ]] || continue
        printf "  %-40s  %-14s  %-10s  %-20s  %s\n" \
          "$id" \
          "$(echo "$status" | tr -d '[:space:]')" \
          "$(echo "$category" | tr -d '[:space:]')" \
          "$(echo "$pipeline" | tr -d '[:space:]')" \
          "$(echo "$created_at" | tr -d '[:space:]')"
      done <<< "$rows"
    fi
  fi
}

# ── Main ──────────────────────────────────────────────────────────────────────

main() {
  parse_args "$@"

  # Load config (best-effort; status command should not fail hard on missing config).
  load_config 2>/dev/null || true

  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  if [[ "$OUTPUT_JSON" == "true" ]]; then
    # Build full JSON status document.
    local supervisor_status registry_summary task_stats active_instances recent_tasks

    supervisor_status="$(_get_supervisor_status)"
    registry_summary="$(_get_registry_summary)"
    task_stats="$(_get_task_queue_stats)"
    active_instances="$(_get_active_instances)"
    recent_tasks="$(_get_recent_tasks)"

    jq -n \
      --arg ts "$ts" \
      --argjson supervisor "$supervisor_status" \
      --argjson registry "$registry_summary" \
      --argjson tasks "$task_stats" \
      --argjson instances "$active_instances" \
      --argjson recent "$recent_tasks" \
      '{
        generated_at: $ts,
        supervisor: $supervisor,
        registry: $registry,
        task_queue: $tasks,
        agent_instances: $instances,
        recent_tasks: $recent
      }'
  else
    echo "=============================="
    echo " AI Fishtank Supervisor Status"
    printf " %s\n" "$ts"
    echo "=============================="

    _get_supervisor_status
    _get_registry_summary
    _get_task_queue_stats
    _get_active_instances
    _get_recent_tasks

    echo ""
    echo "=============================="
  fi
}

main "$@"

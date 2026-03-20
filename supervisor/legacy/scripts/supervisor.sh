#!/usr/bin/env bash
# supervisor/scripts/supervisor.sh
# Aquarco Agent Supervisor — main control loop.
#
# Responsibilities:
#   - Load configuration and agent registry
#   - Run enabled pollers at their configured intervals
#   - Dispatch pending tasks from the queue to pipeline executor
#   - Detect and handle timed-out tasks
#   - Reload config on SIGHUP
#   - Shut down cleanly on SIGTERM / SIGINT
#
# Usage:
#   ./supervisor.sh [--config /path/to/supervisor.yaml]

set -euo pipefail

# Feature flag: if SUPERVISOR_USE_PYTHON=1, delegate to the Python implementation.
if [ "${SUPERVISOR_USE_PYTHON:-0}" = "1" ]; then
    exec aquarco-supervisor "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPERVISOR_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Source libraries ──────────────────────────────────────────────────────────

# shellcheck source=../lib/utils.sh
source "${SUPERVISOR_ROOT}/lib/utils.sh"
# shellcheck source=../lib/config.sh
source "${SUPERVISOR_ROOT}/lib/config.sh"
# shellcheck source=../lib/task-queue.sh
source "${SUPERVISOR_ROOT}/lib/task-queue.sh"
# shellcheck source=../lib/agent-registry.sh
source "${SUPERVISOR_ROOT}/lib/agent-registry.sh"
# shellcheck source=../lib/pipeline-executor.sh
source "${SUPERVISOR_ROOT}/lib/pipeline-executor.sh"

# ── Source pollers ────────────────────────────────────────────────────────────

# shellcheck source=../pollers/github-tasks.sh
source "${SUPERVISOR_ROOT}/pollers/github-tasks.sh"
# shellcheck source=../pollers/github-source.sh
source "${SUPERVISOR_ROOT}/pollers/github-source.sh"
# shellcheck source=../pollers/external-triggers.sh
source "${SUPERVISOR_ROOT}/pollers/external-triggers.sh"

# ── Source workers ────────────────────────────────────────────────────────────

# shellcheck source=clone-worker.sh
source "${SUPERVISOR_ROOT}/scripts/clone-worker.sh"
# shellcheck source=pull-worker.sh
source "${SUPERVISOR_ROOT}/scripts/pull-worker.sh"

# ── Constants ─────────────────────────────────────────────────────────────────

SUPERVISOR_PID=$$
SUPERVISOR_START_TIME="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
SUPERVISOR_SHUTDOWN=false
SUPERVISOR_CONFIG_RELOAD_REQUESTED=false

# ── Logging ───────────────────────────────────────────────────────────────────

log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  local msg
  msg="$(printf '{"ts":"%s","level":"%s","component":"supervisor","pid":%d,"msg":"%s"}\n' \
    "$ts" "$level" "$SUPERVISOR_PID" "$*")"

  # Write to stderr (systemd captures it) and optionally to log file.
  echo "$msg" >&2

  if [[ -n "${CFG_LOG_FILE:-}" ]] && [[ -d "$(dirname "$CFG_LOG_FILE")" ]]; then
    echo "$msg" >> "$CFG_LOG_FILE" || true
  fi
}

# ── Argument parsing ──────────────────────────────────────────────────────────

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --config FILE   Path to supervisor.yaml (default: auto-detected)
  --help          Show this help text

Environment:
  SUPERVISOR_CONFIG_FILE   Override config file path
EOF
  exit 0
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
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

# ── Signal handlers ───────────────────────────────────────────────────────────

handle_sighup() {
  log "info" "SIGHUP received; scheduling config reload"
  SUPERVISOR_CONFIG_RELOAD_REQUESTED=true
}

handle_sigterm() {
  log "info" "SIGTERM received; initiating graceful shutdown"
  SUPERVISOR_SHUTDOWN=true
}

handle_sigint() {
  log "info" "SIGINT received; initiating graceful shutdown"
  SUPERVISOR_SHUTDOWN=true
}

# ── Poller interval tracking ──────────────────────────────────────────────────

# Associative array: poller_name -> unix timestamp of last run
declare -A POLLER_LAST_RUN

_should_run_poller() {
  local poller_name="$1"
  local interval_seconds="$2"
  local now
  now="$(date +%s)"
  local last_run="${POLLER_LAST_RUN[$poller_name]:-0}"
  local elapsed=$(( now - last_run ))
  [[ "$elapsed" -ge "$interval_seconds" ]]
}

_mark_poller_ran() {
  local poller_name="$1"
  POLLER_LAST_RUN["$poller_name"]="$(date +%s)"
}

# ── Agent discovery ───────────────────────────────────────────────────────────

run_agent_discovery() {
  local discover_script="${SUPERVISOR_ROOT}/../agents/schemas/scripts/discover-agents.sh"

  if [[ -x "$discover_script" ]]; then
    log "info" "Running agent discovery script"
    if "$discover_script" 2>/dev/null; then
      log "info" "Agent discovery complete"
    else
      log "warn" "Agent discovery script returned non-zero; loading registry inline"
    fi
  else
    log "info" "No discover-agents.sh found; registry will be built inline"
  fi

  if ! load_registry; then
    log "error" "Failed to load agent registry; supervisor cannot dispatch tasks"
    return 1
  fi

  return 0
}

# ── Poller dispatch ───────────────────────────────────────────────────────────

run_pollers() {
  local poller_name poller_type interval enabled

  # github-tasks poller
  poller_name="github-tasks"
  enabled="$(is_poller_enabled "$poller_name" 2>/dev/null || echo "false")"
  interval="$(get_poller_interval "$poller_name" 2>/dev/null || echo "60")"

  if [[ "$enabled" == "true" ]] && _should_run_poller "$poller_name" "$interval"; then
    log "info" "Running poller: $poller_name"
    ( poll_github_tasks 2>&1 ) || log "warn" "Poller $poller_name returned non-zero"
    _mark_poller_ran "$poller_name"
  fi

  # github-source poller
  poller_name="github-source"
  enabled="$(is_poller_enabled "$poller_name" 2>/dev/null || echo "false")"
  interval="$(get_poller_interval "$poller_name" 2>/dev/null || echo "30")"

  if [[ "$enabled" == "true" ]] && _should_run_poller "$poller_name" "$interval"; then
    log "info" "Running poller: $poller_name"
    ( poll_github_source 2>&1 ) || log "warn" "Poller $poller_name returned non-zero"
    _mark_poller_ran "$poller_name"
  fi

  # external-triggers poller
  poller_name="external-triggers"
  enabled="$(is_poller_enabled "$poller_name" 2>/dev/null || echo "false")"
  interval="$(get_poller_interval "$poller_name" 2>/dev/null || echo "10")"

  if [[ "$enabled" == "true" ]] && _should_run_poller "$poller_name" "$interval"; then
    log "info" "Running poller: $poller_name"
    ( poll_external_triggers 2>&1 ) || log "warn" "Poller $poller_name returned non-zero"
    _mark_poller_ran "$poller_name"
  fi
}

# ── Task dispatch ─────────────────────────────────────────────────────────────

dispatch_pending_tasks() {
  local max_concurrent="${CFG_MAX_CONCURRENT_AGENTS:-3}"

  # Count currently executing tasks across all agents.
  local active_count_sql="SELECT COALESCE(SUM(active_count), 0) FROM agent_instances;"

  local current_active
  current_active="$(psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?}" -c "SET search_path TO aquarco, public;" \
    -c "$active_count_sql" 2>/dev/null | tail -1 | tr -d '[:space:]' || echo "0")"
  current_active="${current_active:-0}"

  if [[ "$current_active" -ge "$max_concurrent" ]]; then
    log "info" "At capacity ($current_active/$max_concurrent active agents); skipping dispatch"
    return 0
  fi

  local available_slots=$(( max_concurrent - current_active ))
  local dispatched=0

  while [[ "$dispatched" -lt "$available_slots" ]]; do
    local task_row
    if ! task_row="$(get_next_task 2>/dev/null)"; then
      # Queue is empty.
      break
    fi

    # task_row: id<TAB>title<TAB>category<TAB>pipeline<TAB>repository<TAB>source<TAB>source_ref
    local task_id task_title task_category task_pipeline task_repository
    IFS=$'\x1f' read -r task_id task_title task_category task_pipeline task_repository _ _ <<< "$task_row"

    if [[ -z "$task_id" ]]; then
      break
    fi

    log "info" "Dispatching task=$task_id category=$task_category pipeline=$task_pipeline"

    # Run the pipeline in a background subshell so the main loop stays responsive.
    # Redirect stderr to the log file so pipeline-executor logs are captured.
    (
      set -euo pipefail

      # Re-source libs in the subshell (environment is inherited but functions are not).
      source "${SUPERVISOR_ROOT}/lib/config.sh"
      source "${SUPERVISOR_ROOT}/lib/task-queue.sh"
      source "${SUPERVISOR_ROOT}/lib/agent-registry.sh"
      source "${SUPERVISOR_ROOT}/lib/pipeline-executor.sh"

      load_registry || { log "error" "Failed to load agent registry in subshell"; exit 1; }

      assign_agent "$task_id" "pending-assignment"
      update_task_status "$task_id" "executing"

      local initial_context
      initial_context="$(get_task "$task_id" | jq '.initial_context // {}' 2>/dev/null || echo '{}')"

      if ! execute_pipeline "$task_pipeline" "$task_id" "$initial_context"; then
        log "error" "Pipeline failed for task=$task_id"
        exit 1
      fi
    ) >> "${CFG_LOG_FILE:-/dev/null}" 2>&1 &

    (( dispatched++ )) || true
    log "info" "Background pipeline started for task=$task_id (PID=$!)"
  done
}

# ── Timeout detection ─────────────────────────────────────────────────────────

check_timed_out_tasks() {
  # Default: tasks executing for more than 90 minutes are considered timed out.
  local timeout_minutes=90

  local timed_out_ids
  mapfile -t timed_out_ids < <(get_timed_out_tasks "$timeout_minutes" 2>/dev/null || true)

  local task_id
  for task_id in "${timed_out_ids[@]}"; do
    [[ -n "$task_id" ]] || continue
    log "warn" "Task timeout detected: id=$task_id"
    update_task_status "$task_id" "timeout"
    fail_task "$task_id" "Task exceeded timeout of ${timeout_minutes} minutes"
  done
}

# ── Health reporting ──────────────────────────────────────────────────────────

_health_last_report=0

maybe_report_health() {
  local interval_minutes="${CFG_HEALTH_REPORT_INTERVAL_MINUTES:-30}"
  local interval_seconds=$(( interval_minutes * 60 ))
  local now
  now="$(date +%s)"
  local elapsed=$(( now - _health_last_report ))

  if [[ "$elapsed" -lt "$interval_seconds" ]]; then
    return 0
  fi

  if [[ "${CFG_HEALTH_ENABLED:-true}" != "true" ]]; then
    return 0
  fi

  log "info" "Generating health report"
  _health_last_report="$now"

  # Gather stats.
  local stats_sql
  stats_sql="$(cat <<'SQL'
SET search_path TO aquarco, public;
SELECT
  status,
  COUNT(*) AS cnt
FROM tasks
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY status;
SQL
)"

  local stats
  stats="$(psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?}" -c "$stats_sql" 2>/dev/null || echo "unavailable")"

  local uptime_seconds
  local start_epoch
  start_epoch="$(date -d "$SUPERVISOR_START_TIME" +%s 2>/dev/null || \
                 date -j -f "%Y-%m-%dT%H:%M:%SZ" "$SUPERVISOR_START_TIME" +%s 2>/dev/null || \
                 echo "$now")"
  uptime_seconds=$(( now - start_epoch ))

  local report_body
  report_body="$(printf \
    "## Supervisor Health Report\n\n**Uptime**: %d minutes\n**PID**: %d\n\n### Task Stats (last 24h)\n\`\`\`\n%s\n\`\`\`\n" \
    "$(( uptime_seconds / 60 ))" "$SUPERVISOR_PID" "$stats")"

  # Post to GitHub issue if configured.
  local issue_number="${CFG_HEALTH_ISSUE_NUMBER:-1}"
  local repo_name
  repo_name="$(get_repositories 2>/dev/null | head -1 || echo "")"
  if [[ -n "$repo_name" ]]; then
    local repo_config
    repo_config="$(get_repository_config "$repo_name" 2>/dev/null || echo "null")"
    local repo_url
    repo_url="$(echo "$repo_config" | jq -r '.url // ""')"
    local repo_slug
    repo_slug="$(_url_to_slug "$repo_url" 2>/dev/null || echo "")"

    if [[ -n "$repo_slug" ]] && command -v gh &>/dev/null; then
      gh issue comment "$issue_number" \
        --repo "$repo_slug" \
        --body "$report_body" 2>/dev/null || \
        log "warn" "Failed to post health report to GitHub issue #$issue_number"
    fi
  fi

  log "info" "Health report complete"
}

# _url_to_slug is provided by supervisor/lib/utils.sh (sourced at top of file).

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
  parse_args "$@"

  # Set up signal handlers.
  trap handle_sighup  SIGHUP
  trap handle_sigterm SIGTERM
  trap handle_sigint  SIGINT

  log "info" "Aquarco Supervisor starting (PID=$SUPERVISOR_PID)"

  # Load configuration.
  if ! load_config; then
    log "error" "Failed to load configuration; exiting"
    exit 1
  fi

  log "info" "Configuration loaded from $SUPERVISOR_CONFIG_FILE"
  log "info" "Database URL: ${CFG_DATABASE_URL:-<not set>}"
  log "info" "Max concurrent agents: ${CFG_MAX_CONCURRENT_AGENTS:-3}"

  # Run agent discovery.
  if ! run_agent_discovery; then
    log "error" "Agent discovery failed; exiting"
    exit 1
  fi

  log "info" "Supervisor ready — entering main loop"

  # ── Main loop ───────────────────────────────────────────────────────────────
  while [[ "$SUPERVISOR_SHUTDOWN" == "false" ]]; do

    # Handle pending config reload (SIGHUP).
    if [[ "$SUPERVISOR_CONFIG_RELOAD_REQUESTED" == "true" ]]; then
      SUPERVISOR_CONFIG_RELOAD_REQUESTED=false
      log "info" "Applying config reload"
      reload_config || log "warn" "Config reload failed; keeping existing config"
      # Re-discover agents after config reload.
      run_agent_discovery || log "warn" "Agent re-discovery failed after config reload"
    fi

    # Clone any pending repositories.
    clone_pending_repos || log "warn" "clone_pending_repos failed"

    # Pull updates for ready repositories (every 30s).
    if _should_run_poller "repo-pull" 30; then
      pull_ready_repos || log "warn" "pull_ready_repos failed"
      _mark_poller_ran "repo-pull"
    fi

    # Run pollers (each respects its own interval).
    run_pollers

    # Dispatch tasks from queue.
    dispatch_pending_tasks

    # Check for timed-out tasks.
    check_timed_out_tasks

    # Periodic health report.
    maybe_report_health

    # Sleep for the cooldown period.
    local cooldown="${CFG_COOLDOWN_SECONDS:-5}"
    sleep "$cooldown"
  done

  log "info" "Supervisor shutting down gracefully"

  # Wait for any background pipeline processes.
  wait

  log "info" "Supervisor stopped"
}

main "$@"

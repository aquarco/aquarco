#!/usr/bin/env bash
# supervisor/lib/config.sh
# Configuration loading library for the AI Fishtank supervisor.
#
# Parses supervisor.yaml using yq and exports configuration values as
# shell variables. Supports runtime reload via SIGHUP.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/config.sh"
#   load_config /path/to/supervisor.yaml
#   get_config_value ".spec.database.url"

set -euo pipefail

# ── Globals ──────────────────────────────────────────────────────────────────

SUPERVISOR_CONFIG_FILE="${SUPERVISOR_CONFIG_FILE:-/home/agent/ai-fishtank/supervisor/config/supervisor.yaml}"
SUPERVISOR_CONFIG_LOADED=false

# ── Logging shim (safe to call before log lib is loaded) ─────────────────────

_cfg_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '{"ts":"%s","level":"%s","component":"config","msg":"%s"}\n' \
    "$ts" "$level" "$*" >&2
}

# ── Public: load_config ───────────────────────────────────────────────────────
# Parse the supervisor YAML file and export all commonly-used values as shell
# variables. Exported names use the prefix CFG_ for easy grepping.
#
# Usage: load_config [config_file]
load_config() {
  local config_file="${1:-$SUPERVISOR_CONFIG_FILE}"

  if [[ ! -f "$config_file" ]]; then
    _cfg_log "error" "Config file not found: $config_file"
    return 1
  fi

  if ! command -v yq &>/dev/null; then
    _cfg_log "error" "yq is required but not found in PATH"
    return 1
  fi

  _cfg_log "info" "Loading config from $config_file"

  # Validate the apiVersion field before proceeding.
  local api_version
  api_version="$(yq '.apiVersion' "$config_file" 2>/dev/null || true)"
  if [[ "$api_version" != "aifishtank.supervisor/v1" ]]; then
    _cfg_log "error" "Invalid apiVersion '$api_version'; expected aifishtank.supervisor/v1"
    return 1
  fi

  # ── Core paths ──────────────────────────────────────────────────────────────
  export CFG_WORKDIR
  CFG_WORKDIR="$(yq '.spec.workdir' "$config_file")"

  export CFG_AGENTS_DIR
  CFG_AGENTS_DIR="$(yq '.spec.agentsDir' "$config_file")"

  export CFG_PROMPTS_DIR
  CFG_PROMPTS_DIR="$(yq '.spec.promptsDir' "$config_file")"

  # ── Database ─────────────────────────────────────────────────────────────────
  export CFG_DATABASE_URL
  CFG_DATABASE_URL="$(yq '.spec.database.url' "$config_file")"

  export CFG_DATABASE_MAX_CONNECTIONS
  CFG_DATABASE_MAX_CONNECTIONS="$(yq '.spec.database.maxConnections // 5' "$config_file")"

  # DATABASE_URL is the conventional name used by psql and most DB clients.
  export DATABASE_URL="$CFG_DATABASE_URL"

  # ── Logging ──────────────────────────────────────────────────────────────────
  export CFG_LOG_LEVEL
  CFG_LOG_LEVEL="$(yq '.spec.logging.level // "info"' "$config_file")"

  export CFG_LOG_FILE
  CFG_LOG_FILE="$(yq '.spec.logging.file // "/var/log/aifishtank/supervisor.log"' "$config_file")"

  export CFG_LOG_FORMAT
  CFG_LOG_FORMAT="$(yq '.spec.logging.format // "json"' "$config_file")"

  # ── Global limits ─────────────────────────────────────────────────────────
  export CFG_MAX_CONCURRENT_AGENTS
  CFG_MAX_CONCURRENT_AGENTS="$(yq '.spec.globalLimits.maxConcurrentAgents // 3' "$config_file")"

  export CFG_MAX_TOKENS_PER_HOUR
  CFG_MAX_TOKENS_PER_HOUR="$(yq '.spec.globalLimits.maxTokensPerHour // 1000000' "$config_file")"

  export CFG_COOLDOWN_SECONDS
  CFG_COOLDOWN_SECONDS="$(yq '.spec.globalLimits.cooldownBetweenTasksSeconds // 5' "$config_file")"

  export CFG_MAX_RETRIES
  CFG_MAX_RETRIES="$(yq '.spec.globalLimits.maxRetries // 3' "$config_file")"

  export CFG_RETRY_DELAY_SECONDS
  CFG_RETRY_DELAY_SECONDS="$(yq '.spec.globalLimits.retryDelaySeconds // 60' "$config_file")"

  # ── Secrets ──────────────────────────────────────────────────────────────────
  export CFG_GITHUB_TOKEN_FILE
  CFG_GITHUB_TOKEN_FILE="$(yq '.spec.secrets.githubTokenFile // "/home/agent/.github-token"' "$config_file")"

  export CFG_ANTHROPIC_KEY_FILE
  CFG_ANTHROPIC_KEY_FILE="$(yq '.spec.secrets.anthropicKeyFile // "/home/agent/.anthropic-key"' "$config_file")"

  # Load secrets into environment if files exist.
  if [[ -f "$CFG_GITHUB_TOKEN_FILE" ]]; then
    export GITHUB_TOKEN
    GITHUB_TOKEN="$(< "$CFG_GITHUB_TOKEN_FILE")"
    export GH_TOKEN="$GITHUB_TOKEN"
  fi

  if [[ -f "$CFG_ANTHROPIC_KEY_FILE" ]]; then
    export ANTHROPIC_API_KEY
    ANTHROPIC_API_KEY="$(< "$CFG_ANTHROPIC_KEY_FILE")"
  fi

  # ── Health reporting ─────────────────────────────────────────────────────────
  export CFG_HEALTH_ENABLED
  CFG_HEALTH_ENABLED="$(yq '.spec.health.enabled // "true"' "$config_file")"

  export CFG_HEALTH_REPORT_INTERVAL_MINUTES
  CFG_HEALTH_REPORT_INTERVAL_MINUTES="$(yq '.spec.health.reportIntervalMinutes // 30' "$config_file")"

  export CFG_HEALTH_ISSUE_NUMBER
  CFG_HEALTH_ISSUE_NUMBER="$(yq '.spec.health.issueNumber // 1' "$config_file")"

  # ── External triggers dir ────────────────────────────────────────────────────
  local triggers_poller_count
  triggers_poller_count="$(yq '[.spec.pollers[] | select(.type == "file-watch")] | length' "$config_file")"
  if [[ "$triggers_poller_count" -gt 0 ]]; then
    export CFG_TRIGGERS_WATCH_DIR
    CFG_TRIGGERS_WATCH_DIR="$(yq '.spec.pollers[] | select(.type == "file-watch") | .config.watchDir' "$config_file" | head -1)"

    export CFG_TRIGGERS_PROCESSED_DIR
    CFG_TRIGGERS_PROCESSED_DIR="$(yq '.spec.pollers[] | select(.type == "file-watch") | .config.processedDir' "$config_file" | head -1)"
  fi

  # Record which config file is loaded and when.
  export SUPERVISOR_CONFIG_FILE="$config_file"
  SUPERVISOR_CONFIG_LOADED=true

  _cfg_log "info" "Config loaded successfully"
  return 0
}

# ── Public: reload_config ─────────────────────────────────────────────────────
# Re-read the configuration file. Called on SIGHUP.
# If validateBeforeReload is true in config, validates the file before
# applying the new values.
#
# Usage: reload_config
reload_config() {
  _cfg_log "info" "Reloading config (SIGHUP received)"

  local validate_before_reload
  validate_before_reload="$(yq '.spec.configReload.validateBeforeReload // "true"' "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "true")"

  if [[ "$validate_before_reload" == "true" ]]; then
    if ! _validate_config "$SUPERVISOR_CONFIG_FILE"; then
      _cfg_log "error" "Config validation failed; keeping existing configuration"
      return 1
    fi
  fi

  # Unset all CFG_ exports so stale values do not survive a reload where a
  # key has been removed from the file.
  # shellcheck disable=SC2046
  unset $(compgen -v CFG_ 2>/dev/null || true)

  load_config "$SUPERVISOR_CONFIG_FILE"
  _cfg_log "info" "Config reload complete"
}

# ── Public: get_config_value ──────────────────────────────────────────────────
# Retrieve a single value from the loaded config file using a yq path.
# Returns the raw string value from yq (null if not found).
#
# Usage: get_config_value ".spec.database.url"
get_config_value() {
  local path="$1"

  if [[ -z "$path" ]]; then
    _cfg_log "error" "get_config_value: path argument is required"
    return 1
  fi

  if [[ ! -f "$SUPERVISOR_CONFIG_FILE" ]]; then
    _cfg_log "error" "get_config_value: config file not loaded (call load_config first)"
    return 1
  fi

  yq "$path" "$SUPERVISOR_CONFIG_FILE"
}

# ── Public: get_poller_config ─────────────────────────────────────────────────
# Return the config block for a named poller as a JSON string.
#
# Usage: get_poller_config "github-tasks"
get_poller_config() {
  local poller_name="$1"

  yq -o=json ".spec.pollers[] | select(.name == \"$poller_name\")" \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "null"
}

# ── Public: get_poller_interval ───────────────────────────────────────────────
# Return the intervalSeconds for the named poller (default: 60).
#
# Usage: get_poller_interval "github-tasks"
get_poller_interval() {
  local poller_name="$1"
  yq ".spec.pollers[] | select(.name == \"$poller_name\") | .intervalSeconds // 60" \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "60"
}

# ── Public: is_poller_enabled ─────────────────────────────────────────────────
# Return "true" or "false" for whether the named poller is enabled.
#
# Usage: is_poller_enabled "github-tasks"
is_poller_enabled() {
  local poller_name="$1"
  yq ".spec.pollers[] | select(.name == \"$poller_name\") | .enabled // true" \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "false"
}

# ── Public: get_repositories ─────────────────────────────────────────────────
# Print repository names, one per line.
#
# Usage: get_repositories
get_repositories() {
  yq '.spec.repositories[].name' "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || true
}

# ── Public: get_repository_config ────────────────────────────────────────────
# Return JSON config block for a named repository.
#
# Usage: get_repository_config "example-app"
get_repository_config() {
  local repo_name="$1"
  yq -o=json ".spec.repositories[] | select(.name == \"$repo_name\")" \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "null"
}

# ── Private: _validate_config ─────────────────────────────────────────────────
# Perform basic structural validation of the config file.
# Returns 0 if valid, 1 otherwise.
_validate_config() {
  local config_file="$1"

  # Must be parseable YAML.
  if ! yq '.' "$config_file" &>/dev/null; then
    _cfg_log "error" "Config file is not valid YAML: $config_file"
    return 1
  fi

  # Must have the correct apiVersion.
  local api_version
  api_version="$(yq '.apiVersion' "$config_file" 2>/dev/null || echo "")"
  if [[ "$api_version" != "aifishtank.supervisor/v1" ]]; then
    _cfg_log "error" "Config apiVersion '$api_version' is invalid"
    return 1
  fi

  # Must have a database URL.
  local db_url
  db_url="$(yq '.spec.database.url' "$config_file" 2>/dev/null || echo "")"
  if [[ -z "$db_url" || "$db_url" == "null" ]]; then
    _cfg_log "error" "Config is missing spec.database.url"
    return 1
  fi

  return 0
}

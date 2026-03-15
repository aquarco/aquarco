#!/usr/bin/env bash
# supervisor/scripts/repo-manager.sh
#
# Manages Docker Compose stacks for target repositories.
#
# Each repository gets its own docker-compose.yml and .env file generated
# from the templates in supervisor/templates/.  Ports are read from
# supervisor.yaml (via config.sh) or auto-allocated from a defined range.
#
# Usage:
#   repo-manager.sh <command> [args...]
#
# Commands:
#   setup    <repo_name> <clone_dir> <ports_json>  Copy templates + substitute vars
#   start    <repo_name> <clone_dir>               docker compose up -d
#   stop     <repo_name> <clone_dir>               docker compose down
#   restart  <repo_name> <clone_dir> [service]     docker compose restart [service]
#   status   <repo_name> <clone_dir>               docker compose ps
#   logs     <repo_name> <clone_dir> [svc] [lines] docker compose logs
#   destroy  <repo_name> <clone_dir>               docker compose down -v
#   list                                           list repos with running stacks
#   alloc    <repo_name>                           print allocated ports as JSON

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPERVISOR_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEMPLATES_DIR="${SCRIPT_DIR}/../templates"
CONFIG_LIB="${SCRIPT_DIR}/../lib/config.sh"

# Port allocation ranges — each repo occupies a consecutive triplet.
# Repo 1 → 3001 / 4001 / 5433
# Repo 2 → 3002 / 4002 / 5434
# ...
FRONTEND_PORT_BASE=3000
API_PORT_BASE=4000
POSTGRES_PORT_BASE=5432

# ── Bootstrap config ──────────────────────────────────────────────────────────

# Source config lib when available (provides get_config_value / get_repository_config).
if [[ -f "${CONFIG_LIB}" ]]; then
  # shellcheck source=../lib/config.sh
  source "${CONFIG_LIB}"
  load_config "${SUPERVISOR_CONFIG_FILE:-${SUPERVISOR_ROOT}/supervisor/config/supervisor.yaml}" 2>/dev/null || true
fi

# ── Logging ───────────────────────────────────────────────────────────────────

_log() {
  local level="$1"; shift
  printf '{"ts":"%s","level":"%s","component":"repo-manager","msg":"%s"}\n' \
    "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$level" "$*" >&2
}

_die() {
  _log "error" "$*"
  exit 1
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_require_arg() {
  local name="$1" value="$2"
  [[ -n "${value}" ]] || _die "Missing required argument: ${name}"
}

_compose_file() {
  local clone_dir="$1"
  printf '%s/docker-compose.yml' "${clone_dir}"
}

_env_file() {
  local clone_dir="$1"
  printf '%s/.env' "${clone_dir}"
}

# ── Command: repo_setup ───────────────────────────────────────────────────────
# Copy template compose file and .env to the target repo directory, substituting
# port and name placeholders.
#
# Arguments:
#   repo_name  — logical name of the repository (matches supervisor.yaml)
#   clone_dir  — absolute path to the cloned repository directory
#   ports_json — JSON object with keys: frontend, api, postgres  (all integers)
#                e.g. '{"frontend":3001,"api":4001,"postgres":5433}'
#
repo_setup() {
  local repo_name="$1"
  local clone_dir="$2"
  local ports_json="$3"

  _require_arg "repo_name" "${repo_name}"
  _require_arg "clone_dir" "${clone_dir}"
  _require_arg "ports_json" "${ports_json}"

  [[ -d "${clone_dir}" ]] || _die "clone_dir does not exist: ${clone_dir}"

  # Parse ports from JSON using jq (preferred) or basic grep fallback.
  local fe_port api_port pg_port
  if command -v jq &>/dev/null; then
    fe_port="$(printf '%s' "${ports_json}"  | jq -r '.frontend')"
    api_port="$(printf '%s' "${ports_json}" | jq -r '.api')"
    pg_port="$(printf '%s' "${ports_json}"  | jq -r '.postgres')"
  else
    fe_port="$(printf '%s'  "${ports_json}" | grep -oP '"frontend"\s*:\s*\K[0-9]+')"
    api_port="$(printf '%s' "${ports_json}" | grep -oP '"api"\s*:\s*\K[0-9]+')"
    pg_port="$(printf '%s'  "${ports_json}" | grep -oP '"postgres"\s*:\s*\K[0-9]+')"
  fi

  [[ -n "${fe_port}"  && "${fe_port}"  != "null" ]] || _die "ports_json missing 'frontend' key"
  [[ -n "${api_port}" && "${api_port}" != "null" ]] || _die "ports_json missing 'api' key"
  [[ -n "${pg_port}"  && "${pg_port}"  != "null" ]] || _die "ports_json missing 'postgres' key"

  local compose_tmpl="${TEMPLATES_DIR}/docker-compose.repo.yml.tmpl"
  local env_tmpl="${TEMPLATES_DIR}/repo.env.tmpl"

  [[ -f "${compose_tmpl}" ]] || _die "Compose template not found: ${compose_tmpl}"
  [[ -f "${env_tmpl}" ]]     || _die "Env template not found: ${env_tmpl}"

  local dest_compose
  dest_compose="$(_compose_file "${clone_dir}")"
  local dest_env
  dest_env="$(_env_file "${clone_dir}")"

  # Install compose file — template is already valid Compose YAML; no substitution
  # needed here because Docker Compose reads the .env file at runtime.
  cp "${compose_tmpl}" "${dest_compose}"

  # Install .env — substitute the __PLACEHOLDER__ tokens with actual values.
  sed \
    -e "s/__REPO_NAME__/${repo_name}/g" \
    -e "s/__FRONTEND_PORT__/${fe_port}/g" \
    -e "s/__API_PORT__/${api_port}/g" \
    -e "s/__POSTGRES_PORT__/${pg_port}/g" \
    "${env_tmpl}" > "${dest_env}"

  _log "info" "setup complete repo=${repo_name} dir=${clone_dir} fe=${fe_port} api=${api_port} pg=${pg_port}"
}

# ── Command: repo_start ───────────────────────────────────────────────────────

repo_start() {
  local repo_name="$1"
  local clone_dir="$2"

  _require_arg "repo_name" "${repo_name}"
  _require_arg "clone_dir" "${clone_dir}"

  local compose_file
  compose_file="$(_compose_file "${clone_dir}")"
  [[ -f "${compose_file}" ]] || \
    _die "docker-compose.yml not found in ${clone_dir} — run 'repo-manager.sh setup' first"

  _log "info" "starting stack repo=${repo_name}"
  docker compose -f "${compose_file}" --env-file "$(_env_file "${clone_dir}")" up -d
  _log "info" "stack started repo=${repo_name}"
}

# ── Command: repo_stop ────────────────────────────────────────────────────────

repo_stop() {
  local repo_name="$1"
  local clone_dir="$2"

  _require_arg "repo_name" "${repo_name}"
  _require_arg "clone_dir" "${clone_dir}"

  _log "info" "stopping stack repo=${repo_name}"
  docker compose -f "$(_compose_file "${clone_dir}")" \
    --env-file "$(_env_file "${clone_dir}")" down
  _log "info" "stack stopped repo=${repo_name}"
}

# ── Command: repo_restart ─────────────────────────────────────────────────────
# Restarts the whole stack, or a single service if provided.

repo_restart() {
  local repo_name="$1"
  local clone_dir="$2"
  local service="${3:-}"   # optional

  _require_arg "repo_name" "${repo_name}"
  _require_arg "clone_dir" "${clone_dir}"

  _log "info" "restarting repo=${repo_name} service=${service:-all}"
  docker compose -f "$(_compose_file "${clone_dir}")" \
    --env-file "$(_env_file "${clone_dir}")" \
    restart ${service}
  _log "info" "restart done repo=${repo_name}"
}

# ── Command: repo_status ──────────────────────────────────────────────────────

repo_status() {
  local repo_name="$1"
  local clone_dir="$2"

  _require_arg "repo_name" "${repo_name}"
  _require_arg "clone_dir" "${clone_dir}"

  docker compose -f "$(_compose_file "${clone_dir}")" \
    --env-file "$(_env_file "${clone_dir}")" \
    ps
}

# ── Command: repo_logs ────────────────────────────────────────────────────────
# Streams (or dumps) logs for all services or a named service.

repo_logs() {
  local repo_name="$1"
  local clone_dir="$2"
  local service="${3:-}"      # optional — empty = all services
  local lines="${4:-100}"     # default: last 100 lines

  _require_arg "repo_name" "${repo_name}"
  _require_arg "clone_dir" "${clone_dir}"

  docker compose -f "$(_compose_file "${clone_dir}")" \
    --env-file "$(_env_file "${clone_dir}")" \
    logs --tail="${lines}" --follow \
    ${service}
}

# ── Command: repo_destroy ─────────────────────────────────────────────────────
# Stops the stack AND removes all associated volumes (destroys data).

repo_destroy() {
  local repo_name="$1"
  local clone_dir="$2"

  _require_arg "repo_name" "${repo_name}"
  _require_arg "clone_dir" "${clone_dir}"

  _log "warn" "destroying stack + volumes repo=${repo_name}"
  docker compose -f "$(_compose_file "${clone_dir}")" \
    --env-file "$(_env_file "${clone_dir}")" \
    down -v
  _log "info" "destroy complete repo=${repo_name}"
}

# ── Command: list_running_repos ───────────────────────────────────────────────
# Find all repo directories under /home/agent/repos that have a running compose
# stack (i.e. at least one container with status "running").

list_running_repos() {
  local repos_root="${REPOS_ROOT:-/home/agent/repos}"

  if [[ ! -d "${repos_root}" ]]; then
    _log "warn" "repos root does not exist: ${repos_root}"
    return 0
  fi

  local found_any=false
  for compose_file in "${repos_root}"/*/docker-compose.yml; do
    [[ -f "${compose_file}" ]] || continue
    local repo_dir
    repo_dir="$(dirname "${compose_file}")"
    local repo_name
    repo_name="$(basename "${repo_dir}")"
    local env_file
    env_file="${repo_dir}/.env"

    # Check if there is at least one running container for this project.
    local running_count
    if [[ -f "${env_file}" ]]; then
      running_count="$(docker compose -f "${compose_file}" --env-file "${env_file}" \
        ps --status running --quiet 2>/dev/null | wc -l | tr -d ' ')"
    else
      running_count="$(docker compose -f "${compose_file}" \
        ps --status running --quiet 2>/dev/null | wc -l | tr -d ' ')"
    fi

    if [[ "${running_count}" -gt 0 ]]; then
      printf '%s\t%s\n' "${repo_name}" "${repo_dir}"
      found_any=true
    fi
  done

  if [[ "${found_any}" == "false" ]]; then
    _log "info" "no running repo stacks found under ${repos_root}"
  fi
}

# ── Command: allocate_ports ───────────────────────────────────────────────────
# Returns a JSON port allocation for the given repo name.
#
# Priority:
#   1. Explicit ports in supervisor.yaml (via get_repository_config)
#   2. Auto-allocate: scan repos_root for existing .env files and pick the next
#      free slot above the base port.
#
# Output:  {"frontend": N, "api": N, "postgres": N}

allocate_ports() {
  local repo_name="$1"
  _require_arg "repo_name" "${repo_name}"

  # 1. Try supervisor.yaml first.
  if declare -f get_repository_config &>/dev/null; then
    local repo_json
    repo_json="$(get_repository_config "${repo_name}" 2>/dev/null || echo "null")"
    if [[ "${repo_json}" != "null" ]] && command -v jq &>/dev/null; then
      local fe api pg
      fe="$(printf '%s' "${repo_json}" | jq -r '.ports.frontend // empty')"
      api="$(printf '%s' "${repo_json}" | jq -r '.ports.api // empty')"
      pg="$(printf '%s' "${repo_json}" | jq -r '.ports.postgres // empty')"
      if [[ -n "${fe}" && -n "${api}" && -n "${pg}" ]]; then
        printf '{"frontend":%s,"api":%s,"postgres":%s}\n' "${fe}" "${api}" "${pg}"
        return 0
      fi
    fi
  fi

  # 2. Auto-allocate by scanning existing .env files for the highest used slot.
  local repos_root="${REPOS_ROOT:-/home/agent/repos}"
  local max_slot=0

  for env_file in "${repos_root}"/*/.env; do
    [[ -f "${env_file}" ]] || continue
    # Extract FRONTEND_PORT value; slot = port - base
    local port
    port="$(grep -E '^FRONTEND_PORT=' "${env_file}" | cut -d= -f2 | tr -d ' ' || true)"
    if [[ -n "${port}" && "${port}" -gt "${FRONTEND_PORT_BASE}" ]]; then
      local slot=$(( port - FRONTEND_PORT_BASE ))
      (( slot > max_slot )) && max_slot="${slot}"
    fi
  done

  local next_slot=$(( max_slot + 1 ))
  local next_fe=$(( FRONTEND_PORT_BASE + next_slot ))
  local next_api=$(( API_PORT_BASE + next_slot ))
  local next_pg=$(( POSTGRES_PORT_BASE + next_slot ))

  printf '{"frontend":%d,"api":%d,"postgres":%d}\n' "${next_fe}" "${next_api}" "${next_pg}"
}

# ── Entrypoint ────────────────────────────────────────────────────────────────

_usage() {
  cat >&2 <<'EOF'
Usage: repo-manager.sh <command> [args...]

Commands:
  setup    <repo_name> <clone_dir> <ports_json>  Initialise compose + env from templates
  start    <repo_name> <clone_dir>               docker compose up -d
  stop     <repo_name> <clone_dir>               docker compose down
  restart  <repo_name> <clone_dir> [service]     docker compose restart [service]
  status   <repo_name> <clone_dir>               docker compose ps
  logs     <repo_name> <clone_dir> [svc] [lines] docker compose logs (follows)
  destroy  <repo_name> <clone_dir>               docker compose down -v  (REMOVES DATA)
  list                                           Print running repos (name<TAB>path)
  alloc    <repo_name>                           Print port allocation JSON
EOF
}

main() {
  local cmd="${1:-}"
  shift || true

  case "${cmd}" in
    setup)    repo_setup    "${1:-}" "${2:-}" "${3:-}" ;;
    start)    repo_start    "${1:-}" "${2:-}" ;;
    stop)     repo_stop     "${1:-}" "${2:-}" ;;
    restart)  repo_restart  "${1:-}" "${2:-}" "${3:-}" ;;
    status)   repo_status   "${1:-}" "${2:-}" ;;
    logs)     repo_logs     "${1:-}" "${2:-}" "${3:-}" "${4:-100}" ;;
    destroy)  repo_destroy  "${1:-}" "${2:-}" ;;
    list)     list_running_repos ;;
    alloc)    allocate_ports "${1:-}" ;;
    ""|help|-h|--help) _usage; exit 0 ;;
    *) _log "error" "Unknown command: ${cmd}"; _usage; exit 1 ;;
  esac
}

main "$@"

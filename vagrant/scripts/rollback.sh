#!/usr/bin/env bash
# rollback.sh — restore credentials and restart Docker services after a failed update
#
# Usage: rollback.sh --backup-dir /var/lib/aquarco/backups/20260404T180000
#
# Exit codes:
#   0 — rollback completed, services healthy
#   1 — argument error or services unhealthy after rollback

set -euo pipefail

COMPOSE_DIR="/home/agent/aquarco/docker"
HEALTH_TIMEOUT=60    # seconds to wait for services to become healthy

GH_TOKEN_DEST="${HOME}/.config/gh/hosts.yml"
CLAUDE_CREDS_DEST="${HOME}/.claude/.credentials.json"

# ── Parse arguments ──────────────────────────────────────────────────────────

BACKUP_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup-dir)
      BACKUP_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "${BACKUP_DIR}" ]]; then
  echo "ERROR: --backup-dir is required" >&2
  exit 1
fi

if [[ ! -d "${BACKUP_DIR}" ]]; then
  echo "ERROR: Backup directory does not exist: ${BACKUP_DIR}" >&2
  exit 1
fi

# ── Load secrets ────────────────────────────────────────────────────────────

# POSTGRES_PASSWORD and DATABASE_URL are required by compose.yml.
# The file is readable by both root and the agent group (640 root:agent).
# shellcheck source=/dev/null
set -a
# shellcheck disable=SC1091
. /etc/aquarco/docker-secrets.env
set +a

# ── Detect environment (dev vs production) ─────────────────────────────────

AQUARCO_ENV="development"
if [[ -f /etc/aquarco/env ]]; then
  AQUARCO_ENV="$(cat /etc/aquarco/env)"
fi

# NOTE: Docker commands use sudo because the agent user is not in the docker
# group. The backup-credentials.sh script runs as the agent user (no sudo)
# which is intentional — credential files are owned by the agent user.
COMPOSE_CMD=(sudo docker compose -f compose.yml)
if [[ "${AQUARCO_ENV}" == "production" ]]; then
  COMPOSE_CMD+=(-f compose.prod.yml --env-file versions.env)
fi

# ── Stop Docker services ────────────────────────────────────────────────────

echo "[rollback] Stopping Docker Compose services (env=${AQUARCO_ENV})..."
cd "${COMPOSE_DIR}"
"${COMPOSE_CMD[@]}" down --timeout 30 || true

# ── Restore credentials ─────────────────────────────────────────────────────

echo "[rollback] Restoring credentials from ${BACKUP_DIR}..."

if [[ -f "${BACKUP_DIR}/hosts.yml" ]]; then
  mkdir -p "$(dirname "${GH_TOKEN_DEST}")"
  cp "${BACKUP_DIR}/hosts.yml" "${GH_TOKEN_DEST}"
  chmod 600 "${GH_TOKEN_DEST}"
  echo "[rollback] Restored GitHub token."
fi

if [[ -f "${BACKUP_DIR}/credentials.json" ]]; then
  mkdir -p "$(dirname "${CLAUDE_CREDS_DEST}")"
  cp "${BACKUP_DIR}/credentials.json" "${CLAUDE_CREDS_DEST}"
  chmod 600 "${CLAUDE_CREDS_DEST}"
  echo "[rollback] Restored Claude API credentials."
fi

# ── Restart Docker services ─────────────────────────────────────────────────

echo "[rollback] Starting Docker Compose services (env=${AQUARCO_ENV})..."
"${COMPOSE_CMD[@]}" up -d

# ── Wait for health ─────────────────────────────────────────────────────────

echo "[rollback] Waiting up to ${HEALTH_TIMEOUT}s for services to become healthy..."

elapsed=0
while (( elapsed < HEALTH_TIMEOUT )); do
  # Count containers that are either not running or have a failing health check.
  # Containers without a HEALTHCHECK directive have Health="" or null and are
  # accepted as long as their State is "running".
  unhealthy="$("${COMPOSE_CMD[@]}" ps --format json 2>/dev/null \
    | jq -r 'select(
        .State != "running"
        or (.Health != null and .Health != "" and .Health != "healthy")
      ) | .Name' \
    | wc -l)"

  if (( unhealthy == 0 )); then
    echo "[rollback] All services healthy."
    exit 0
  fi

  sleep 5
  elapsed=$(( elapsed + 5 ))
done

echo "[rollback] ERROR: Services did not become healthy within ${HEALTH_TIMEOUT}s" >&2
exit 1

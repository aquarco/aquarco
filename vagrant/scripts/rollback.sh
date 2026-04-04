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

# ── Stop Docker services ────────────────────────────────────────────────────

echo "[rollback] Stopping Docker Compose services..."
cd "${COMPOSE_DIR}"
sudo docker compose down --timeout 30 || true

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

echo "[rollback] Starting Docker Compose services..."
sudo docker compose up -d

# ── Wait for health ─────────────────────────────────────────────────────────

echo "[rollback] Waiting up to ${HEALTH_TIMEOUT}s for services to become healthy..."

elapsed=0
while (( elapsed < HEALTH_TIMEOUT )); do
  # Count unhealthy/starting containers
  unhealthy="$(sudo docker compose ps --format json 2>/dev/null \
    | jq -r 'select(.Health != null and .Health != "healthy" and .Health != "") | .Name' \
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

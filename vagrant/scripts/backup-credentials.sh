#!/usr/bin/env bash
# backup-credentials.sh — snapshot GitHub token and Claude API key before update
#
# Exit codes:
#   0 — at least one credential was found and backed up
#   1 — neither credential was found (nothing to back up)
#
# Output: prints the backup directory path on the last line of stdout.

set -euo pipefail

BACKUP_ROOT="/var/lib/aquarco/backups"
MAX_BACKUPS=10
TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
BACKUP_DIR="${BACKUP_ROOT}/${TIMESTAMP}"

GH_TOKEN_PATH="${HOME}/.config/gh/hosts.yml"
CLAUDE_CREDS_PATH="${HOME}/.claude/.credentials.json"

# ── Create backup directory ──────────────────────────────────────────────────

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

# ── Copy credentials ────────────────────────────────────────────────────────

found=()
missing=()

if [[ -f "${GH_TOKEN_PATH}" ]]; then
  cp "${GH_TOKEN_PATH}" "${BACKUP_DIR}/hosts.yml"
  chmod 600 "${BACKUP_DIR}/hosts.yml"
  found+=("gh_token")
else
  missing+=("gh_token")
fi

if [[ -f "${CLAUDE_CREDS_PATH}" ]]; then
  cp "${CLAUDE_CREDS_PATH}" "${BACKUP_DIR}/credentials.json"
  chmod 600 "${BACKUP_DIR}/credentials.json"
  found+=("claude_api_key")
else
  missing+=("claude_api_key")
fi

# ── Write manifest ──────────────────────────────────────────────────────────

_json_array() {
  if [[ $# -eq 0 ]]; then
    echo '[]'
  else
    printf '%s\n' "$@" | jq -R . | jq -s .
  fi
}

cat > "${BACKUP_DIR}/manifest.json" <<MANIFEST
{
  "timestamp": "${TIMESTAMP}",
  "found": $(_json_array "${found[@]+"${found[@]}"}"),
  "missing": $(_json_array "${missing[@]+"${missing[@]}"}")
}
MANIFEST
chmod 600 "${BACKUP_DIR}/manifest.json"

# ── Prune old backups (keep only MAX_BACKUPS) ───────────────────────────────

backup_count="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d | wc -l)"
if (( backup_count > MAX_BACKUPS )); then
  # Remove oldest first (sorted by name = timestamp)
  find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d -print0 \
    | sort -z \
    | head -z -n "$(( backup_count - MAX_BACKUPS ))" \
    | xargs -0 rm -rf
fi

# ── Exit status ─────────────────────────────────────────────────────────────

if (( ${#found[@]} == 0 )); then
  # Clean up the empty backup directory so it doesn't count toward MAX_BACKUPS
  rm -rf "${BACKUP_DIR}"
  echo "ERROR: No credentials found to back up." >&2
  exit 1
fi

echo "${BACKUP_DIR}"

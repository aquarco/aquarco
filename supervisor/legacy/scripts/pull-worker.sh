#!/usr/bin/env bash
# supervisor/scripts/pull-worker.sh
# Pulls latest changes for repositories with clone_status='ready'.
#
# Called from the supervisor main loop on each cycle.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPERVISOR_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "${SUPERVISOR_ROOT}/lib/utils.sh"

pull_ready_repos() {
  local db_url="${DATABASE_URL:?DATABASE_URL must be set}"

  local rows
  rows="$(psql --no-psqlrc --tuples-only --no-align "$db_url" -c "
    SELECT name, clone_dir, branch FROM repositories
    WHERE clone_status = 'ready'
    ORDER BY last_pulled_at ASC NULLS FIRST;
  " 2>/dev/null || true)"

  [[ -n "$rows" ]] || return 0

  while IFS='|' read -r repo_name clone_dir branch; do
    [[ -n "$repo_name" ]] || continue
    [[ -d "$clone_dir/.git" ]] || continue

    local old_sha new_sha
    old_sha="$(git -C "$clone_dir" rev-parse HEAD 2>/dev/null || echo "")"

    if git -C "$clone_dir" fetch origin "$branch" --quiet 2>/dev/null && \
       git -C "$clone_dir" reset --hard "origin/$branch" --quiet 2>/dev/null; then

      new_sha="$(git -C "$clone_dir" rev-parse HEAD 2>/dev/null || echo "")"

      psql --no-psqlrc --tuples-only --no-align "$db_url" -c "
        UPDATE repositories
        SET last_pulled_at = NOW(),
            head_sha = '$new_sha'
        WHERE name = '$repo_name';
      " >/dev/null 2>&1

      if [[ "$old_sha" != "$new_sha" ]]; then
        log "info" "Pulled new changes: name=$repo_name old=$old_sha new=$new_sha"
      fi
    else
      log "warn" "Pull failed: name=$repo_name dir=$clone_dir"
    fi
  done <<< "$rows"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  pull_ready_repos
fi

#!/usr/bin/env bash
# supervisor/scripts/clone-worker.sh
# Picks up repositories with clone_status='pending', clones them, and updates status.
#
# Called from the supervisor main loop on each cycle.
# Idempotent: safe to call repeatedly.
#
# On clone failure:
#   - Converts HTTPS URL to SSH
#   - Generates a per-repo deploy key
#   - Stores public key + SSH URL in DB so the UI can guide the user

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPERVISOR_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Source shared utils
source "${SUPERVISOR_ROOT}/lib/utils.sh"

# log() may be provided by the supervisor environment; define a fallback.
if ! declare -f log &>/dev/null; then
  log() {
    local level="$1"; shift
    printf '{"ts":"%s","level":"%s","component":"clone-worker","msg":"%s"}\n' \
      "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$level" "$*" >&2
  }
fi

# Convert an HTTPS repo URL to SSH form.
# https://github.com/org/repo.git → git@github.com:org/repo.git
# Already-SSH URLs are returned unchanged.
url_to_ssh() {
  local url="$1"
  if [[ "$url" =~ ^https?://([^/]+)/(.+)$ ]]; then
    echo "git@${BASH_REMATCH[1]}:${BASH_REMATCH[2]}"
  else
    echo "$url"
  fi
}

# Derive a filesystem-safe key directory name from a repo URL.
# git@github.com:org/repo.git → github.com-org-repo
url_to_key_name() {
  echo "$1" | sed 's|^git@||; s|^https\?://||; s|\.git$||; s|[/:]|-|g; s|[^a-zA-Z0-9._-]|-|g'
}

clone_pending_repos() {
  local db_url="${DATABASE_URL:?DATABASE_URL must be set}"

  # Fetch one pending repo at a time (avoid racing with concurrent runs)
  local row
  row="$(psql --no-psqlrc --tuples-only --no-align "$db_url" -c "
    UPDATE repositories
    SET clone_status = 'cloning'
    WHERE name = (
      SELECT name FROM repositories
      WHERE clone_status = 'pending'
      ORDER BY name
      LIMIT 1
      FOR UPDATE SKIP LOCKED
    )
    RETURNING name, url, branch, clone_dir;
  " 2>/dev/null | grep '|' || true)"

  [[ -n "$row" ]] || return 0

  local repo_name repo_url repo_branch clone_dir
  IFS='|' read -r repo_name repo_url repo_branch clone_dir <<< "$row"

  log "info" "Cloning repository: name=$repo_name url=$repo_url branch=$repo_branch dir=$clone_dir"

  # Ensure parent directory exists
  mkdir -p "$(dirname "$clone_dir")"

  # If directory already exists with a valid git repo, treat as already cloned
  if [[ -d "$clone_dir/.git" ]]; then
    log "info" "Directory already contains a git repo, skipping clone: name=$repo_name dir=$clone_dir"
    local head_sha
    head_sha="$(git -C "$clone_dir" rev-parse HEAD 2>/dev/null || echo "")"

    psql --no-psqlrc --tuples-only --no-align "$db_url" -c "
      UPDATE repositories
      SET clone_status = 'ready',
          last_cloned_at = NOW(),
          head_sha = '$head_sha',
          error_message = NULL
      WHERE name = '$repo_name';
    " >/dev/null 2>&1
    return 0
  fi

  # Set up credentials based on URL type
  local git_ssh_cmd=""
  local github_token_file="$HOME/.ssh/github-token"

  if [[ "$repo_url" =~ ^https?:// ]]; then
    # HTTPS: use GitHub token if available — inject token into URL
    if [[ -f "$github_token_file" ]] && [[ -r "$github_token_file" ]]; then
      local token
      token="$(cat "$github_token_file")"
      if [[ -n "$token" ]]; then
        repo_url="${repo_url/https:\/\//https://x-access-token:${token}@}"
        log "info" "Using GitHub token for HTTPS clone"
      fi
    fi
  else
    # SSH: use per-repo deploy key if available
    local key_name
    key_name="$(url_to_key_name "$repo_url")"
    local repo_key="$HOME/.ssh/deploy-keys/$key_name/id_ed25519"
    if [[ -f "$repo_key" ]]; then
      git_ssh_cmd="ssh -i $repo_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
      log "info" "Using per-repo deploy key: $repo_key"
    fi
  fi

  # Clone attempt
  local clone_err
  if clone_err="$(GIT_SSH_COMMAND="${git_ssh_cmd}" git clone --branch "$repo_branch" --single-branch "$repo_url" "$clone_dir" 2>&1)"; then
    local head_sha
    head_sha="$(git -C "$clone_dir" rev-parse HEAD 2>/dev/null || echo "")"

    psql --no-psqlrc --tuples-only --no-align "$db_url" -c "
      UPDATE repositories
      SET clone_status = 'ready',
          last_cloned_at = NOW(),
          head_sha = '$head_sha',
          error_message = NULL
      WHERE name = '$repo_name';
    " >/dev/null 2>&1

    log "info" "Clone complete: name=$repo_name sha=$head_sha"
    return 0
  fi

  log "error" "Clone failed: name=$repo_name err=$clone_err"

  # Clone failed — generate deploy key (if not already generated) and rewrite URL to SSH
  local ssh_url
  ssh_url="$(url_to_ssh "$repo_url")"
  local ssh_key_name
  ssh_key_name="$(url_to_key_name "$ssh_url")"
  local ssh_key_dir="$HOME/.ssh/deploy-keys/$ssh_key_name"
  local ssh_repo_key="$ssh_key_dir/id_ed25519"

  local deploy_pub_key=""
  if [[ ! -f "$ssh_repo_key" ]]; then
    log "info" "Generating deploy key for: $repo_name -> $ssh_key_dir"
    mkdir -p "$ssh_key_dir"
    ssh-keygen -t ed25519 -f "$ssh_repo_key" -N '' -C "aifishtank-${ssh_key_name}" -q
  fi

  if [[ -f "${ssh_repo_key}.pub" ]]; then
    deploy_pub_key="$(cat "${ssh_repo_key}.pub")"
  fi

  # Escape single quotes for SQL
  local safe_err="${clone_err//\'/\'\'}"
  local safe_pub="${deploy_pub_key//\'/\'\'}"

  psql --no-psqlrc --tuples-only --no-align "$db_url" -c "
    UPDATE repositories
    SET clone_status = 'error',
        url = '$ssh_url',
        error_message = '${safe_err}',
        deploy_public_key = '${safe_pub}'
    WHERE name = '$repo_name';
  " >/dev/null 2>&1

  log "info" "Deploy key generated, URL rewritten to SSH: name=$repo_name url=$ssh_url"
}

# Can be sourced by supervisor.sh or run standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  clone_pending_repos
fi

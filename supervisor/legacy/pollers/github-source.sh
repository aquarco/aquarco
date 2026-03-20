#!/usr/bin/env bash
# supervisor/pollers/github-source.sh
# GitHub source poller.
#
# Monitors open pull requests and recent commits across configured repositories.
# Creates review and test tasks for new or updated PRs, and creates tasks for
# new commits on watched branches.
#
# Dependencies: config.sh, task-queue.sh must be sourced first.
#
# Usage (called by supervisor main loop):
#   source pollers/github-source.sh
#   poll_github_source

set -euo pipefail

# Source shared utilities (provides _tq_escape and _url_to_slug).
_GS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/utils.sh
source "${_GS_SCRIPT_DIR}/../lib/utils.sh"

# ── Logging ───────────────────────────────────────────────────────────────────

_gs_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '{"ts":"%s","level":"%s","component":"github-source-poller","msg":"%s"}\n' \
    "$ts" "$level" "$*" >&2
}

# ── Public: poll_github_source ────────────────────────────────────────────────
# Main entry point. Polls PRs and recent commits for all configured
# repositories that have the github-source poller enabled.
#
# Usage: poll_github_source
poll_github_source() {
  _gs_log "info" "Starting github-source poll cycle"

  if ! command -v gh &>/dev/null; then
    _gs_log "error" "gh CLI not found; cannot poll GitHub source"
    return 1
  fi

  local cursor
  cursor="$(get_poll_cursor "github-source" 2>/dev/null || echo "")"
  cursor="${cursor:-$(date -u -d "1 hour ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || \
                      date -u -v-1H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || \
                      echo "2000-01-01T00:00:00Z")}"

  local new_cursor
  new_cursor="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  local pr_count=0
  local commit_count=0
  local error_count=0
  local repo_name

  while IFS= read -r repo_name; do
    [[ -n "$repo_name" && "$repo_name" != "null" ]] || continue

    local repo_config
    repo_config="$(get_repository_config "$repo_name" 2>/dev/null || echo "null")"

    local has_poller
    has_poller="$(echo "$repo_config" | jq -r '
      if (.pollers // []) | map(select(. == "github-source")) | length > 0
      then "true" else "false" end' 2>/dev/null || echo "false")"

    [[ "$has_poller" == "true" ]] || continue

    local repo_url
    repo_url="$(echo "$repo_config" | jq -r '.url // ""')"
    local repo_slug
    repo_slug="$(_url_to_slug "$repo_url")"

    if [[ -z "$repo_slug" ]]; then
      _gs_log "warn" "Cannot determine GitHub slug for repo=$repo_name"
      continue
    fi

    # Poll PRs.
    local pr_result=0
    local prs_created=0
    prs_created="$(poll_github_prs "$repo_name" "$repo_slug" "$cursor" 2>/dev/null || true)"
    pr_result=$?
    if [[ "$pr_result" -eq 0 ]]; then
      (( pr_count += prs_created )) || true
      _gs_log "info" "PR poll complete for repo=$repo_slug prs_created=$prs_created"
    else
      _gs_log "warn" "PR poll encountered errors for repo=$repo_slug"
      (( error_count++ )) || true
    fi

    # Poll recent commits.
    local clone_dir
    clone_dir="$(echo "$repo_config" | jq -r '.cloneDir // ""')"
    if [[ -n "$clone_dir" && -d "${clone_dir}/.git" ]]; then
      local commit_result=0
      local commits_created=0
      commits_created="$(poll_recent_commits "$repo_name" "$clone_dir" "$cursor" 2>/dev/null || true)"
      commit_result=$?
      if [[ "$commit_result" -eq 0 ]]; then
        (( commit_count += commits_created )) || true
      else
        _gs_log "warn" "Commit poll encountered errors for repo=$repo_name"
        (( error_count++ )) || true
      fi
    else
      _gs_log "info" "Skipping commit poll for repo=$repo_name (no local clone at $clone_dir)"
    fi

  done < <(get_repositories 2>/dev/null)

  local state_data
  state_data="$(jq -n \
    --arg prs "$pr_count" \
    --arg commits "$commit_count" \
    --arg errors "$error_count" \
    '{"prs_processed": ($prs|tonumber), "commits_processed": ($commits|tonumber), "errors": ($errors|tonumber)}')"

  update_poll_state "github-source" "$new_cursor" "$state_data"

  _gs_log "info" "github-source poll cycle complete errors=$error_count"
}

# ── Public: poll_github_prs ───────────────────────────────────────────────────
# Fetch open PRs from GitHub and process any that are new or updated since
# the cursor timestamp.
#
# Arguments:
#   $1  repo_name  — internal repository name
#   $2  repo_slug  — GitHub owner/repo slug
#   $3  cursor     — ISO 8601 timestamp; process PRs updated after this
poll_github_prs() {
  local repo_name="$1"
  local repo_slug="$2"
  local cursor="$3"

  _gs_log "info" "Polling PRs for repo=$repo_slug since=$cursor" >&2

  local prs_json
  if ! prs_json="$(gh pr list \
      --repo "$repo_slug" \
      --state "open" \
      --json "number,title,headRefName,baseRefName,url,labels,createdAt,updatedAt,additions,deletions,changedFiles" \
      --limit 50 2>/dev/null)"; then
    _gs_log "error" "gh pr list failed for repo=$repo_slug" >&2
    return 1
  fi

  local fetched_pr_count
  fetched_pr_count="$(echo "$prs_json" | jq 'length' 2>/dev/null || echo "0")"
  _gs_log "info" "Found $fetched_pr_count open PRs in $repo_slug" >&2

  local created_task_count=0
  local i
  for (( i = 0; i < fetched_pr_count; i++ )); do
    local pr_json
    pr_json="$(echo "$prs_json" | jq ".[$i]")"

    local updated_at
    updated_at="$(echo "$pr_json" | jq -r '.updatedAt // ""')"

    # Determine if this PR is "new" or "updated" relative to the cursor.
    local created_at
    created_at="$(echo "$pr_json" | jq -r '.createdAt // ""')"

    local event_type="pr_updated"
    if [[ "$created_at" > "$cursor" ]]; then
      event_type="pr_opened"
    fi

    if [[ "$updated_at" > "$cursor" ]] || [[ "$created_at" > "$cursor" ]]; then
      if process_pr "$pr_json" "$repo_name" "$repo_slug" "$event_type"; then
        (( created_task_count++ )) || true
      fi
    fi
  done

  # Print created count to stdout so the caller can accumulate it.
  echo "$created_task_count"
}

# ── Public: poll_recent_commits ───────────────────────────────────────────────
# Use git log to find commits on the default branch since the cursor timestamp.
# Creates tasks for each new commit if a relevant trigger fires.
#
# Arguments:
#   $1  repo_name   — internal repository name
#   $2  clone_dir   — absolute path to the local git clone
#   $3  cursor      — ISO 8601 timestamp; process commits after this
poll_recent_commits() {
  local repo_name="$1"
  local clone_dir="$2"
  local cursor="$3"

  _gs_log "info" "Polling commits for repo=$repo_name dir=$clone_dir since=$cursor"

  # Fetch latest from remote before inspecting log.
  if ! git -C "$clone_dir" fetch --quiet origin 2>/dev/null; then
    _gs_log "warn" "git fetch failed for repo=$repo_name at $clone_dir"
  fi

  local branch
  branch="$(git -C "$clone_dir" rev-parse --abbrev-ref origin/HEAD 2>/dev/null | sed 's|origin/||' || echo "main")"

  # List commits since cursor, one per line: sha<TAB>subject<TAB>author<TAB>date
  local commit_lines
  commit_lines="$(git -C "$clone_dir" log \
    "origin/${branch}" \
    --since="$cursor" \
    --pretty=format:"%H\t%s\t%an\t%aI" \
    --no-walk=unsorted \
    2>/dev/null || true)"

  if [[ -z "$commit_lines" ]]; then
    _gs_log "info" "No new commits for repo=$repo_name since $cursor" >&2
    echo "0"
    return 0
  fi

  local created_commit_count=0
  local sha subject author date_str

  while IFS=$'\t' read -r sha subject author date_str; do
    [[ -n "$sha" ]] || continue

    local task_id="github-commit-${repo_name}-${sha:0:12}"

    if task_exists "$task_id" 2>/dev/null; then
      _gs_log "info" "Commit already queued: $sha" >&2
      continue
    fi

    _gs_log "info" "New commit on $repo_name: $sha $subject" >&2

    local context_json
    context_json="$(jq -n \
      --arg sha "$sha" \
      --arg subject "$subject" \
      --arg author "$author" \
      --arg date "$date_str" \
      --arg repo "$repo_name" \
      --arg branch "$branch" \
      '{
        commit_sha: $sha,
        subject: $subject,
        author: $author,
        committed_at: $date,
        repository: $repo,
        branch: $branch
      }')"

    if create_task \
        "$task_id" \
        "Review commit: $subject" \
        "review" \
        "github-commit" \
        "$sha" \
        "$repo_name" \
        "pr-review-pipeline" \
        "$context_json"; then
      (( created_commit_count++ )) || true
      _gs_log "info" "Created review task for commit $sha in $repo_name" >&2
    fi
  done <<< "$commit_lines"

  _gs_log "info" "Commit poll complete for repo=$repo_name: $created_commit_count new tasks" >&2
  # Print created count to stdout so the caller can accumulate it.
  echo "$created_commit_count"
}

# ── Public: process_pr ───────────────────────────────────────────────────────
# Process a single pull request JSON object.
# Creates review and test tasks according to the configured triggers.
#
# Arguments:
#   $1  pr_json     — JSON object from gh pr list
#   $2  repo_name   — internal repository name
#   $3  repo_slug   — GitHub owner/repo slug
#   $4  event_type  — "pr_opened" | "pr_updated"
#
# Returns 0 if at least one task was created, 1 otherwise.
process_pr() {
  local pr_json="$1"
  local repo_name="$2"
  local repo_slug="$3"
  local event_type="${4:-pr_opened}"

  local pr_number pr_title pr_url head_branch base_branch
  pr_number="$(echo "$pr_json" | jq -r '.number')"
  pr_title="$(echo "$pr_json" | jq -r '.title')"
  pr_url="$(echo "$pr_json" | jq -r '.url // ""')"
  head_branch="$(echo "$pr_json" | jq -r '.headRefName // ""')"
  base_branch="$(echo "$pr_json" | jq -r '.baseRefName // "main"')"

  _gs_log "info" "Processing PR #$pr_number ($event_type): $pr_title"

  # Skip PRs created by the system itself to prevent recursion.
  if [[ "$head_branch" == aquarco/* ]]; then
    _gs_log "info" "Skipping PR #$pr_number: head branch '$head_branch' is system-created"
    return 1
  fi

  # Resolve trigger configuration for this event type.
  local trigger_categories_json
  trigger_categories_json="$(yq -o=json \
    ".spec.pollers[] | select(.name == \"github-source\") | .config.triggers.${event_type} // []" \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "[]")"

  local trigger_count
  trigger_count="$(echo "$trigger_categories_json" | jq 'length' 2>/dev/null || echo "0")"

  if [[ "$trigger_count" -eq 0 ]]; then
    _gs_log "info" "No triggers configured for event=$event_type; skipping PR #$pr_number"
    return 1
  fi

  local created_count=0
  local context_json
  context_json="$(jq -n \
    --arg number "$pr_number" \
    --arg title "$pr_title" \
    --arg url "$pr_url" \
    --arg head "$head_branch" \
    --arg base "$base_branch" \
    --arg repo_slug "$repo_slug" \
    --arg event "$event_type" \
    '{
      github_pr_number: ($number | tonumber),
      title: $title,
      url: $url,
      head_branch: $head,
      base_branch: $base,
      repository_slug: $repo_slug,
      event_type: $event
    }')"

  local i
  for (( i = 0; i < trigger_count; i++ )); do
    local category
    category="$(echo "$trigger_categories_json" | jq -r ".[$i]")"
    [[ -n "$category" && "$category" != "null" ]] || continue

    local task_id="github-pr-${repo_name}-${pr_number}-${category}"

    # For pr_updated events, suffix with a timestamp to allow re-triggering.
    if [[ "$event_type" == "pr_updated" ]]; then
      local ts_suffix
      ts_suffix="$(date -u +"%Y%m%dT%H%M")"
      task_id="github-pr-${repo_name}-${pr_number}-${category}-${ts_suffix}"
    fi

    if task_exists "$task_id" 2>/dev/null; then
      _gs_log "info" "PR task already queued: $task_id"
      continue
    fi

    if create_task \
        "$task_id" \
        "${category^} PR #${pr_number}: $pr_title" \
        "$category" \
        "github-pr" \
        "$pr_number" \
        "$repo_name" \
        "pr-review-pipeline" \
        "$context_json"; then
      (( created_count++ )) || true
      _gs_log "info" "Created task=$task_id for PR #$pr_number event=$event_type"
    fi
  done

  [[ "$created_count" -gt 0 ]]
}

# _url_to_slug is provided by supervisor/lib/utils.sh (sourced at top of file).

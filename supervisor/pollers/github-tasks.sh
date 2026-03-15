#!/usr/bin/env bash
# supervisor/pollers/github-tasks.sh
# GitHub tasks poller.
#
# Polls GitHub issues labelled "agent-task" across all configured repositories.
# Maps issue labels to task categories via the config label mapping, then
# inserts new tasks into the queue. Already-processed issues are skipped via
# the task_exists() idempotency check.
#
# Dependencies: config.sh, task-queue.sh must be sourced first.
#
# Usage (called by supervisor main loop):
#   source pollers/github-tasks.sh
#   poll_github_tasks

set -euo pipefail

# Source shared utilities (provides _tq_escape and _url_to_slug).
_GT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/utils.sh
source "${_GT_SCRIPT_DIR}/../lib/utils.sh"

# ── Logging ───────────────────────────────────────────────────────────────────

_gt_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '{"ts":"%s","level":"%s","component":"github-tasks-poller","msg":"%s"}\n' \
    "$ts" "$level" "$*" >&2
}

# ── Public: poll_github_tasks ─────────────────────────────────────────────────
# Main entry point. Iterates over all configured repositories and fetches
# issues with the "agent-task" label that have been updated since the last
# poll cursor.
#
# Usage: poll_github_tasks
poll_github_tasks() {
  _gt_log "info" "Starting github-tasks poll cycle"

  if ! command -v gh &>/dev/null; then
    _gt_log "error" "gh CLI not found; cannot poll GitHub issues"
    return 1
  fi

  # Fetch the cursor (ISO timestamp of last successful poll).
  local cursor
  cursor="$(get_poll_cursor "github-tasks" 2>/dev/null || echo "")"
  cursor="${cursor:-$(date -u -d "24 hours ago" +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || \
                      date -u -v-24H +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || \
                      echo "2000-01-01T00:00:00Z")}"

  local new_cursor
  new_cursor="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  local repo_name
  local processed_count=0
  local error_count=0

  while IFS= read -r repo_name; do
    [[ -n "$repo_name" && "$repo_name" != "null" ]] || continue

    local repo_config
    repo_config="$(get_repository_config "$repo_name" 2>/dev/null || echo "null")"

    # Check if this repo has the github-tasks poller configured.
    local has_poller
    has_poller="$(echo "$repo_config" | jq -r '
      if (.pollers // []) | map(select(. == "github-tasks")) | length > 0
      then "true" else "false" end' 2>/dev/null || echo "false")"

    [[ "$has_poller" == "true" ]] || continue

    # Resolve the GitHub repo slug (owner/repo) from the clone URL.
    local repo_url
    repo_url="$(echo "$repo_config" | jq -r '.url // ""')"
    local repo_slug
    repo_slug="$(_url_to_slug "$repo_url")"

    if [[ -z "$repo_slug" ]]; then
      _gt_log "warn" "Cannot determine GitHub slug for repo=$repo_name url=$repo_url"
      continue
    fi

    _gt_log "info" "Polling issues for repo=$repo_slug since=$cursor"

    # Use --search to ask GitHub to pre-filter by update time, avoiding
    # re-fetching the entire issue list on every poll cycle.
    # The task_exists() idempotency check below is still the authoritative
    # guard; the search filter is a best-effort bandwidth optimisation.
    local issues_json
    if ! issues_json="$(gh issue list \
        --repo "$repo_slug" \
        --label "agent-task" \
        --state "open" \
        --search "updated:>${cursor}" \
        --json "number,title,labels,body,createdAt,updatedAt,url" \
        --limit 100 2>/dev/null)"; then
      _gt_log "error" "gh issue list failed for repo=$repo_slug"
      (( error_count++ )) || true
      continue
    fi

    local issue_count
    issue_count="$(echo "$issues_json" | jq 'length' 2>/dev/null || echo "0")"
    _gt_log "info" "Found $issue_count open agent-task issues in $repo_slug"

    local i
    for (( i = 0; i < issue_count; i++ )); do
      local issue_json
      issue_json="$(echo "$issues_json" | jq ".[$i]")"

      if process_issue "$issue_json" "$repo_name" "$repo_slug"; then
        (( processed_count++ )) || true
      fi
    done

  done < <(get_repositories 2>/dev/null)

  # Update poll state with new cursor timestamp.
  local state_data
  state_data="$(jq -n \
    --arg processed "$processed_count" \
    --arg errors "$error_count" \
    '{"last_processed": ($processed | tonumber), "last_errors": ($errors | tonumber)}')"

  update_poll_state "github-tasks" "$new_cursor" "$state_data"

  _gt_log "info" "github-tasks poll cycle complete: processed=$processed_count errors=$error_count"
}

# ── Public: process_issue ─────────────────────────────────────────────────────
# Process a single GitHub issue JSON object.
# Creates a task if this issue has not already been seen.
#
# Arguments:
#   $1  issue_json   — JSON object from gh issue list
#   $2  repo_name    — internal repository name (from config)
#   $3  repo_slug    — GitHub owner/repo slug
#
# Returns 0 if a new task was created, 1 if skipped.
process_issue() {
  local issue_json="$1"
  local repo_name="$2"
  local repo_slug="$3"

  local issue_number issue_title issue_body issue_url
  issue_number="$(echo "$issue_json" | jq -r '.number')"
  issue_title="$(echo "$issue_json" | jq -r '.title')"
  issue_body="$(echo "$issue_json" | jq -r '.body // ""')"
  issue_url="$(echo "$issue_json" | jq -r '.url // ""')"

  local task_id="github-issue-${repo_name}-${issue_number}"

  # Idempotency check.
  if task_exists "$task_id" 2>/dev/null; then
    _gt_log "info" "Issue already queued: task=$task_id"
    return 1
  fi

  _gt_log "info" "Processing new issue #${issue_number}: $issue_title"

  # Extract label names.
  local labels_json
  labels_json="$(echo "$issue_json" | jq '[.labels[].name]')"

  # Determine category from labels.
  local category
  category="$(_categorize_issue "$labels_json")"

  # Determine pipeline from labels.
  local pipeline
  pipeline="$(_select_pipeline "$labels_json")"

  # Build initial context.
  local context_json
  context_json="$(jq -n \
    --arg number "$issue_number" \
    --arg title "$issue_title" \
    --arg body "$issue_body" \
    --arg url "$issue_url" \
    --arg repo_slug "$repo_slug" \
    --argjson labels "$labels_json" \
    '{
      github_issue_number: ($number | tonumber),
      title: $title,
      body: $body,
      url: $url,
      repository_slug: $repo_slug,
      labels: $labels
    }')"

  # Create the task.
  if create_task \
      "$task_id" \
      "$issue_title" \
      "$category" \
      "github-issue" \
      "$issue_number" \
      "$repo_name" \
      "$pipeline" \
      "$context_json"; then
    _gt_log "info" "Created task=$task_id category=$category pipeline=$pipeline"
    return 0
  else
    _gt_log "error" "Failed to create task for issue #$issue_number"
    return 1
  fi
}

# ── Private: _categorize_issue ───────────────────────────────────────────────
# Map issue labels to a task category using the configured label mapping.
# Returns the category string.
_categorize_issue() {
  local labels_json="$1"

  # Load label mapping from config.
  local mapping_json
  mapping_json="$(yq -o=json \
    '.spec.pollers[] | select(.name == "github-tasks") | .config.categorization.labelMapping // {}' \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "{}")"

  local default_category
  default_category="$(yq \
    '.spec.pollers[] | select(.name == "github-tasks") | .config.categorization.defaultCategory // "analyze"' \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "analyze")"

  # Check each label against the mapping (first match wins).
  local label
  while IFS= read -r label; do
    [[ -n "$label" && "$label" != "null" ]] || continue
    local mapped
    mapped="$(echo "$mapping_json" | jq -r --arg lbl "$label" '.[$lbl] // ""' 2>/dev/null || echo "")"
    if [[ -n "$mapped" && "$mapped" != "null" ]]; then
      echo "$mapped"
      return 0
    fi
  done < <(echo "$labels_json" | jq -r '.[]' 2>/dev/null)

  echo "$default_category"
}

# ── Private: _select_pipeline ─────────────────────────────────────────────────
# Choose a pipeline name based on issue labels.
# Returns the pipeline name string.
_select_pipeline() {
  local labels_json="$1"

  local pipeline_count
  pipeline_count="$(yq '[.spec.pipelines[]] | length' "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "0")"

  local i
  for (( i = 0; i < pipeline_count; i++ )); do
    local trigger_labels
    trigger_labels="$(yq -o=json \
      ".spec.pipelines[${i}].trigger.labels // []" \
      "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "[]")"

    # Check if any issue label matches a trigger label.
    local match
    match="$(jq -n \
      --argjson issue_labels "$labels_json" \
      --argjson trigger_labels "$trigger_labels" \
      '($issue_labels | map(. as $il | $trigger_labels[] | select(. == $il)) | length) > 0' \
      2>/dev/null || echo "false")"

    if [[ "$match" == "true" ]]; then
      local pipeline_name
      pipeline_name="$(yq ".spec.pipelines[${i}].name" "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "")"
      if [[ -n "$pipeline_name" && "$pipeline_name" != "null" ]]; then
        echo "$pipeline_name"
        return 0
      fi
    fi
  done

  # Default to feature-pipeline if no match.
  echo "feature-pipeline"
}

# _url_to_slug is provided by supervisor/lib/utils.sh (sourced at top of file).

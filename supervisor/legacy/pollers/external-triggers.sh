#!/usr/bin/env bash
# supervisor/pollers/external-triggers.sh
# External trigger interface.
#
# Watches a directory for YAML or JSON trigger files dropped by external tools
# (CI systems, humans, other automation). Each file is parsed, a task is
# created, and the file is moved to the processed/ subdirectory.
#
# Trigger file format (YAML or JSON):
#   category:  analyze | implementation | test | design | docs | review
#   title:     Human-readable task title
#   context:   Arbitrary JSON object with task context
#   priority:  0-100 (optional, default 50)
#   labels:    list of label strings (optional)
#   pipeline:  pipeline name (optional, auto-selected if omitted)
#   repository: repository name (required; must match a configured repository)
#
# Dependencies: config.sh, task-queue.sh must be sourced first.
#
# Usage (called by supervisor main loop):
#   source pollers/external-triggers.sh
#   poll_external_triggers

set -euo pipefail

# ── Logging ───────────────────────────────────────────────────────────────────

_et_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '{"ts":"%s","level":"%s","component":"external-triggers-poller","msg":"%s"}\n' \
    "$ts" "$level" "$*" >&2
}

# ── Public: poll_external_triggers ───────────────────────────────────────────
# Scan the configured trigger watch directory for new YAML/JSON files
# and process each one.
#
# Usage: poll_external_triggers
poll_external_triggers() {
  local watch_dir="${CFG_TRIGGERS_WATCH_DIR:-/var/lib/aquarco/triggers}"
  local processed_dir="${CFG_TRIGGERS_PROCESSED_DIR:-/var/lib/aquarco/triggers/processed}"

  _et_log "info" "Scanning trigger directory: $watch_dir"

  if [[ ! -d "$watch_dir" ]]; then
    _et_log "warn" "Trigger watch directory does not exist: $watch_dir"
    return 0
  fi

  # Ensure the processed directory exists.
  if [[ ! -d "$processed_dir" ]]; then
    if ! mkdir -p "$processed_dir" 2>/dev/null; then
      _et_log "error" "Cannot create processed directory: $processed_dir"
      return 1
    fi
  fi

  local processed_count=0
  local error_count=0
  local trigger_file

  # Process YAML files.
  for trigger_file in "${watch_dir}"/*.yaml "${watch_dir}"/*.yml "${watch_dir}"/*.json; do
    # Skip glob expansion failures (no matching files).
    [[ -f "$trigger_file" ]] || continue
    # Skip files in subdirectories (already processed).
    [[ "$(dirname "$trigger_file")" == "$watch_dir" ]] || continue

    _et_log "info" "Found trigger file: $trigger_file"

    if _process_trigger_file "$trigger_file" "$processed_dir"; then
      (( processed_count++ )) || true
    else
      (( error_count++ )) || true
    fi
  done

  if [[ "$processed_count" -gt 0 || "$error_count" -gt 0 ]]; then
    _et_log "info" "External triggers cycle complete: processed=$processed_count errors=$error_count"
  fi

  local new_cursor
  new_cursor="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  local state_data
  state_data="$(jq -n \
    --arg p "$processed_count" \
    --arg e "$error_count" \
    '{"last_processed": ($p|tonumber), "last_errors": ($e|tonumber)}')"

  update_poll_state "external-triggers" "$new_cursor" "$state_data" 2>/dev/null || true
}

# ── Private: _process_trigger_file ───────────────────────────────────────────
# Parse and process a single trigger file.
#
# Arguments:
#   $1  trigger_file    — absolute path to the trigger file
#   $2  processed_dir   — directory to move processed files into
#
# Returns 0 on success, 1 on error.
_process_trigger_file() {
  local trigger_file="$1"
  local processed_dir="$2"

  local basename
  basename="$(basename "$trigger_file")"
  local extension="${basename##*.}"

  # Parse the trigger file into a JSON object for uniform handling.
  local trigger_json
  case "$extension" in
    yaml|yml)
      if ! command -v yq &>/dev/null; then
        _et_log "error" "yq not found; cannot parse YAML trigger file: $trigger_file"
        return 1
      fi
      if ! trigger_json="$(yq -o=json '.' "$trigger_file" 2>/dev/null)"; then
        _et_log "error" "Failed to parse YAML trigger file: $trigger_file"
        _move_to_failed "$trigger_file" "$processed_dir" "yaml-parse-error"
        return 1
      fi
      ;;
    json)
      if ! trigger_json="$(jq '.' "$trigger_file" 2>/dev/null)"; then
        _et_log "error" "Failed to parse JSON trigger file: $trigger_file"
        _move_to_failed "$trigger_file" "$processed_dir" "json-parse-error"
        return 1
      fi
      ;;
    *)
      _et_log "warn" "Unknown trigger file extension: $extension — skipping $trigger_file"
      return 1
      ;;
  esac

  # Validate required fields.
  local category title repository
  category="$(echo "$trigger_json" | jq -r '.category // ""')"
  title="$(echo "$trigger_json" | jq -r '.title // ""')"
  repository="$(echo "$trigger_json" | jq -r '.repository // ""')"

  if [[ -z "$category" || "$category" == "null" ]]; then
    _et_log "error" "Trigger file missing 'category': $trigger_file"
    _move_to_failed "$trigger_file" "$processed_dir" "missing-category"
    return 1
  fi

  if [[ -z "$title" || "$title" == "null" ]]; then
    _et_log "error" "Trigger file missing 'title': $trigger_file"
    _move_to_failed "$trigger_file" "$processed_dir" "missing-title"
    return 1
  fi

  if [[ -z "$repository" || "$repository" == "null" ]]; then
    _et_log "error" "Trigger file missing 'repository': $trigger_file"
    _move_to_failed "$trigger_file" "$processed_dir" "missing-repository"
    return 1
  fi

  # Validate category value.
  local valid_categories=("review" "implementation" "test" "design" "docs" "analyze")
  local is_valid_category=false
  local valid_cat
  for valid_cat in "${valid_categories[@]}"; do
    if [[ "$category" == "$valid_cat" ]]; then
      is_valid_category=true
      break
    fi
  done

  if [[ "$is_valid_category" == "false" ]]; then
    _et_log "error" "Invalid category '$category' in trigger file: $trigger_file"
    _move_to_failed "$trigger_file" "$processed_dir" "invalid-category"
    return 1
  fi

  # Extract optional fields.
  local priority pipeline labels_json context_json source_ref
  priority="$(echo "$trigger_json" | jq -r '.priority // 50')"
  pipeline="$(echo "$trigger_json" | jq -r '.pipeline // ""')"
  labels_json="$(echo "$trigger_json" | jq '.labels // []' 2>/dev/null || echo "[]")"
  context_json="$(echo "$trigger_json" | jq '.context // {}' 2>/dev/null || echo "{}")"
  source_ref="$(echo "$trigger_json" | jq -r '.source_ref // ""')"

  # Auto-select pipeline if not specified.
  if [[ -z "$pipeline" || "$pipeline" == "null" ]]; then
    pipeline="$(_select_pipeline_for_category "$category")"
  fi

  # Generate a deterministic task ID from file content hash.
  local file_hash
  file_hash="$(sha256sum "$trigger_file" 2>/dev/null | cut -c1-16 || \
               shasum -a 256 "$trigger_file" 2>/dev/null | cut -c1-16 || \
               echo "$(date +%s%N)")"

  local task_id="external-${repository}-${file_hash}"

  # Idempotency check.
  if task_exists "$task_id" 2>/dev/null; then
    _et_log "info" "Trigger already queued: task=$task_id file=$basename"
    _move_to_processed "$trigger_file" "$processed_dir"
    return 0
  fi

  # Enrich context with trigger metadata.
  local enriched_context
  enriched_context="$(echo "$context_json" | jq \
    --arg file "$basename" \
    --arg ts "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    --argjson labels "$labels_json" \
    '. + {"_trigger_file": $file, "_triggered_at": $ts, "_labels": $labels}')"

  # Create the task.
  local create_result=0
  if create_task \
      "$task_id" \
      "$title" \
      "$category" \
      "external" \
      "${source_ref:-$basename}" \
      "$repository" \
      "$pipeline" \
      "$enriched_context"; then
    _et_log "info" "Created task=$task_id from trigger file=$basename"
  else
    _et_log "error" "Failed to create task from trigger file=$basename"
    _move_to_failed "$trigger_file" "$processed_dir" "task-creation-failed"
    return 1
  fi

  # Move the file to the processed directory.
  _move_to_processed "$trigger_file" "$processed_dir"
  return 0
}

# ── Private: _move_to_processed ───────────────────────────────────────────────
# Move a trigger file to the processed/ directory with a timestamp prefix.
_move_to_processed() {
  local trigger_file="$1"
  local processed_dir="$2"

  local basename
  basename="$(basename "$trigger_file")"
  local ts
  ts="$(date -u +"%Y%m%dT%H%M%SZ")"
  local dest="${processed_dir}/${ts}-${basename}"

  if mv "$trigger_file" "$dest" 2>/dev/null; then
    _et_log "info" "Moved processed trigger file to $dest"
  else
    _et_log "warn" "Could not move trigger file to processed: $trigger_file -> $dest"
  fi
}

# ── Private: _move_to_failed ──────────────────────────────────────────────────
# Move a failed trigger file to a failed/ subdirectory with an error suffix.
_move_to_failed() {
  local trigger_file="$1"
  local processed_dir="$2"
  local reason="$3"

  local failed_dir="${processed_dir}/failed"
  mkdir -p "$failed_dir" 2>/dev/null || true

  local basename
  basename="$(basename "$trigger_file")"
  local ts
  ts="$(date -u +"%Y%m%dT%H%M%SZ")"
  local dest="${failed_dir}/${ts}-${reason}-${basename}"

  if mv "$trigger_file" "$dest" 2>/dev/null; then
    _et_log "warn" "Moved failed trigger file to $dest"
  else
    _et_log "warn" "Could not move failed trigger file: $trigger_file"
  fi
}

# ── Private: _select_pipeline_for_category ───────────────────────────────────
# Choose a default pipeline for a given task category.
_select_pipeline_for_category() {
  local category="$1"

  case "$category" in
    analyze|design|implementation|docs)
      echo "feature-pipeline"
      ;;
    test|review)
      echo "pr-review-pipeline"
      ;;
    *)
      echo "feature-pipeline"
      ;;
  esac
}

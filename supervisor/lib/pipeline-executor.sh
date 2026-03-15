#!/usr/bin/env bash
# supervisor/lib/pipeline-executor.sh
# Pipeline execution engine.
#
# Chains pipeline stages in sequence: each stage selects and invokes an
# agent, passes accumulated context forward, and checkpoints progress so
# execution can resume after an unexpected shutdown.
#
# Dependencies: config.sh, task-queue.sh, agent-registry.sh must be sourced first.
#
# Usage:
#   source pipeline-executor.sh
#   execute_pipeline "feature-pipeline" "github-issue-42" "$context_json"

set -euo pipefail

# Source shared utilities (provides _tq_escape).
_PE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./utils.sh
source "${_PE_LIB_DIR}/utils.sh"

# ── Globals ───────────────────────────────────────────────────────────────────

PIPELINE_CONTEXT_DIR="${PIPELINE_CONTEXT_DIR:-/tmp/aifishtank/pipeline-context}"

# ── Logging shim ──────────────────────────────────────────────────────────────

_pe_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '{"ts":"%s","level":"%s","component":"pipeline-executor","msg":"%s"}\n' \
    "$ts" "$level" "$*" >&2
}

# ── Public: execute_pipeline ─────────────────────────────────────────────────
# Run all stages of a named pipeline for a given task.
#
# Arguments:
#   $1  pipeline_name  — e.g. "feature-pipeline"
#   $2  task_id        — task identifier
#   $3  context        — initial context JSON string
#
# Returns: 0 on complete success, 1 if any required stage failed.
execute_pipeline() {
  local pipeline_name="$1"
  local task_id="$2"
  local context="$3"

  _pe_log "info" "Starting pipeline=$pipeline_name task=$task_id"

  # Determine the start stage (supports resume).
  local start_stage=0
  local resume_checkpoint
  resume_checkpoint="$(_get_checkpoint "$task_id" 2>/dev/null || echo "")"
  if [[ -n "$resume_checkpoint" && "$resume_checkpoint" != "null" ]]; then
    local last_completed
    last_completed="$(echo "$resume_checkpoint" | jq -r '.last_completed_stage // -1')"
    start_stage=$(( last_completed + 1 ))
    _pe_log "info" "Resuming pipeline from stage=$start_stage (last_completed=$last_completed)"
  fi

  # Load pipeline stage definitions from config.
  local stage_count
  stage_count="$(yq "[.spec.pipelines[] | select(.name == \"${pipeline_name}\") | .stages[]] | length" \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "0")"

  if [[ "$stage_count" -eq 0 ]]; then
    _pe_log "error" "Pipeline not found or has no stages: $pipeline_name"
    return 1
  fi

  local previous_output="{}"
  local stage_num
  local overall_status=0

  for (( stage_num = 0; stage_num < stage_count; stage_num++ )); do
    # Skip already-completed stages when resuming.
    if [[ "$stage_num" -lt "$start_stage" ]]; then
      _pe_log "info" "Skipping already-completed stage $stage_num (resume)"
      continue
    fi

    local category required conditions
    category="$(yq ".spec.pipelines[] | select(.name == \"${pipeline_name}\") | .stages[${stage_num}].category" \
      "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "")"
    required="$(yq ".spec.pipelines[] | select(.name == \"${pipeline_name}\") | .stages[${stage_num}].required // true" \
      "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "true")"
    conditions="$(yq -o=json ".spec.pipelines[] | select(.name == \"${pipeline_name}\") | .stages[${stage_num}].conditions // []" \
      "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "[]")"

    if [[ -z "$category" || "$category" == "null" ]]; then
      _pe_log "warn" "Stage $stage_num has no category; skipping"
      continue
    fi

    # Evaluate stage conditions.
    if ! check_conditions "$conditions" "$previous_output"; then
      _pe_log "info" "Stage $stage_num ($category) conditions not met; skipping"

      if [[ "$required" == "true" ]]; then
        _pe_log "error" "Required stage $stage_num ($category) skipped due to unmet conditions"
        fail_task "$task_id" "Required stage $stage_num ($category) conditions not met"
        return 1
      fi

      # Record as skipped.
      _record_stage_skipped "$task_id" "$stage_num" "$category"
      continue
    fi

    # Build accumulated context from database + previous output.
    local accumulated_context
    accumulated_context="$(build_accumulated_context "$task_id" "$stage_num" "$previous_output")"

    # Execute the stage.
    local stage_output
    if stage_output="$(execute_stage "$category" "$task_id" "$accumulated_context" "$previous_output" "$stage_num")"; then
      previous_output="$stage_output"
      store_stage_output "$task_id" "$stage_num" "$category" \
        "$(echo "$stage_output" | jq -r '._agent_name // "unknown"')" \
        "$stage_output"
      checkpoint_pipeline "$task_id" "$stage_num"
      _pe_log "info" "Stage $stage_num ($category) completed for task=$task_id"
    else
      _pe_log "error" "Stage $stage_num ($category) failed for task=$task_id"

      if [[ "$required" == "true" ]]; then
        fail_task "$task_id" "Stage $stage_num ($category) failed"
        overall_status=1
        break
      else
        _pe_log "warn" "Optional stage $stage_num ($category) failed; continuing pipeline"
        _record_stage_skipped "$task_id" "$stage_num" "$category"
      fi
    fi
  done

  if [[ "$overall_status" -eq 0 ]]; then
    complete_task "$task_id"
    _pe_log "info" "Pipeline=$pipeline_name completed for task=$task_id"
    # Remove checkpoint on clean completion.
    _delete_checkpoint "$task_id"
  fi

  return "$overall_status"
}

# ── Public: execute_stage ────────────────────────────────────────────────────
# Run a single pipeline stage by selecting and invoking an agent.
#
# Arguments:
#   $1  category        — e.g. "analyze"
#   $2  task_id         — task identifier
#   $3  context         — accumulated context JSON string
#   $4  previous_output — JSON output from the previous stage
#   $5  stage_num       — 0-based stage index (for DB recording)
#
# Outputs the structured agent output JSON to stdout.
# Returns 0 on success, 1 on failure.
execute_stage() {
  local category="$1"
  local task_id="$2"
  local context="$3"
  local previous_output="$4"
  local stage_num="${5:-0}"

  _pe_log "info" "Executing stage: category=$category task=$task_id stage=$stage_num"

  # Select the best available agent.
  local agent_name
  if ! agent_name="$(select_agent "$category")"; then
    _pe_log "error" "No available agent for category=$category"
    return 1
  fi

  # Mark stage as executing in DB.
  _record_stage_executing "$task_id" "$stage_num" "$category" "$agent_name"

  # Claim concurrency slot.
  increment_agent_instances "$agent_name"

  local stage_output
  local exec_status=0
  if stage_output="$(execute_agent "$agent_name" "$task_id" "$context" "$previous_output" "$stage_num")"; then
    _pe_log "info" "Agent=$agent_name completed stage=$stage_num task=$task_id"
  else
    exec_status=$?
    _pe_log "error" "Agent=$agent_name failed stage=$stage_num task=$task_id"
  fi

  # Release concurrency slot.
  decrement_agent_instances "$agent_name"

  if [[ "$exec_status" -ne 0 ]]; then
    _record_stage_failed "$task_id" "$stage_num" "Agent execution failed"
    return 1
  fi

  echo "$stage_output"
  return 0
}

# ── Public: execute_agent ─────────────────────────────────────────────────────
# Invoke the claude CLI with the agent's system prompt and context.
#
# The context is written to a temporary file and fed via stdin.
# The agent's structured JSON output is extracted from stdout.
#
# Arguments:
#   $1  agent_name      — e.g. "analyze-agent"
#   $2  task_id         — task identifier (for logging/context)
#   $3  context         — context JSON string
#   $4  previous_output — previous stage output JSON
#   $5  stage_num       — 0-based stage index
#
# Outputs structured JSON to stdout.
execute_agent() {
  local agent_name="$1"
  local task_id="$2"
  local context="$3"
  local previous_output="$4"
  local stage_num="${5:-0}"

  # Resolve the prompt file.
  local prompt_file
  prompt_file="$(get_agent_prompt_file "$agent_name")"

  if [[ ! -f "$prompt_file" ]]; then
    _pe_log "error" "Prompt file not found for agent=$agent_name: $prompt_file"
    return 1
  fi

  # Get timeout for this agent.
  local timeout_minutes
  timeout_minutes="$(get_agent_timeout "$agent_name")"
  local timeout_seconds=$(( timeout_minutes * 60 ))

  # Prepare context directory and file.
  mkdir -p "$PIPELINE_CONTEXT_DIR"
  local context_file
  context_file="$(mktemp "${PIPELINE_CONTEXT_DIR}/ctx-${task_id}-stage${stage_num}-XXXXXX.json")"

  # Build the full context document for the agent.
  local agent_context
  agent_context="$(jq -n \
    --arg task_id "$task_id" \
    --arg agent "$agent_name" \
    --arg stage "$stage_num" \
    --argjson ctx "$(echo "$context" | jq '.' 2>/dev/null || echo '{}')" \
    --argjson prev "$(echo "$previous_output" | jq '.' 2>/dev/null || echo '{}')" \
    '{
      task_id: $task_id,
      agent: $agent,
      stage_number: ($stage | tonumber),
      accumulated_context: $ctx,
      previous_stage_output: $prev
    }')"

  echo "$agent_context" > "$context_file"

  _pe_log "info" "Invoking agent=$agent_name prompt=$prompt_file timeout=${timeout_minutes}m task=$task_id"

  local raw_output
  local exit_code=0

  # Run the claude CLI. The prompt file is the agent's system prompt;
  # the context JSON is piped on stdin.
  if ! raw_output="$(timeout "${timeout_seconds}" \
      claude --print --system-prompt "$prompt_file" < "$context_file" 2>&1)"; then
    exit_code=$?
    _pe_log "error" "claude CLI exited with code=$exit_code for agent=$agent_name task=$task_id"
    rm -f "$context_file"
    return 1
  fi

  rm -f "$context_file"

  # Extract the structured JSON block from the agent output.
  # Agents are expected to emit a JSON code block or a raw JSON object.
  local structured_output
  structured_output="$(_extract_json_output "$raw_output")"

  if [[ -z "$structured_output" || "$structured_output" == "null" ]]; then
    _pe_log "warn" "Agent=$agent_name produced no structured JSON; wrapping raw output"
    structured_output="$(jq -n --arg raw "$raw_output" --arg agent "$agent_name" \
      '{"_agent_name": $agent, "_raw_output": $raw, "_no_structured_output": true}')"
  else
    # Inject agent name for stage recording.
    structured_output="$(echo "$structured_output" | jq --arg agent "$agent_name" \
      '. + {"_agent_name": $agent}')"
  fi

  echo "$structured_output"
  return 0
}

# ── Public: check_conditions ─────────────────────────────────────────────────
# Evaluate stage conditions against the previous stage's output.
#
# Supported condition syntax:
#   "analysis.complexity >= medium"   — compare field to threshold
#   "analysis.estimated_complexity == high"
#
# Arguments:
#   $1  conditions_json  — JSON array of condition strings
#   $2  previous_output  — JSON output from the previous stage
#
# Returns 0 if all conditions pass (or conditions array is empty), 1 otherwise.
check_conditions() {
  local conditions_json="$1"
  local previous_output="$2"

  # No conditions — always pass.
  local condition_count
  condition_count="$(echo "$conditions_json" | jq 'length' 2>/dev/null || echo "0")"
  if [[ "$condition_count" -eq 0 ]]; then
    return 0
  fi

  local complexity_order=("trivial" "low" "medium" "high" "epic")

  local i
  for (( i = 0; i < condition_count; i++ )); do
    local condition
    condition="$(echo "$conditions_json" | jq -r ".[${i}]")"

    # Parse "field operator value" expressions.
    local field operator value
    if ! read -r field operator value <<< "$condition"; then
      _pe_log "warn" "Could not parse condition: $condition"
      continue
    fi

    # Resolve the field value from previous_output.
    # Convert "analysis.complexity" -> ".analysis.complexity"
    local jq_path=".${field}"
    local actual_value
    actual_value="$(echo "$previous_output" | jq -r "$jq_path // \"\"" 2>/dev/null || echo "")"

    if [[ -z "$actual_value" ]]; then
      _pe_log "info" "Condition field '$field' not found in previous output; treating as false"
      return 1
    fi

    # Evaluate the condition.
    case "$operator" in
      "=="|"=")
        [[ "$actual_value" == "$value" ]] || return 1
        ;;
      "!=")
        [[ "$actual_value" != "$value" ]] || return 1
        ;;
      ">=")
        if ! _complexity_gte "$actual_value" "$value"; then
          return 1
        fi
        ;;
      ">")
        if ! _complexity_gt "$actual_value" "$value"; then
          return 1
        fi
        ;;
      "<=")
        if ! _complexity_lte "$actual_value" "$value"; then
          return 1
        fi
        ;;
      "<")
        if ! _complexity_lt "$actual_value" "$value"; then
          return 1
        fi
        ;;
      *)
        _pe_log "warn" "Unknown condition operator '$operator' in: $condition"
        ;;
    esac
  done

  return 0
}

# ── Public: build_accumulated_context ────────────────────────────────────────
# Assemble the context bundle for the current stage.
# Strategy: full initial context + summaries of earlier stages (if any) +
# full output of the immediately preceding stage.
#
# Arguments:
#   $1  task_id        — task identifier
#   $2  current_stage  — 0-based index of the stage about to run
#   $3  previous_output — the immediately previous stage's full output
#
# Outputs the context JSON to stdout.
build_accumulated_context() {
  local task_id="$1"
  local current_stage="$2"
  local previous_output="${3:-{}}"

  _pe_log "info" "Building context for task=$task_id stage=$current_stage"

  # Fetch the full task context document from the DB.
  local db_context
  db_context="$(get_task_context "$task_id" 2>/dev/null || echo "null")"

  if [[ "$db_context" == "null" ]]; then
    # Minimal fallback context.
    jq -n \
      --arg tid "$task_id" \
      --arg stage "$current_stage" \
      --argjson prev "$(echo "$previous_output" | jq '.' 2>/dev/null || echo '{}')" \
      '{"task_id": $tid, "current_stage": ($stage|tonumber), "previous_output": $prev}'
    return 0
  fi

  # Build the context bundle.
  # For stages 0 and 1, include all previous output in full.
  # For later stages, include summaries plus the N-1 full output.
  local include_full_threshold=2

  local accumulated
  accumulated="$(echo "$db_context" | jq \
    --argjson threshold "$include_full_threshold" \
    --argjson current "$current_stage" \
    --argjson prev "$(echo "$previous_output" | jq '.' 2>/dev/null || echo '{}')" \
    '{
      task: .task,
      current_stage: $current,
      previous_output: $prev,
      stage_history: [
        .stages[]
        | select(.stage_number < $current)
        | if .stage_number >= ($current - $threshold)
          then .
          else {
            stage_number: .stage_number,
            category: .category,
            agent: .agent,
            status: .status,
            summary: (.structured_output.summary // .structured_output.issue_summary // "completed")
          }
          end
      ],
      context_entries: .context
    }')"

  echo "$accumulated"
}

# ── Public: checkpoint_pipeline ──────────────────────────────────────────────
# Save a checkpoint recording the last successfully completed stage.
# Allows the pipeline to resume cleanly after a crash.
#
# Usage: checkpoint_pipeline "github-issue-42" 2
checkpoint_pipeline() {
  local task_id="$1"
  local stage_num="$2"

  _pe_log "info" "Checkpointing task=$task_id at stage=$stage_num"

  local checkpoint_data
  checkpoint_data="$(jq -n \
    --arg ts "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    --arg workdir "${CFG_WORKDIR:-/home/agent/ai-fishtank}" \
    '{"checkpointed_at": $ts, "workdir": $workdir}')"

  local e_task_id e_checkpoint
  e_task_id="$(_tq_escape "$task_id")"
  e_checkpoint="$(_tq_escape "$checkpoint_data")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
INSERT INTO pipeline_checkpoints (task_id, last_completed_stage, checkpoint_data, created_at)
VALUES (
  \$tq\$${e_task_id}\$tq\$,
  ${stage_num},
  \$tq\$${e_checkpoint}\$tq\$::jsonb,
  NOW()
)
ON CONFLICT (task_id) DO UPDATE
  SET last_completed_stage = EXCLUDED.last_completed_stage,
      checkpoint_data      = EXCLUDED.checkpoint_data,
      created_at           = NOW();
SQL
)"

  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?}" -c "$sql" &>/dev/null || \
    _pe_log "warn" "Failed to write checkpoint for task=$task_id stage=$stage_num"
}

# ── Public: resume_pipeline ───────────────────────────────────────────────────
# Resume a previously interrupted pipeline from its last checkpoint.
#
# Usage: resume_pipeline "github-issue-42"
resume_pipeline() {
  local task_id="$1"

  _pe_log "info" "Resuming pipeline for task=$task_id"

  local task_json
  task_json="$(get_task "$task_id")"

  if [[ "$task_json" == "null" ]]; then
    _pe_log "error" "Cannot resume: task=$task_id not found"
    return 1
  fi

  local pipeline_name repository
  pipeline_name="$(echo "$task_json" | jq -r '.pipeline // ""')"
  repository="$(echo "$task_json" | jq -r '.repository // ""')"

  if [[ -z "$pipeline_name" || "$pipeline_name" == "null" ]]; then
    _pe_log "error" "Cannot resume: task=$task_id has no pipeline set"
    return 1
  fi

  local initial_context
  initial_context="$(echo "$task_json" | jq '.initial_context // {}' 2>/dev/null || echo '{}')"

  # execute_pipeline will read the checkpoint and skip completed stages.
  update_task_status "$task_id" "executing"
  execute_pipeline "$pipeline_name" "$task_id" "$initial_context"
}

# ── Private: _extract_json_output ────────────────────────────────────────────
# Extract a JSON object from raw claude CLI output.
# Looks for a ```json ... ``` code block first, then falls back to finding
# the first top-level JSON object in the output.
_extract_json_output() {
  local raw_output="$1"

  # Try to extract from ```json ... ``` code block.
  local json_block
  if json_block="$(echo "$raw_output" | awk '/^```json/{f=1;next}/^```/{if(f)exit;f=0}f' 2>/dev/null)"; then
    if [[ -n "$json_block" ]] && echo "$json_block" | jq '.' &>/dev/null; then
      echo "$json_block"
      return 0
    fi
  fi

  # Fall back: find first complete JSON object on a line by itself.
  local line
  while IFS= read -r line; do
    if echo "$line" | jq '.' &>/dev/null 2>&1; then
      local first_char="${line:0:1}"
      if [[ "$first_char" == "{" || "$first_char" == "[" ]]; then
        echo "$line"
        return 0
      fi
    fi
  done <<< "$raw_output"

  echo ""
  return 1
}

# ── Private: _get_checkpoint ─────────────────────────────────────────────────
_get_checkpoint() {
  local task_id="$1"

  local e_task_id
  e_task_id="$(_tq_escape "$task_id")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
SELECT row_to_json(pc)
FROM (
  SELECT task_id, last_completed_stage, checkpoint_data, created_at
  FROM   pipeline_checkpoints
  WHERE  task_id = \$tq\$${e_task_id}\$tq\$
) pc;
SQL
)"

  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?}" -c "$sql" 2>/dev/null | tr -d '[:space:]' || echo ""
}

# ── Private: _delete_checkpoint ───────────────────────────────────────────────
_delete_checkpoint() {
  local task_id="$1"

  local e_task_id
  e_task_id="$(_tq_escape "$task_id")"

  local sql
  sql="SET search_path TO aifishtank, public; DELETE FROM pipeline_checkpoints WHERE task_id = \$tq\$${e_task_id}\$tq\$;"
  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?}" -c "$sql" &>/dev/null || true
}

# ── Private: _record_stage_executing ─────────────────────────────────────────
_record_stage_executing() {
  local task_id="$1" stage_num="$2" category="$3" agent="$4"

  local e_task_id e_category e_agent
  e_task_id="$(_tq_escape "$task_id")"
  e_category="$(_tq_escape "$category")"
  e_agent="$(_tq_escape "$agent")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
INSERT INTO stages (task_id, stage_number, category, agent, status, started_at)
VALUES (\$tq\$${e_task_id}\$tq\$, ${stage_num}, \$tq\$${e_category}\$tq\$, \$tq\$${e_agent}\$tq\$, 'executing', NOW())
ON CONFLICT (task_id, stage_number) DO UPDATE
  SET agent      = EXCLUDED.agent,
      status     = 'executing',
      started_at = NOW();
SQL
)"
  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?}" -c "$sql" &>/dev/null || true
}

# ── Private: _record_stage_failed ────────────────────────────────────────────
_record_stage_failed() {
  local task_id="$1" stage_num="$2" error_msg="$3"

  local e_task_id e_error_msg
  e_task_id="$(_tq_escape "$task_id")"
  e_error_msg="$(_tq_escape "$error_msg")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
UPDATE stages
SET    status        = 'failed',
       completed_at  = NOW(),
       error_message = \$tq\$${e_error_msg}\$tq\$
WHERE  task_id      = \$tq\$${e_task_id}\$tq\$
  AND  stage_number = ${stage_num};
SQL
)"
  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?}" -c "$sql" &>/dev/null || true
}

# ── Private: _record_stage_skipped ───────────────────────────────────────────
_record_stage_skipped() {
  local task_id="$1" stage_num="$2" category="$3"

  local e_task_id e_category
  e_task_id="$(_tq_escape "$task_id")"
  e_category="$(_tq_escape "$category")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
INSERT INTO stages (task_id, stage_number, category, status, started_at, completed_at)
VALUES (\$tq\$${e_task_id}\$tq\$, ${stage_num}, \$tq\$${e_category}\$tq\$, 'skipped', NOW(), NOW())
ON CONFLICT (task_id, stage_number) DO UPDATE
  SET status       = 'skipped',
      completed_at = NOW();
SQL
)"
  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?}" -c "$sql" &>/dev/null || true
}

# ── Private: complexity comparison helpers ────────────────────────────────────

_complexity_index() {
  local val="$1"
  local levels=("trivial" "low" "medium" "high" "epic")
  local i
  for (( i = 0; i < ${#levels[@]}; i++ )); do
    if [[ "${levels[$i]}" == "$val" ]]; then
      echo "$i"
      return 0
    fi
  done
  echo "-1"
}

_complexity_gte() {
  local a b
  a="$(_complexity_index "$1")"
  b="$(_complexity_index "$2")"
  [[ "$a" -ge "$b" ]] && [[ "$a" -ge 0 ]] && [[ "$b" -ge 0 ]]
}

_complexity_gt() {
  local a b
  a="$(_complexity_index "$1")"
  b="$(_complexity_index "$2")"
  [[ "$a" -gt "$b" ]] && [[ "$a" -ge 0 ]] && [[ "$b" -ge 0 ]]
}

_complexity_lte() {
  local a b
  a="$(_complexity_index "$1")"
  b="$(_complexity_index "$2")"
  [[ "$a" -le "$b" ]] && [[ "$a" -ge 0 ]] && [[ "$b" -ge 0 ]]
}

_complexity_lt() {
  local a b
  a="$(_complexity_index "$1")"
  b="$(_complexity_index "$2")"
  [[ "$a" -lt "$b" ]] && [[ "$a" -ge 0 ]] && [[ "$b" -ge 0 ]]
}

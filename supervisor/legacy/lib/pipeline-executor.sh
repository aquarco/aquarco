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

  # If no pipeline specified, run a single stage matching the task category.
  if [[ -z "$pipeline_name" ]]; then
    _pe_log "info" "No pipeline specified for task=$task_id; running single-stage execution"
    local task_category
    task_category="$(_psql -c "SELECT category FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
    if [[ -z "$task_category" ]]; then
      _pe_log "error" "Cannot determine category for task=$task_id"
      fail_task "$task_id" "No pipeline and no category found"
      return 1
    fi

    local accumulated_context
    if ! accumulated_context="$(build_accumulated_context "$task_id" 0 "{}")"; then
      _pe_log "warn" "build_accumulated_context failed; using minimal context"
      accumulated_context="{\"task_id\":\"$task_id\",\"category\":\"$task_category\"}"
    fi
    if [[ -z "$accumulated_context" ]] || ! echo "$accumulated_context" | jq '.' &>/dev/null; then
      _pe_log "warn" "accumulated_context is invalid JSON; using minimal context"
      accumulated_context="{\"task_id\":\"$task_id\",\"category\":\"$task_category\"}"
    fi
    local stage_output
    if stage_output="$(execute_stage "$task_category" "$task_id" "$accumulated_context" "{}" 0)"; then
      store_stage_output "$task_id" 0 "$task_category" \
        "$(echo "$stage_output" | jq -r '._agent_name // "unknown"')" \
        "$stage_output"

      # Post-execution: create PR if the agent made code changes.
      _maybe_create_pr "$task_id" "$task_category" "$stage_output" || true

      complete_task "$task_id"
      _pe_log "info" "Single-stage execution completed for task=$task_id category=$task_category"
      return 0
    else
      _pe_log "error" "Single-stage execution failed for task=$task_id category=$task_category"
      fail_task "$task_id" "Stage $task_category failed"
      return 1
    fi
  fi

  # Load pipeline stage definitions from config.
  local stage_count
  stage_count="$(yq "[.spec.pipelines[] | select(.name == \"${pipeline_name}\") | .stages[]] | length" \
    "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "0")"

  if [[ "$stage_count" -eq 0 ]]; then
    _pe_log "error" "Pipeline not found or has no stages: $pipeline_name"
    return 1
  fi

  # ── Create all stage records upfront as 'pending' ──────────────────────
  if [[ "$start_stage" -eq 0 ]]; then
    local s_num
    for (( s_num = 0; s_num < stage_count; s_num++ )); do
      local s_category s_required
      s_category="$(yq ".spec.pipelines[] | select(.name == \"${pipeline_name}\") | .stages[${s_num}].category" \
        "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "")"
      s_required="$(yq ".spec.pipelines[] | select(.name == \"${pipeline_name}\") | .stages[${s_num}].required // true" \
        "$SUPERVISOR_CONFIG_FILE" 2>/dev/null || echo "true")"
      [[ -n "$s_category" && "$s_category" != "null" ]] || continue

      local e_tid e_cat
      e_tid="$(_tq_escape "$task_id")"
      e_cat="$(_tq_escape "$s_category")"
      local init_sql
      init_sql="SET search_path TO aifishtank, public;"
      init_sql+=" INSERT INTO stages (task_id, stage_number, category, status)"
      init_sql+=" VALUES (\$tq\$${e_tid}\$tq\$, ${s_num}, \$tq\$${e_cat}\$tq\$, 'pending')"
      init_sql+=" ON CONFLICT (task_id, stage_number) DO NOTHING;"
      psql --no-psqlrc --tuples-only --no-align "${DATABASE_URL:?}" -c "$init_sql" &>/dev/null || true
    done
    _pe_log "info" "Created $stage_count stage records for task=$task_id"
  fi

  # ── Prepare pipeline branch ──────────────────────────────────────────────
  # All agents in this pipeline work on a single shared branch.
  # - Feature/bugfix pipelines: new branch from origin/main
  # - PR review pipelines: checkout the PR's head branch directly
  local pipeline_branch=""
  local clone_dir=""
  local task_repo
  task_repo="$(_psql -c "SELECT repository FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
  if [[ -n "$task_repo" ]]; then
    clone_dir="$(_psql -c "SELECT clone_dir FROM repositories WHERE name = \$tq\$$(_tq_escape "$task_repo")\$tq\$ AND clone_status = 'ready';" 2>/dev/null | tr -d '[:space:]' || echo "")"
    if [[ -n "$clone_dir" && -d "$clone_dir" ]]; then
      local default_branch
      default_branch="$(git -C "$clone_dir" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")"
      git -C "$clone_dir" fetch --quiet origin 2>/dev/null || true

      # Check if this is a PR review task — if so, work on the PR branch directly.
      local pr_head_branch
      pr_head_branch="$(_psql -c "SELECT initial_context::json->>'head_branch' FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"

      if [[ -n "$pr_head_branch" && "$pr_head_branch" != "null" ]]; then
        # PR review pipeline — checkout the PR's branch.
        pipeline_branch="$pr_head_branch"
        git -C "$clone_dir" checkout "$pipeline_branch" 2>/dev/null || \
          git -C "$clone_dir" checkout -b "$pipeline_branch" "origin/$pipeline_branch" 2>/dev/null || true
        git -C "$clone_dir" reset --hard "origin/$pipeline_branch" 2>/dev/null || true
        _pe_log "info" "Checked out PR branch=$pipeline_branch for review pipeline"
      else
        # Feature/bugfix pipeline — create a new branch from main.
        local task_title_slug
        task_title_slug="$(_psql -c "SELECT title FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "task")"
        task_title_slug="$(echo "$task_title_slug" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/--*/-/g; s/^-//; s/-$//' | head -c 40)"
        pipeline_branch="aifishtank/${task_id}/${task_title_slug}"

        git -C "$clone_dir" checkout "$default_branch" 2>/dev/null || true
        git -C "$clone_dir" reset --hard "origin/$default_branch" 2>/dev/null || true
        git -C "$clone_dir" checkout -b "$pipeline_branch" 2>/dev/null || \
          git -C "$clone_dir" checkout "$pipeline_branch" 2>/dev/null || true
        _pe_log "info" "Created pipeline branch=$pipeline_branch from $default_branch"
      fi
    fi
  fi

  local previous_output="{}"
  local stage_num
  local overall_status=0
  local stage_summaries=""

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
      _record_stage_skipped "$task_id" "$stage_num" "$category"
      continue
    fi

    # Ensure we're on the pipeline branch before each stage (agents may switch branches).
    if [[ -n "$clone_dir" && -n "$pipeline_branch" ]]; then
      git -C "$clone_dir" checkout "$pipeline_branch" 2>/dev/null || true
    fi

    # Build accumulated context from database + previous output.
    local accumulated_context
    accumulated_context="$(build_accumulated_context "$task_id" "$stage_num" "$previous_output")"

    # Execute the stage.
    local stage_output
    if stage_output="$(execute_stage "$category" "$task_id" "$accumulated_context" "$previous_output" "$stage_num")"; then
      previous_output="$stage_output"
      local stage_agent
      stage_agent="$(echo "$stage_output" | jq -r '._agent_name // empty' 2>/dev/null || echo "")"
      [[ -n "$stage_agent" ]] || stage_agent="unknown"
      store_stage_output "$task_id" "$stage_num" "$category" "$stage_agent" "$stage_output"
      checkpoint_pipeline "$task_id" "$stage_num"

      # Auto-commit any changes the agent made on the pipeline branch.
      local stage_summary
      stage_summary="$(echo "$stage_output" | jq -r '.summary // empty' 2>/dev/null || echo "")"
      [[ -n "$stage_summary" ]] || stage_summary="Stage $stage_num ($category) completed"
      stage_summaries+="### ${category^} agent\n${stage_summary}\n\n"

      if [[ -n "$clone_dir" && -n "$pipeline_branch" ]]; then
        local has_changes
        has_changes="$(git -C "$clone_dir" status --porcelain 2>/dev/null || echo "")"
        if [[ -n "$has_changes" ]]; then
          git -C "$clone_dir" add -A 2>/dev/null || true
          git -C "$clone_dir" commit -m "${category}: ${stage_summary} [${task_id}]" 2>/dev/null || true
          _pe_log "info" "Committed stage $stage_num ($category) changes on branch=$pipeline_branch"
        fi
      fi

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
    # Create PR if the pipeline branch has commits ahead of the default branch.
    if [[ -n "$clone_dir" && -n "$pipeline_branch" ]]; then
      _create_pipeline_pr "$task_id" "$pipeline_branch" "$clone_dir" "$stage_summaries" || true
    fi

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

  # Resolve the working directory — use the task's repository clone_dir.
  local work_dir=""
  local task_repo
  task_repo="$(_psql -c "SELECT repository FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
  if [[ -n "$task_repo" ]]; then
    local clone_dir
    clone_dir="$(_psql -c "SELECT clone_dir FROM repositories WHERE name = \$tq\$$(_tq_escape "$task_repo")\$tq\$ AND clone_status = 'ready';" 2>/dev/null | tr -d '[:space:]' || echo "")"
    if [[ -n "$clone_dir" && -d "$clone_dir" ]]; then
      work_dir="$clone_dir"
      # Check if we're on a pipeline branch (set up by execute_pipeline).
      # If the current branch is not the default branch, assume the pipeline set it up.
      local current_branch default_branch
      current_branch="$(git -C "$work_dir" branch --show-current 2>/dev/null || echo "")"
      default_branch="$(git -C "$work_dir" symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")"
      if [[ -n "$current_branch" && "$current_branch" != "$default_branch" ]]; then
        _pe_log "info" "Agent will run on pipeline branch=$current_branch in $work_dir"
      else
        # On default branch — single-stage execution; reset to latest.
        git -C "$work_dir" fetch --quiet origin 2>/dev/null || true
        git -C "$work_dir" checkout "$default_branch" 2>/dev/null || true
        git -C "$work_dir" reset --hard "origin/$default_branch" 2>/dev/null || true
        _pe_log "info" "Agent will run on $default_branch (latest) in $work_dir"
      fi
    fi
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
  # Validate JSON inputs first to avoid jq --argjson failures.
  local valid_ctx valid_prev
  valid_ctx="$(echo "$context" | jq -c '.' 2>/dev/null)" || valid_ctx='{}'
  [[ -n "$valid_ctx" ]] || valid_ctx='{}'
  valid_prev="$(echo "$previous_output" | jq -c '.' 2>/dev/null)" || valid_prev='{}'
  [[ -n "$valid_prev" ]] || valid_prev='{}'

  local agent_context
  agent_context="$(jq -n \
    --arg task_id "$task_id" \
    --arg agent "$agent_name" \
    --arg stage "$stage_num" \
    --argjson ctx "$valid_ctx" \
    --argjson prev "$valid_prev" \
    '{
      task_id: $task_id,
      agent: $agent,
      stage_number: ($stage | tonumber),
      accumulated_context: $ctx,
      previous_stage_output: $prev
    }' 2>/dev/null)" || true

  # Guard against empty context (would cause claude --print to hang).
  if [[ -z "$agent_context" ]]; then
    _pe_log "warn" "agent_context is empty; using minimal context"
    agent_context="{\"task_id\":\"$task_id\",\"agent\":\"$agent_name\",\"stage_number\":$stage_num}"
  fi

  echo "$agent_context" > "$context_file"

  _pe_log "info" "Invoking agent=$agent_name prompt=$prompt_file timeout=${timeout_minutes}m context_bytes=$(wc -c < "$context_file") task=$task_id"

  local raw_output
  local exit_code=0

  # Read the system prompt from the file.
  local system_prompt
  system_prompt="$(< "$prompt_file")"

  # Build tool flags from the agent registry.
  local allowed_tools denied_tools
  local -a claude_args=()
  allowed_tools="$(echo "$AGENT_REGISTRY_JSON" \
    | jq -r --arg name "$agent_name" \
      '.agents[] | select(.name == $name) | .tools.allowed // [] | join(" ")' 2>/dev/null || echo "")"
  denied_tools="$(echo "$AGENT_REGISTRY_JSON" \
    | jq -r --arg name "$agent_name" \
      '.agents[] | select(.name == $name) | .tools.denied // [] | join(" ")' 2>/dev/null || echo "")"

  claude_args=(--print --dangerously-skip-permissions --output-format json --max-turns 30 --verbose)
  claude_args+=(--system-prompt "$system_prompt")

  if [[ -n "$allowed_tools" ]]; then
    claude_args+=(--allowedTools "$allowed_tools")
  fi
  if [[ -n "$denied_tools" ]]; then
    claude_args+=(--disallowedTools "$denied_tools")
  fi

  # Run claude in full agentic mode with tool access.
  # Context is piped on stdin as the user prompt.
  # cd to the repo directory so the agent sees the actual codebase.
  # Debug/verbose output goes to a separate log file per invocation.
  local debug_log="/var/log/aifishtank/claude-${task_id}-stage${stage_num}.log"
  if ! raw_output="$(cd "${work_dir:-.}" && timeout "${timeout_seconds}" \
      claude "${claude_args[@]}" < "$context_file" 2>"$debug_log")"; then
    exit_code=$?
    _pe_log "error" "claude CLI exited with code=$exit_code for agent=$agent_name task=$task_id (debug: $debug_log)"
    rm -f "$context_file"
    return 1
  fi

  rm -f "$context_file"

  # Extract structured output from the agent response.
  # With --output-format json, raw_output is a JSON object with "result" field
  # containing the final text response from Claude.
  local final_text
  final_text="$(echo "$raw_output" | jq -r '.result // empty' 2>/dev/null || echo "$raw_output")"

  # Extract the structured JSON block from the final text.
  # Agents are expected to emit a JSON code block or a raw JSON object.
  local structured_output
  structured_output="$(_extract_json_output "$final_text")"

  if [[ -z "$structured_output" || "$structured_output" == "null" ]]; then
    _pe_log "warn" "Agent=$agent_name produced no structured JSON; wrapping raw output"
    structured_output="$(jq -n --arg raw "$final_text" --arg agent "$agent_name" \
      '{"_agent_name": $agent, "_raw_output": $raw, "_no_structured_output": true}')"
  else
    # Inject agent name for stage recording.
    structured_output="$(echo "$structured_output" | jq --arg agent "$agent_name" \
      '. + {"_agent_name": $agent}')"
  fi

  # Save structured output for debugging store failures.
  echo "$structured_output" > "/var/log/aifishtank/agent-output-${task_id}-stage${stage_num}.json" 2>/dev/null || true

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
  local default_prev='{}'
  local previous_output="${3:-$default_prev}"

  _pe_log "info" "Building context for task=$task_id stage=$current_stage"

  # Validate previous_output is valid JSON.
  local valid_prev
  valid_prev="$(echo "$previous_output" | jq -c '.' 2>/dev/null)" || valid_prev='{}'
  [[ -n "$valid_prev" ]] || valid_prev='{}'

  # Fetch the full task context document from the DB.
  local db_context
  db_context="$(get_task_context "$task_id" 2>/dev/null || echo "null")"

  if [[ "$db_context" == "null" || -z "$db_context" ]]; then
    # Minimal fallback context.
    jq -n \
      --arg tid "$task_id" \
      --arg stage "$current_stage" \
      --argjson prev "$valid_prev" \
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
    --argjson prev "$valid_prev" \
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

  psql --no-psqlrc --tuples-only --no-align --quiet \
    "${DATABASE_URL:?}" -c "$sql" 2>/dev/null | { grep -v '^SET$' || true; } | tr -d '[:space:]' || echo ""
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

# ── Private: _create_pipeline_pr ──────────────────────────────────────────────
# Push the pipeline branch and create a PR with collected stage summaries.
#
# Arguments:
#   $1  task_id         — task identifier
#   $2  branch_name     — pipeline branch name (aifishtank/<task_id>/...)
#   $3  clone_dir       — path to the repository clone
#   $4  stage_summaries — collected markdown summaries from all stages
_create_pipeline_pr() {
  local task_id="$1"
  local branch_name="$2"
  local clone_dir="$3"
  local stage_summaries="$4"

  cd "$clone_dir" || return 0

  local default_branch
  default_branch="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")"

  # Resolve repo slug for gh commands.
  local task_repo
  task_repo="$(_psql -c "SELECT repository FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
  local repo_url repo_slug
  repo_url="$(_psql -c "SELECT url FROM repositories WHERE name = \$tq\$$(_tq_escape "$task_repo")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
  repo_slug="$(_url_to_slug "$repo_url" 2>/dev/null || echo "")"
  if [[ -z "$repo_slug" ]]; then
    _pe_log "warn" "Cannot finalize PR: unable to resolve repo slug for task=$task_id"
    git checkout "$default_branch" 2>/dev/null || true
    return 0
  fi

  # Determine if this is a PR review pipeline (branch already has a PR)
  # or a feature pipeline (needs a new PR).
  local pr_head_branch
  pr_head_branch="$(_psql -c "SELECT initial_context::json->>'head_branch' FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"

  if [[ -n "$pr_head_branch" && "$pr_head_branch" != "null" ]]; then
    # ── PR review pipeline: push fixes and comment on the existing PR ────
    local has_changes
    has_changes="$(git status --porcelain 2>/dev/null || echo "")"
    if [[ -n "$has_changes" ]]; then
      git add -A 2>/dev/null || true
      git commit -m "fix: address review findings [${task_id}]" 2>/dev/null || true
    fi

    local ahead
    ahead="$(git rev-list --count "origin/${branch_name}..${branch_name}" 2>/dev/null || echo "0")"
    if [[ "$ahead" -gt 0 ]]; then
      git push origin "$branch_name" 2>/dev/null || {
        _pe_log "error" "Failed to push fixes to PR branch $branch_name"
        git checkout "$default_branch" 2>/dev/null || true
        return 0
      }
      _pe_log "info" "Pushed $ahead fix commits to PR branch=$branch_name"

      # Post a summary comment on the existing PR.
      local pr_number
      pr_number="$(_psql -c "SELECT initial_context::json->>'github_pr_number' FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
      if [[ -n "$pr_number" && "$pr_number" != "null" ]]; then
        local comment_body
        comment_body="## AI Fishtank Review — Fixes Pushed

**Task**: ${task_id}
**Commits pushed**: ${ahead}

$(echo -e "$stage_summaries")
---
*Automatically generated by AI Fishtank supervisor*"
        gh issue comment "$pr_number" --repo "$repo_slug" --body "$comment_body" 2>/dev/null || \
          _pe_log "warn" "Failed to post review comment on PR #$pr_number"
      fi
    else
      _pe_log "info" "No new commits to push to PR branch=$branch_name (review only, no fixes needed)"

      # Still post the review summary as a PR comment.
      local pr_number
      pr_number="$(_psql -c "SELECT initial_context::json->>'github_pr_number' FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
      if [[ -n "$pr_number" && "$pr_number" != "null" ]]; then
        local comment_body
        comment_body="## AI Fishtank Review

**Task**: ${task_id}

$(echo -e "$stage_summaries")
---
*Automatically generated by AI Fishtank supervisor*"
        gh issue comment "$pr_number" --repo "$repo_slug" --body "$comment_body" 2>/dev/null || \
          _pe_log "warn" "Failed to post review comment on PR #$pr_number"
      fi
    fi

  else
    # ── Feature/bugfix pipeline: create a new PR ────────────────────────
    local ahead
    ahead="$(git rev-list --count "${default_branch}..${branch_name}" 2>/dev/null || echo "0")"
    if [[ "$ahead" -eq 0 ]]; then
      _pe_log "info" "Pipeline branch has no commits ahead of $default_branch; no PR needed"
      git checkout "$default_branch" 2>/dev/null || true
      return 0
    fi

    _pe_log "info" "Pushing pipeline branch=$branch_name ($ahead commits) for task=$task_id"

    git push origin "$branch_name" --force 2>/dev/null || {
      _pe_log "error" "Failed to push branch $branch_name for task=$task_id"
      git checkout "$default_branch" 2>/dev/null || true
      return 0
    }

    local task_title
    task_title="$(_psql -c "SELECT title FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "$task_id")"

    local pr_body
    pr_body="## AI Fishtank Pipeline

**Task**: ${task_id}
**Commits**: ${ahead}

## Agent Summaries
$(echo -e "$stage_summaries")
---
*Automatically generated by AI Fishtank supervisor*"

    local pr_url
    if pr_url="$(gh pr create \
        --repo "$repo_slug" \
        --head "$branch_name" \
        --base "$default_branch" \
        --title "feat: ${task_title}" \
        --body "$pr_body" 2>&1)"; then
      _pe_log "info" "PR created: $pr_url for task=$task_id"
    else
      _pe_log "error" "Failed to create PR for task=$task_id: $pr_url"
    fi
  fi

  git checkout "$default_branch" 2>/dev/null || true
  return 0
}

# ── Private: _maybe_create_pr ────────────────────────────────────────────────
# After agent execution, check if the working tree has changes.
# If so, create a feature branch, commit, push, and open a PR.
# For review-only agents (no code changes), post the review as a GH issue comment.
_maybe_create_pr() {
  local task_id="$1"
  local category="$2"
  local stage_output="$3"

  # Resolve the repo working directory.
  local task_repo
  task_repo="$(_psql -c "SELECT repository FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
  local clone_dir
  clone_dir="$(_psql -c "SELECT clone_dir FROM repositories WHERE name = \$tq\$$(_tq_escape "$task_repo")\$tq\$ AND clone_status = 'ready';" 2>/dev/null | tr -d '[:space:]' || echo "")"

  if [[ -z "$clone_dir" || ! -d "$clone_dir" ]]; then
    _pe_log "warn" "Cannot create PR: repo directory not found for task=$task_id"
    return 0
  fi

  # Get the repo slug for gh commands.
  local repo_url repo_slug
  repo_url="$(_psql -c "SELECT url FROM repositories WHERE name = \$tq\$$(_tq_escape "$task_repo")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "")"
  repo_slug="$(_url_to_slug "$repo_url" 2>/dev/null || echo "")"

  if [[ -z "$repo_slug" ]]; then
    _pe_log "warn" "Cannot create PR: unable to resolve repo slug for task=$task_id"
    return 0
  fi

  # Get task title for PR/commit messages.
  local task_title
  task_title="$(_psql -c "SELECT title FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "$task_id")"

  cd "$clone_dir" || return 0

  local default_branch
  default_branch="$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@' || echo "main")"

  # Look for an agent-created branch matching this task ID.
  local branch_name=""
  local agent_branch
  agent_branch="$(git branch --list "aifishtank/${task_id}*" 2>/dev/null | head -1 | sed 's/^[* ]*//' || echo "")"

  if [[ -n "$agent_branch" ]]; then
    branch_name="$agent_branch"
    _pe_log "info" "Found agent branch '$branch_name' for task=$task_id"
  fi

  # Also check if there are uncommitted changes on current branch.
  local has_changes
  has_changes="$(git status --porcelain 2>/dev/null || echo "")"

  if [[ -n "$has_changes" && -z "$branch_name" ]]; then
    # Uncommitted changes but no agent branch — create one.
    branch_name="aifishtank/${task_id}"
    _pe_log "info" "Agent produced uncommitted changes; creating branch for task=$task_id"
    git checkout -b "$branch_name" 2>/dev/null || git checkout "$branch_name" 2>/dev/null || true
    git add -A 2>/dev/null
    git commit -m "${category}: ${task_title} [${task_id}]" 2>/dev/null || true
  elif [[ -n "$has_changes" && -n "$branch_name" ]]; then
    # Both agent branch and uncommitted changes — commit remaining changes to the branch.
    git checkout "$branch_name" 2>/dev/null || true
    git add -A 2>/dev/null
    git commit -m "${category}: additional changes [${task_id}]" 2>/dev/null || true
  fi

  if [[ -n "$branch_name" ]]; then
    # Check that the branch actually has commits ahead of the default branch.
    local ahead
    ahead="$(git rev-list --count "${default_branch}..${branch_name}" 2>/dev/null || echo "0")"
    if [[ "$ahead" -eq 0 ]]; then
      _pe_log "info" "Branch '$branch_name' has no commits ahead of $default_branch; skipping PR"
      git checkout "$default_branch" 2>/dev/null || true
      return 0
    fi

    _pe_log "info" "Pushing branch '$branch_name' ($ahead commits ahead) for task=$task_id"
    git push origin "$branch_name" --force 2>/dev/null || {
      _pe_log "error" "Failed to push branch $branch_name for task=$task_id"
      git checkout "$default_branch" 2>/dev/null || true
      return 0
    }

    # Build PR body from agent output.
    local summary
    summary="$(echo "$stage_output" | jq -r '.summary // ._raw_output // "Agent completed task."' 2>/dev/null || echo "Agent completed task.")"

    local pr_body
    pr_body="## AI Fishtank Agent Output

**Task**: ${task_id}
**Category**: ${category}
**Agent**: $(echo "$stage_output" | jq -r '._agent_name // "unknown"' 2>/dev/null)

### Summary
${summary}

---
*Automatically generated by AI Fishtank supervisor*"

    # Create PR.
    local pr_url
    if pr_url="$(gh pr create \
        --repo "$repo_slug" \
        --head "$branch_name" \
        --base "$default_branch" \
        --title "${category}: ${task_title}" \
        --body "$pr_body" 2>&1)"; then
      _pe_log "info" "PR created: $pr_url for task=$task_id"

      # Store PR URL in task context.
      local e_task_id e_pr_url
      e_task_id="$(_tq_escape "$task_id")"
      e_pr_url="$(_tq_escape "$pr_url")"
      _psql -c "INSERT INTO task_context (task_id, key, value_type, value_text) VALUES (\$tq\$${e_task_id}\$tq\$, 'pr_url', 'text', \$tq\$${e_pr_url}\$tq\$) ON CONFLICT DO NOTHING;" &>/dev/null || true
    else
      _pe_log "error" "Failed to create PR for task=$task_id: $pr_url"
    fi

    # Return to default branch.
    git checkout "$default_branch" 2>/dev/null || true

  elif [[ -z "$branch_name" && "$category" == "review" ]]; then
    # Review agent: post findings as a GH issue comment.
    local summary
    summary="$(echo "$stage_output" | jq -r '.summary // ._raw_output // "No findings."' 2>/dev/null || echo "No findings.")"
    local recommendation
    recommendation="$(echo "$stage_output" | jq -r '.recommendation // "comment"' 2>/dev/null || echo "comment")"
    local findings
    findings="$(echo "$stage_output" | jq -r '
      if .findings then
        [.findings[] | "- **\(.severity)**: \(.message)"] | join("\n")
      else
        "No specific findings."
      end
    ' 2>/dev/null || echo "")"

    local comment_body
    comment_body="## Code Review — ${task_title}

**Recommendation**: ${recommendation}

### Summary
${summary}

### Findings
${findings}

---
*Review by AI Fishtank review-agent (task: ${task_id})*"

    # Post as issue comment (issue #1 or source_ref).
    local source_ref
    source_ref="$(_psql -c "SELECT COALESCE(source_ref, '1') FROM tasks WHERE id = \$tq\$$(_tq_escape "$task_id")\$tq\$;" 2>/dev/null | tr -d '[:space:]' || echo "1")"

    if gh issue comment "$source_ref" --repo "$repo_slug" --body "$comment_body" 2>/dev/null; then
      _pe_log "info" "Review comment posted to issue #$source_ref for task=$task_id"
    else
      _pe_log "warn" "Failed to post review comment for task=$task_id"
    fi
  else
    _pe_log "info" "No code changes and not a review; skipping PR creation for task=$task_id"
  fi

  return 0
}

#!/usr/bin/env bash
# supervisor/lib/task-queue.sh
# Task queue operations backed by PostgreSQL.
#
# All functions communicate with the database using DATABASE_URL, which must
# be exported before sourcing this file (or set via load_config).
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/task-queue.sh"
#   create_task "github-issue-42" "Fix login bug" "implementation" \
#               "github-issue" "42" "example-app" "bugfix-pipeline" '{}'

set -euo pipefail

# Source shared utilities (provides _tq_escape and _url_to_slug).
# Support being sourced from different working directories.
_TQ_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./utils.sh
source "${_TQ_LIB_DIR}/utils.sh"

# ── Logging shim ──────────────────────────────────────────────────────────────

_tq_log() {
  local level="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '{"ts":"%s","level":"%s","component":"task-queue","msg":"%s"}\n' \
    "$ts" "$level" "$*" >&2
}

# ── Private: _psql ────────────────────────────────────────────────────────────
# Execute a SQL string against the configured database.
# Prints output to stdout; returns psql exit code.
_psql() {
  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?DATABASE_URL is not set}" \
    "$@"
}

# ── Private: _psql_json ───────────────────────────────────────────────────────
# Execute SQL and return a single JSON string result.
_psql_json() {
  psql --no-psqlrc --tuples-only --no-align \
    "${DATABASE_URL:?DATABASE_URL is not set}" \
    -c "SET search_path TO aifishtank, public;" \
    "$@"
}

# ── Public: create_task ───────────────────────────────────────────────────────
# Insert a new task into the queue.
#
# Arguments:
#   $1  id            — stable external identifier (e.g. github-issue-42)
#   $2  title         — human-readable title
#   $3  category      — review | implementation | test | design | docs | analyze
#   $4  source        — github-issue | github-pr | external
#   $5  source_ref    — issue/PR number or external reference
#   $6  repository    — repository name (must exist in repositories table)
#   $7  pipeline      — pipeline name (e.g. feature-pipeline)
#   $8  context_json  — initial context as JSON string
#
# Returns: 0 on success, 1 on failure.
create_task() {
  local id="$1"
  local title="$2"
  local category="$3"
  local source="$4"
  local source_ref="$5"
  local repository="$6"
  local pipeline="$7"
  local context_json="${8:-{}}"

  _tq_log "info" "Creating task id=$id category=$category source=$source repository=$repository"

  # Escape all user-supplied string values for $tq$-quoted SQL literals.
  local e_id e_title e_category e_source e_source_ref e_repository e_pipeline e_context
  e_id="$(_tq_escape "$id")"
  e_title="$(_tq_escape "$title")"
  e_category="$(_tq_escape "$category")"
  e_source="$(_tq_escape "$source")"
  e_source_ref="$(_tq_escape "$source_ref")"
  e_repository="$(_tq_escape "$repository")"
  e_pipeline="$(_tq_escape "$pipeline")"
  e_context="$(_tq_escape "$context_json")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
INSERT INTO tasks (id, title, category, source, source_ref, repository, pipeline, initial_context)
VALUES (
  \$tq\$${e_id}\$tq\$,
  \$tq\$${e_title}\$tq\$,
  \$tq\$${e_category}\$tq\$,
  \$tq\$${e_source}\$tq\$,
  \$tq\$${e_source_ref}\$tq\$,
  \$tq\$${e_repository}\$tq\$,
  \$tq\$${e_pipeline}\$tq\$,
  \$tq\$${e_context}\$tq\$::jsonb
)
ON CONFLICT (id) DO NOTHING;
SQL
)"

  if _psql -c "$sql" &>/dev/null; then
    _tq_log "info" "Task created: id=$id"
    return 0
  else
    _tq_log "error" "Failed to create task id=$id"
    return 1
  fi
}

# ── Public: get_next_task ─────────────────────────────────────────────────────
# Fetch and atomically claim the next pending task.
# Outputs a single line with tab-separated fields:
#   id  title  category  pipeline  repository  source  source_ref
#
# Returns 0 if a task was found, 1 if the queue is empty.
get_next_task() {
  local sql
  sql="$(cat <<'SQL'
SET search_path TO aifishtank, public;
UPDATE tasks
SET    status     = 'queued',
       updated_at = NOW()
WHERE  id = (
    SELECT id
    FROM   tasks
    WHERE  status = 'pending'
    ORDER  BY priority ASC, created_at ASC
    LIMIT  1
    FOR UPDATE SKIP LOCKED
)
RETURNING id, title, category, pipeline, repository, source, source_ref;
SQL
)"

  local result
  result="$(_psql -c "$sql" 2>/dev/null || true)"

  if [[ -z "$result" ]]; then
    return 1
  fi

  echo "$result"
  return 0
}

# ── Public: update_task_status ────────────────────────────────────────────────
# Update the lifecycle status of a task.
# Automatically sets started_at when transitioning to 'executing' and
# completed_at when transitioning to 'completed', 'failed', or 'timeout'.
#
# Usage: update_task_status "github-issue-42" "executing"
update_task_status() {
  local task_id="$1"
  local status="$2"

  _tq_log "info" "Updating task status: id=$task_id status=$status"

  local timestamp_clause=""
  case "$status" in
    executing)
      timestamp_clause=", started_at = COALESCE(started_at, NOW())"
      ;;
    completed|failed|timeout)
      timestamp_clause=", completed_at = NOW()"
      ;;
  esac

  local e_task_id e_status
  e_task_id="$(_tq_escape "$task_id")"
  e_status="$(_tq_escape "$status")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
UPDATE tasks
SET    status     = \$tq\$${e_status}\$tq\$${timestamp_clause},
       updated_at = NOW()
WHERE  id = \$tq\$${e_task_id}\$tq\$;
SQL
)"

  if _psql -c "$sql" &>/dev/null; then
    return 0
  else
    _tq_log "error" "Failed to update task status: id=$task_id"
    return 1
  fi
}

# ── Public: task_exists ───────────────────────────────────────────────────────
# Check whether a task with the given id already exists (idempotency guard).
#
# Usage: if task_exists "github-issue-42"; then ...
task_exists() {
  local task_id="$1"

  local e_task_id
  e_task_id="$(_tq_escape "$task_id")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
SELECT COUNT(*) FROM tasks WHERE id = \$tq\$${e_task_id}\$tq\$;
SQL
)"

  local count
  count="$(_psql -c "$sql" 2>/dev/null | tr -d '[:space:]' || echo "0")"

  [[ "$count" -gt 0 ]]
}

# ── Public: get_task ──────────────────────────────────────────────────────────
# Fetch a single task as a JSON object.
# Prints the JSON to stdout, or "null" if not found.
#
# Usage: task_json="$(get_task "github-issue-42")"
get_task() {
  local task_id="$1"

  local e_task_id
  e_task_id="$(_tq_escape "$task_id")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
SELECT row_to_json(t)
FROM (
  SELECT id, title, category, status, priority, source, source_ref,
         pipeline, repository, initial_context, created_at, updated_at,
         started_at, completed_at, assigned_agent, current_stage,
         retry_count, error_message
  FROM   tasks
  WHERE  id = \$tq\$${e_task_id}\$tq\$
) t;
SQL
)"

  local result
  result="$(_psql -c "$sql" 2>/dev/null | tr -d '[:space:]' || echo "")"

  if [[ -z "$result" ]]; then
    echo "null"
    return 1
  fi

  echo "$result"
  return 0
}

# ── Public: fail_task ─────────────────────────────────────────────────────────
# Mark a task as failed and record the error message.
# Increments retry_count and resets to 'pending' if retries remain;
# otherwise sets status to 'failed'.
#
# Usage: fail_task "github-issue-42" "Agent timed out after 60 minutes"
fail_task() {
  local task_id="$1"
  local error_message="$2"
  local max_retries="${CFG_MAX_RETRIES:-3}"

  _tq_log "error" "Failing task id=$task_id error=$error_message"

  local e_task_id e_error
  e_task_id="$(_tq_escape "$task_id")"
  e_error="$(_tq_escape "$error_message")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
UPDATE tasks
SET    retry_count   = retry_count + 1,
       error_message = \$tq\$${e_error}\$tq\$,
       status        = CASE
                         WHEN retry_count + 1 >= ${max_retries} THEN 'failed'
                         ELSE 'pending'
                       END,
       updated_at    = NOW(),
       completed_at  = CASE
                         WHEN retry_count + 1 >= ${max_retries} THEN NOW()
                         ELSE NULL
                       END
WHERE  id = \$tq\$${e_task_id}\$tq\$;
SQL
)"

  if _psql -c "$sql" &>/dev/null; then
    return 0
  else
    _tq_log "error" "Failed to fail task id=$task_id"
    return 1
  fi
}

# ── Public: complete_task ─────────────────────────────────────────────────────
# Mark a task as successfully completed.
#
# Usage: complete_task "github-issue-42"
complete_task() {
  local task_id="$1"

  _tq_log "info" "Completing task id=$task_id"

  local e_task_id
  e_task_id="$(_tq_escape "$task_id")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
UPDATE tasks
SET    status       = 'completed',
       completed_at = NOW(),
       updated_at   = NOW()
WHERE  id = \$tq\$${e_task_id}\$tq\$;
SQL
)"

  if _psql -c "$sql" &>/dev/null; then
    return 0
  else
    _tq_log "error" "Failed to complete task id=$task_id"
    return 1
  fi
}

# ── Public: store_stage_output ────────────────────────────────────────────────
# Record the output for a completed pipeline stage.
#
# Arguments:
#   $1  task_id      — parent task identifier
#   $2  stage_num    — 0-based stage index
#   $3  category     — stage category (e.g. analyze)
#   $4  agent        — agent name that executed this stage
#   $5  output_json  — structured output as a JSON string
#
# Usage: store_stage_output "github-issue-42" 0 "analyze" "analyze-agent" '{"issue_summary":"..."}'
store_stage_output() {
  local task_id="$1"
  local stage_num="$2"
  local category="$3"
  local agent="$4"
  local output_json="$5"

  _tq_log "info" "Storing stage output: task=$task_id stage=$stage_num agent=$agent"

  local e_task_id e_category e_agent e_output
  e_task_id="$(_tq_escape "$task_id")"
  e_category="$(_tq_escape "$category")"
  e_agent="$(_tq_escape "$agent")"
  e_output="$(_tq_escape "$output_json")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
INSERT INTO stages (task_id, stage_number, category, agent, status, structured_output, started_at, completed_at)
VALUES (
  \$tq\$${e_task_id}\$tq\$,
  ${stage_num},
  \$tq\$${e_category}\$tq\$,
  \$tq\$${e_agent}\$tq\$,
  'completed',
  \$tq\$${e_output}\$tq\$::jsonb,
  NOW(),
  NOW()
)
ON CONFLICT (task_id, stage_number) DO UPDATE
  SET agent             = EXCLUDED.agent,
      status            = 'completed',
      structured_output = EXCLUDED.structured_output,
      completed_at      = NOW();

UPDATE tasks
SET    current_stage = ${stage_num} + 1,
       updated_at    = NOW()
WHERE  id = \$tq\$${e_task_id}\$tq\$;
SQL
)"

  if _psql -c "$sql" &>/dev/null; then
    return 0
  else
    _tq_log "error" "Failed to store stage output: task=$task_id stage=$stage_num"
    return 1
  fi
}

# ── Public: get_task_context ──────────────────────────────────────────────────
# Call the get_task_context() database function to retrieve the full context
# document for a task (task metadata + all stages + all context entries).
# Prints the JSON document to stdout.
#
# Usage: context_json="$(get_task_context "github-issue-42")"
get_task_context() {
  local task_id="$1"

  _tq_log "info" "Fetching task context: id=$task_id"

  local e_task_id
  e_task_id="$(_tq_escape "$task_id")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
SELECT get_task_context(\$tq\$${e_task_id}\$tq\$);
SQL
)"

  local result
  result="$(_psql -c "$sql" 2>/dev/null | tr -d '[:space:]' || echo "")"

  if [[ -z "$result" || "$result" == "null" ]]; then
    _tq_log "warn" "No context found for task id=$task_id"
    echo "null"
    return 1
  fi

  echo "$result"
  return 0
}

# ── Public: update_poll_state ─────────────────────────────────────────────────
# Upsert the poll state for a named poller (cursor tracking).
#
# Usage: update_poll_state "github-tasks" "2026-03-14T10:00:00Z" '{"last_id":42}'
update_poll_state() {
  local poller_name="$1"
  local cursor="${2:-}"
  local state_data="${3:-{}}"

  local e_poller_name e_cursor e_state
  e_poller_name="$(_tq_escape "$poller_name")"
  e_cursor="$(_tq_escape "$cursor")"
  e_state="$(_tq_escape "$state_data")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
INSERT INTO poll_state (poller_name, last_poll_at, last_successful_at, cursor, state_data)
VALUES (
  \$tq\$${e_poller_name}\$tq\$,
  NOW(),
  NOW(),
  \$tq\$${e_cursor}\$tq\$,
  \$tq\$${e_state}\$tq\$::jsonb
)
ON CONFLICT (poller_name) DO UPDATE
  SET last_poll_at       = NOW(),
      last_successful_at = NOW(),
      cursor             = EXCLUDED.cursor,
      state_data         = EXCLUDED.state_data;
SQL
)"

  _psql -c "$sql" &>/dev/null
}

# ── Public: get_poll_cursor ───────────────────────────────────────────────────
# Return the stored cursor for a poller.
# Prints the cursor string to stdout, or an empty string if not found.
#
# Usage: cursor="$(get_poll_cursor "github-tasks")"
get_poll_cursor() {
  local poller_name="$1"

  local e_poller_name
  e_poller_name="$(_tq_escape "$poller_name")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
SELECT COALESCE(cursor, '') FROM poll_state WHERE poller_name = \$tq\$${e_poller_name}\$tq\$;
SQL
)"

  _psql -c "$sql" 2>/dev/null | tr -d '[:space:]' || echo ""
}

# ── Public: get_timed_out_tasks ───────────────────────────────────────────────
# Return task IDs for executing tasks that have exceeded their timeout.
# Prints one ID per line.
#
# Usage: while IFS= read -r task_id; do ...; done < <(get_timed_out_tasks 60)
get_timed_out_tasks() {
  local timeout_minutes="${1:-60}"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
SELECT id
FROM   tasks
WHERE  status     = 'executing'
  AND  started_at < NOW() - INTERVAL '${timeout_minutes} minutes';
SQL
)"

  _psql -c "$sql" 2>/dev/null || true
}

# ── Public: assign_agent ─────────────────────────────────────────────────────
# Record which agent instance is executing a task.
#
# Usage: assign_agent "github-issue-42" "analyze-agent"
assign_agent() {
  local task_id="$1"
  local agent_name="$2"

  local e_task_id e_agent_name
  e_task_id="$(_tq_escape "$task_id")"
  e_agent_name="$(_tq_escape "$agent_name")"

  local sql
  sql="$(cat <<SQL
SET search_path TO aifishtank, public;
UPDATE tasks
SET    assigned_agent = \$tq\$${e_agent_name}\$tq\$,
       status         = 'executing',
       started_at     = COALESCE(started_at, NOW()),
       updated_at     = NOW()
WHERE  id = \$tq\$${e_task_id}\$tq\$;
SQL
)"

  _psql -c "$sql" &>/dev/null
}

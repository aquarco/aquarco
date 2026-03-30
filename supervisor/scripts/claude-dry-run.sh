#!/usr/bin/env bash
# supervisor/scripts/claude-dry-run.sh
# Drop-in replacement for `claude` CLI that logs all inputs and emits
# valid NDJSON output without calling the Anthropic API.
#
# Toggle: CLAUDE_DRY_RUN=1 in the supervisor environment.
# Logs to: /var/log/aquarco/claude-dry-run-<timestamp>.log
set -euo pipefail

# --- Parse CLI args ---
SYSTEM_PROMPT_FILE=""
JSON_SCHEMA=""
MAX_TURNS=""
DEBUG_FILE=""
ALLOWED_TOOLS=""
DENIED_TOOLS=""
RESUME_SESSION=""
APPEND_SYSTEM_PROMPT=""
ALL_ARGS=("$@")

while [[ $# -gt 0 ]]; do
  case "$1" in
    --system-prompt-file) SYSTEM_PROMPT_FILE="$2"; shift 2 ;;
    --json-schema)        JSON_SCHEMA="$2"; shift 2 ;;
    --max-turns)          MAX_TURNS="$2"; shift 2 ;;
    --debug-file)         DEBUG_FILE="$2"; shift 2 ;;
    --allowedTools)       ALLOWED_TOOLS="$2"; shift 2 ;;
    --disallowedTools)    DENIED_TOOLS="$2"; shift 2 ;;
    --resume)             RESUME_SESSION="$2"; shift 2 ;;
    --append-system-prompt) APPEND_SYSTEM_PROMPT="$2"; shift 2 ;;
    *)                    shift ;;
  esac
done

# --- Setup logging ---
TS="$(date -u +"%Y%m%dT%H%M%SZ")"
SESSION_ID="dry-run-${TS}-$$"
LOG_DIR="/var/log/aquarco"
LOG_FILE="${LOG_DIR}/claude-dry-run-${TS}-$$.log"
mkdir -p "$LOG_DIR"

# --- Read stdin ---
STDIN_CONTENT="$(cat)"

# --- Log everything (restrictive permissions to protect sensitive data) ---
touch "$LOG_FILE"
chmod 600 "$LOG_FILE"
{
  echo "=== CLAUDE DRY-RUN LOG ==="
  echo "timestamp:    $TS"
  echo "pid:          $$"
  echo "ppid:         $PPID"
  echo "cwd:          $(pwd)"
  echo "user:         $(whoami)"
  echo ""
  echo "=== CLI ARGS ==="
  printf '%s\n' "${ALL_ARGS[@]}"
  echo ""
  echo "=== PARSED ARGS ==="
  echo "system_prompt_file: $SYSTEM_PROMPT_FILE"
  echo "json_schema:        ${JSON_SCHEMA:0:200}..."
  echo "max_turns:          $MAX_TURNS"
  echo "debug_file:         $DEBUG_FILE"
  echo "allowed_tools:      $ALLOWED_TOOLS"
  echo "denied_tools:       $DENIED_TOOLS"
  echo "resume_session:     $RESUME_SESSION"
  echo "append_system_prompt_len: ${#APPEND_SYSTEM_PROMPT}"
  echo ""
  echo "=== ENVIRONMENT (safe subset) ==="
  echo "AGENT_MODE=${AGENT_MODE:-}"
  echo "STRICT_MODE=${STRICT_MODE:-}"
  echo "CLAUDE_DRY_RUN=${CLAUDE_DRY_RUN:-}"
  echo ""
  echo "=== STDIN (context) ==="
  echo "stdin_bytes: ${#STDIN_CONTENT}"
  echo "(content omitted — may contain sensitive data)"
  echo ""
  echo "=== SYSTEM PROMPT FILE CONTENT ==="
  if [[ -n "$SYSTEM_PROMPT_FILE" && -f "$SYSTEM_PROMPT_FILE" ]]; then
    wc -c < "$SYSTEM_PROMPT_FILE"
    echo " bytes"
  else
    echo "(not found or not specified)"
  fi
} > "$LOG_FILE" 2>&1

# --- Write debug log (mimics --debug-file) ---
if [[ -n "$DEBUG_FILE" ]]; then
  mkdir -p "$(dirname "$DEBUG_FILE")"
  {
    echo "$(date -u +"%Y-%m-%dT%H:%M:%S.000Z") [DEBUG] DRY-RUN mode active"
    echo "$(date -u +"%Y-%m-%dT%H:%M:%S.000Z") [DEBUG] Session: $SESSION_ID"
    echo "$(date -u +"%Y-%m-%dT%H:%M:%S.000Z") [DEBUG] Args logged to: $LOG_FILE"
  } > "$DEBUG_FILE"
fi

# --- Build structured_output from --json-schema ---
if [[ -n "$JSON_SCHEMA" ]] && command -v jq &>/dev/null; then
  # Extract required fields and generate stub values based on type
  STRUCTURED_OUTPUT="$(echo "$JSON_SCHEMA" | jq -c '
    (.required // []) as $req |
    .properties // {} |
    to_entries |
    map(select(.key as $k | $req | index($k))) |
    map(
      if .value.type == "string" then {(.key): ("dry-run stub for " + .key)}
      elif .value.type == "array" then {(.key): []}
      elif .value.type == "object" then {(.key): {}}
      elif .value.type == "integer" then {(.key): 0}
      elif .value.type == "number" then {(.key): 0}
      elif .value.type == "boolean" then {(.key): false}
      else {(.key): null}
      end
    ) | add // {}
  ')"
else
  STRUCTURED_OUTPUT='{"_dry_run": true}'
fi

# --- Phase 1: Startup (3 seconds) ---
sleep 3

# Emit system init event
echo '{"type":"system","subtype":"init","cwd":"'"$(pwd)"'","session_id":"'"$SESSION_ID"'","tools":["Read","Write","Edit","Grep","Glob","Bash"],"model":"claude-sonnet-4-6","permissionMode":"bypassPermissions","claude_code_version":"dry-run","uuid":"'"$SESSION_ID-init"'"}'

# --- Phase 2: First API call (5 seconds) ---
sleep 5

# Emit assistant thinking event
echo '{"type":"assistant","message":{"model":"claude-sonnet-4-6","id":"msg_dry_run_1","type":"message","role":"assistant","content":[{"type":"text","text":"[DRY-RUN] Analyzing the task context and producing structured output."}],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":100,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0,"output_tokens":20,"service_tier":"standard"},"context_management":null},"parent_tool_use_id":null,"session_id":"'"$SESSION_ID"'","uuid":"'"$SESSION_ID-msg1"'"}'

# --- Phase 3: Tool use simulation (5 seconds) ---
sleep 5

# --- Emit result event ---
RESULT_JSON="$(jq -n -c \
  --arg subtype "success" \
  --argjson structured "$STRUCTURED_OUTPUT" \
  --arg result "[DRY-RUN] Task completed with stub output." \
  --arg session "$SESSION_ID" \
  '{
    type: "result",
    subtype: $subtype,
    is_error: false,
    duration_ms: 13000,
    duration_api_ms: 10000,
    num_turns: 3,
    result: $result,
    stop_reason: "end_turn",
    session_id: $session,
    total_cost_usd: 0.0,
    structured_output: $structured,
    usage: {
      input_tokens: 5000,
      cache_creation_input_tokens: 10000,
      cache_read_input_tokens: 0,
      output_tokens: 500,
      server_tool_use: {web_search_requests: 0, web_fetch_requests: 0},
      service_tier: "standard"
    },
    modelUsage: {
      "claude-sonnet-4-6": {
        inputTokens: 5000,
        outputTokens: 500,
        cacheReadInputTokens: 0,
        cacheCreationInputTokens: 10000,
        costUSD: 0.0,
        contextWindow: 200000,
        maxOutputTokens: 32000
      }
    },
    permission_denials: [],
    uuid: ($session + "-result")
  }'
)"

echo "$RESULT_JSON"

# --- Log completion ---
{
  echo ""
  echo "=== COMPLETION ==="
  echo "exit_time: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "structured_output: $STRUCTURED_OUTPUT"
  echo "duration: ~13s (simulated)"
} >> "$LOG_FILE" 2>&1

exit 0

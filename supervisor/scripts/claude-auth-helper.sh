#!/usr/bin/env bash
# supervisor/scripts/claude-auth-helper.sh
# File-based IPC helper for Claude CLI authentication.
#
# Uses Python pexpect to drive the interactive `claude auth login` flow.
# The API container communicates via the shared IPC directory.
#
# Commands (written as files):
#   login-request   → starts claude auth login, captures URL → writes login-response
#   code-submit     → feeds the auth code to the waiting CLI process
#   status-request  → runs claude auth status --json → writes status-response
#   logout-request  → runs claude auth logout → writes logout-response

set -euo pipefail

IPC_DIR="/var/lib/aifishtank/claude-ipc"
POLL_INTERVAL=2

log() {
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf '{"ts":"%s","component":"claude-auth-helper","msg":"%s"}\n' "$ts" "$*" >&2
}

mkdir -p "$IPC_DIR"

log "Watching $IPC_DIR for auth commands (poll=${POLL_INTERVAL}s)"

while true; do
  # ── Handle login request ──────────────────────────────────────────────────
  if [[ -f "$IPC_DIR/login-request" ]]; then
    log "Login request received"
    rm -f "$IPC_DIR/login-request" "$IPC_DIR/login-response"
    rm -f "$IPC_DIR/code-submit" "$IPC_DIR/code-complete"

    # Kill any previous login helper
    pkill -f "claude-auth-oauth" 2>/dev/null || true
    pkill -f "claude-auth-pexpect" 2>/dev/null || true
    pkill -f "claude auth login" 2>/dev/null || true
    sleep 1

    # Launch the direct OAuth PKCE driver in the background
    python3 "$(dirname "${BASH_SOURCE[0]}")/claude-auth-oauth.py" "$IPC_DIR" &
    log "Started OAuth login driver"
  fi

  # ── Handle status request ─────────────────────────────────────────────────
  if [[ -f "$IPC_DIR/status-request" ]]; then
    log "Status request received"
    rm -f "$IPC_DIR/status-request" "$IPC_DIR/status-response"

    # Try claude auth status first (with short timeout to avoid hangs)
    status_json="$(timeout 5 claude auth status --json 2>/dev/null || true)"

    # If CLI didn't respond, check credentials file directly
    if [[ -z "$status_json" ]] || ! echo "$status_json" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
      CRED_FILE="$HOME/.claude/.credentials.json"
      if [[ -f "$CRED_FILE" ]]; then
        has_token="$(python3 -c "
import json,sys
try:
    c=json.load(open('$CRED_FILE'))
    t=c.get('claudeAiOauth',{})
    if t.get('accessToken'):
        print(json.dumps({'loggedIn':True,'authMethod':'oauth'}))
    else:
        print(json.dumps({'loggedIn':False}))
except:
    print(json.dumps({'loggedIn':False}))
" 2>/dev/null)"
        status_json="${has_token:-{\"loggedIn\":false}}"
      else
        status_json='{"loggedIn":false}'
      fi
    fi

    echo "$status_json" > "$IPC_DIR/status-response"
    log "Status response written"
  fi

  # ── Handle logout request ─────────────────────────────────────────────────
  if [[ -f "$IPC_DIR/logout-request" ]]; then
    log "Logout request received"
    rm -f "$IPC_DIR/logout-request" "$IPC_DIR/logout-response"

    claude auth logout 2>/dev/null || true
    echo '{"success":true}' > "$IPC_DIR/logout-response"
    log "Logout completed"
  fi

  sleep "$POLL_INTERVAL"
done

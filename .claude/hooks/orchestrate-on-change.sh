#!/usr/bin/env bash
# .claude/hooks/orchestrate-on-change.sh
# Fires after every Write/Edit/MultiEdit tool use.
# Reads changed file info and dispatches to the solution-architect subagent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG_DIR="$PROJECT_ROOT/.claude/logs"
LOG_FILE="$LOG_DIR/orchestrator.log"

mkdir -p "$LOG_DIR"

# Parse stdin JSON from Claude Code hook event
INPUT="$(cat)"
TOOL_NAME="$(echo "$INPUT" | jq -r '.tool_name // "unknown"')"
FILE_PATH="$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.new_path // "unknown"')"
SESSION_ID="$(echo "$INPUT" | jq -r '.session_id // "unknown"')"
TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# Log the event
echo "$TIMESTAMP | session=$SESSION_ID | tool=$TOOL_NAME | file=$FILE_PATH" >> "$LOG_FILE"

# Skip non-source files (logs, node_modules, .git, etc.)
if echo "$FILE_PATH" | grep -qE "(node_modules|\.git/|\.claude/logs|dist/|\.next/|coverage/)"; then
  exit 0
fi

# Skip if it's the prd.json itself (ralph writes this, don't re-trigger)
if echo "$FILE_PATH" | grep -q "prd\.json"; then
  exit 0
fi

# Skip if it's a doc file being written by the docs agent (don't re-trigger)
if echo "$FILE_PATH" | grep -qE "^(CLAUDE|README|CHANGELOG)\.md$"; then
  exit 0
fi

# Determine file category for routing hint
CATEGORY="general"
if echo "$FILE_PATH" | grep -qE "\.(sql|migration)$|migrations/"; then
  CATEGORY="database"
elif echo "$FILE_PATH" | grep -qE "\.(test|spec)\.(ts|tsx|js|jsx)$|__tests__/"; then
  CATEGORY="testing"
elif echo "$FILE_PATH" | grep -qE "auth|security|password|token|secret|permission"; then
  CATEGORY="security"
elif echo "$FILE_PATH" | grep -qE "docker-compose|Dockerfile|compose\."; then
  CATEGORY="dev-infra"
elif echo "$FILE_PATH" | grep -qE "\.graphql$|schema\.|resolver\.|\.gql$"; then
  CATEGORY="graphql"
elif echo "$FILE_PATH" | grep -qE "components/|pages/|app/|hooks/|\.tsx$|\.jsx$"; then
  CATEGORY="frontend"
elif echo "$FILE_PATH" | grep -qE "e2e/|playwright\.config|register|portfolio|middleware\.ts"; then
  CATEGORY="e2e"
elif echo "$FILE_PATH" | grep -qE "scripts/|Makefile|\.sh$|workflows/"; then
  CATEGORY="scripting"
elif echo "$FILE_PATH" | grep -qE "\.claude/agents/|\.claude/commands/|\.claude/hooks/|\.claude/settings\.json"; then
  CATEGORY="docs"
fi

# Output context for Claude to pick up and route to solution-architect
cat <<EOF
{
  "hookEventName": "PostToolUse",
  "additionalContext": "File changed: $FILE_PATH (category: $CATEGORY). The solution-architect agent should review this change, write a task file to tasks/, and delegate to the appropriate specialist agents if needed. Check prd.json for current project context. Do NOT invoke ralph unless explicitly requested."
}
EOF

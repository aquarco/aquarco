#!/usr/bin/env bash
# .claude/hooks/session-start.sh
# Fires at the start of every Claude Code session.
# Injects current PRD context so Claude starts with project awareness.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PRD_FILE="$PROJECT_ROOT/prd.json"

TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# Build context summary from prd.json
if [ -f "$PRD_FILE" ]; then
  PROJECT_NAME="$(jq -r '.project.name // "unnamed project"' "$PRD_FILE")"
  PROJECT_STATUS="$(jq -r '.project.status // "unknown"' "$PRD_FILE")"
  PRD_VERSION="$(jq -r '._meta.version // "0.0.0"' "$PRD_FILE")"
  ADR_COUNT="$(jq '.architecture_decisions | length' "$PRD_FILE")"
  OPEN_Q_COUNT="$(jq '.open_questions | length' "$PRD_FILE")"
  
  CONTEXT="Session started at $TIMESTAMP. Project: '$PROJECT_NAME' (status: $PROJECT_STATUS, PRD v$PRD_VERSION, $ADR_COUNT architecture decisions, $OPEN_Q_COUNT open questions). Read prd.json for full context before starting work. The multi-agent system is active: solution-architect coordinates all work, ralph manages prd.json."
else
  CONTEXT="Session started at $TIMESTAMP. No prd.json found yet. This is a new project. Consider running /architect-init to initialize the project with the solution-architect agent."
fi

cat <<EOF
{
  "additionalContext": "$CONTEXT"
}
EOF

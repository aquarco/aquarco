#!/usr/bin/env bash
# supervisor/lib/utils.sh
# Shared utility functions for supervisor scripts.
#
# Source this file from any supervisor script that needs these helpers:
#   source "$(dirname "${BASH_SOURCE[0]}")/../lib/utils.sh"   # from pollers/
#   source "${SUPERVISOR_ROOT}/lib/utils.sh"                  # from scripts/

set -euo pipefail

# ── SQL helpers ───────────────────────────────────────────────────────────────

# _tq_escape VAL
# Escape a value for safe interpolation inside a $tq$...$tq$ dollar-quoted
# SQL string literal.  If the value itself contains the literal text "$tq$"
# it would break out of the quoting; replace it with the harmless stand-in
# "$_tq_$" before interpolation.
#
# Usage:
#   escaped="$(_tq_escape "$user_supplied_string")"
#   sql="INSERT INTO t (col) VALUES (\$tq\$${escaped}\$tq\$)"
_tq_escape() {
  local val="$1"
  # Replace every occurrence of $tq$ inside the value so it cannot close
  # the surrounding dollar-quote tags.
  echo "${val//\$tq\$/\$_tq_\$}"
}

# ── GitHub URL helpers ────────────────────────────────────────────────────────

# _url_to_slug URL
# Convert a GitHub repository URL (HTTPS or SSH) to an "owner/repo" slug.
#
# Examples:
#   https://github.com/owner/example-app.git  ->  owner/example-app
#   git@github.com:owner/example-app.git      ->  owner/example-app
#
# Returns 0 on success (slug printed to stdout), 1 if URL is unrecognised.
_url_to_slug() {
  local url="$1"

  # HTTPS: https://github.com/owner/repo[.git]
  if [[ "$url" =~ ^https://github.com/([^/]+/[^/]+?)(.git)?$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi

  # SSH: git@github.com:owner/repo[.git]
  if [[ "$url" =~ ^git@github.com:([^/]+/[^/]+?)(.git)?$ ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi

  echo ""
  return 1
}

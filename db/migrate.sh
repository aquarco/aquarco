#!/bin/sh
set -e

# Helper script for yoyo migration operations.
# Usage: migrate.sh [apply|rollback|reapply|list] [extra yoyo args...]

COMMAND="${1:-apply}"
shift 2>/dev/null || true

case "$COMMAND" in
  apply)
    echo "==> Applying pending migrations..."
    yoyo apply --batch  -c /db/yoyo.ini "$@"
    ;;
  rollback)
    echo "==> Rolling back last migration..."
    yoyo rollback --batch  -c /db/yoyo.ini "$@"
    ;;
  reapply)
    echo "==> Re-applying last migration (rollback + apply)..."
    yoyo reapply --batch  -c /db/yoyo.ini "$@"
    ;;
  list)
    echo "==> Listing migrations..."
    yoyo list -c /db/yoyo.ini "$@"
    ;;
  *)
    echo "Usage: migrate.sh [apply|rollback|reapply|list] [extra args...]"
    exit 1
    ;;
esac

echo "==> Done."

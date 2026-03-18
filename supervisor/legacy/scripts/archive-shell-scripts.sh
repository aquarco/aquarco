#!/usr/bin/env bash
# Archive shell scripts to supervisor/legacy/ after Python cutover.
#
# This script:
# 1. Creates supervisor/legacy/ directory
# 2. Moves all shell implementation files there
# 3. Updates the systemd service to use Python
# 4. Keeps config/ and templates/ in place (shared between implementations)
#
# Usage:
#   ./supervisor/scripts/archive-shell-scripts.sh [--dry-run]
#
# To rollback:
#   mv supervisor/legacy/lib supervisor/lib
#   mv supervisor/legacy/pollers supervisor/pollers
#   mv supervisor/legacy/scripts/*.sh supervisor/scripts/
#   # Then update systemd to point back to supervisor.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SUPERVISOR_DIR="$(dirname "$SCRIPT_DIR")"
LEGACY_DIR="$SUPERVISOR_DIR/legacy"
DRY_RUN="${1:-}"

log() { echo "[$(date -Iseconds)] $*"; }

do_mv() {
    if [ "$DRY_RUN" = "--dry-run" ]; then
        log "  [DRY RUN] mv $1 -> $2"
    else
        mv "$1" "$2"
        log "  moved $1 -> $2"
    fi
}

log "Archiving shell scripts to $LEGACY_DIR"

if [ "$DRY_RUN" = "--dry-run" ]; then
    log "DRY RUN MODE - no files will be moved"
fi

# Create legacy directory
mkdir -p "$LEGACY_DIR"

# Move shell implementation directories
for dir in lib pollers; do
    if [ -d "$SUPERVISOR_DIR/$dir" ]; then
        do_mv "$SUPERVISOR_DIR/$dir" "$LEGACY_DIR/$dir"
    fi
done

# Move shell scripts (but keep Python scripts and utility scripts)
mkdir -p "$LEGACY_DIR/scripts"
for script in supervisor.sh clone-worker.sh pull-worker.sh claude-auth-helper.sh; do
    if [ -f "$SUPERVISOR_DIR/scripts/$script" ]; then
        do_mv "$SUPERVISOR_DIR/scripts/$script" "$LEGACY_DIR/scripts/$script"
    fi
done

log ""
log "Archive complete. Shell scripts moved to $LEGACY_DIR"
log ""
log "Next steps:"
log "  1. Install Python package: cd supervisor/python && pip install -e ."
log "  2. Update systemd:"
log "     sudo cp supervisor/systemd/aifishtank-supervisor-python.service \\"
log "              /etc/systemd/system/aifishtank-supervisor.service"
log "     sudo systemctl daemon-reload"
log "     sudo systemctl restart aifishtank-supervisor"
log ""
log "To rollback, move files back from supervisor/legacy/"

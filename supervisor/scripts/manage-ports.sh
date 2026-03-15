#!/usr/bin/env bash
# manage-ports.sh — VirtualBox NAT port forwarding management
#
# Adds, removes, and lists NAT port forwarding rules on the AI Fishtank VM.
# Works with both powered-off VMs (modifyvm) and running VMs (controlvm).
#
# Usage:
#   manage-ports.sh <command> [args...]
#
# Commands:
#   add    <vm> <rule-name> <host-port> <guest-port>
#   remove <vm> <rule-name>
#   list   <vm>
#   setup-repo   <vm> <repo-name> <frontend-port> <api-port> <postgres-port>
#   teardown-repo <vm> <repo-name>
#   help
#
# Examples:
#   manage-ports.sh add aifishtank webui 8080 8080
#   manage-ports.sh setup-repo aifishtank my-saas-app 3001 4001 5433
#   manage-ports.sh teardown-repo aifishtank my-saas-app
#   manage-ports.sh list aifishtank

set -euo pipefail

# ─── Helpers ──────────────────────────────────────────────────────────────────

log() {
  echo "[manage-ports] $*"
}

die() {
  echo "[manage-ports] ERROR: $*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage: $(basename "$0") <command> [args]

Commands:
  add    <vm> <rule-name> <host-port> <guest-port>
         Add a NAT port forwarding rule.

  remove <vm> <rule-name>
         Remove a NAT port forwarding rule.

  list   <vm>
         List all NAT port forwarding rules on the VM.

  setup-repo <vm> <repo-name> <frontend-port> <api-port> <postgres-port>
         Add port forwarding rules for a target repo's three services.
         Rule names: <repo-name>-fe, <repo-name>-api, <repo-name>-pg

  teardown-repo <vm> <repo-name>
         Remove all port forwarding rules for a target repo.

  help   Show this help text.

Environment variables:
  VBOXMANAGE   Path to VBoxManage binary (default: VBoxManage)

Notes:
  - For running VMs, controlvm natpf1 is used automatically.
  - For stopped VMs, modifyvm --natpf1 is used.
  - Rule names must be unique within a VM.
EOF
  exit 0
}

# ─── VBoxManage wrapper ───────────────────────────────────────────────────────

VBOXMANAGE="${VBOXMANAGE:-VBoxManage}"

require_vboxmanage() {
  if ! command -v "${VBOXMANAGE}" &>/dev/null; then
    die "VBoxManage not found. Install VirtualBox, or set VBOXMANAGE env var."
  fi
}

# Returns "running", "poweroff", "saved", or other state string
vm_state() {
  local vm_name="$1"
  "${VBOXMANAGE}" showvminfo "${vm_name}" --machinereadable 2>/dev/null \
    | grep '^VMState=' \
    | cut -d'"' -f2 \
    || echo "unknown"
}

# Check that a VM exists
require_vm() {
  local vm_name="$1"
  if ! "${VBOXMANAGE}" showvminfo "${vm_name}" &>/dev/null; then
    die "VM '${vm_name}' not found. Check the name with: VBoxManage list vms"
  fi
}

# ─── Core functions ───────────────────────────────────────────────────────────

# add_port_forward <vm_name> <rule_name> <host_port> <guest_port>
add_port_forward() {
  local vm_name="$1"
  local rule_name="$2"
  local host_port="$3"
  local guest_port="$4"

  [[ -z "${vm_name}"   ]] && die "vm_name is required"
  [[ -z "${rule_name}" ]] && die "rule_name is required"
  [[ -z "${host_port}" ]] && die "host_port is required"
  [[ -z "${guest_port}" ]] && die "guest_port is required"

  require_vm "${vm_name}"

  local rule="tcp,,${host_port},,${guest_port}"
  local state
  state="$(vm_state "${vm_name}")"

  # Check if rule already exists (list current rules and grep)
  if "${VBOXMANAGE}" showvminfo "${vm_name}" --machinereadable 2>/dev/null \
    | grep -q "\"${rule_name}\""; then
    log "Rule '${rule_name}' already exists on VM '${vm_name}' — skipping add."
    return 0
  fi

  if [[ "${state}" == "running" ]]; then
    log "VM is running — using controlvm natpf1 add"
    "${VBOXMANAGE}" controlvm "${vm_name}" natpf1 "${rule_name},${rule}"
  else
    log "VM is ${state} — using modifyvm --natpf1"
    "${VBOXMANAGE}" modifyvm "${vm_name}" --natpf1 "${rule_name},${rule}"
  fi

  log "Added: ${rule_name} host:${host_port} -> guest:${guest_port} (VM: ${vm_name})"
}

# remove_port_forward <vm_name> <rule_name>
remove_port_forward() {
  local vm_name="$1"
  local rule_name="$2"

  [[ -z "${vm_name}"   ]] && die "vm_name is required"
  [[ -z "${rule_name}" ]] && die "rule_name is required"

  require_vm "${vm_name}"

  # Check if rule exists before attempting removal
  if ! "${VBOXMANAGE}" showvminfo "${vm_name}" --machinereadable 2>/dev/null \
    | grep -q "\"${rule_name}\""; then
    log "Rule '${rule_name}' does not exist on VM '${vm_name}' — nothing to remove."
    return 0
  fi

  local state
  state="$(vm_state "${vm_name}")"

  if [[ "${state}" == "running" ]]; then
    log "VM is running — using controlvm natpf1 remove"
    "${VBOXMANAGE}" controlvm "${vm_name}" natpf1 remove "${rule_name}"
  else
    log "VM is ${state} — using modifyvm --natpf1 delete"
    "${VBOXMANAGE}" modifyvm "${vm_name}" --natpf1 "delete ${rule_name}"
  fi

  log "Removed: ${rule_name} (VM: ${vm_name})"
}

# list_port_forwards <vm_name>
list_port_forwards() {
  local vm_name="$1"

  [[ -z "${vm_name}" ]] && die "vm_name is required"

  require_vm "${vm_name}"

  local state
  state="$(vm_state "${vm_name}")"
  log "Port forwarding rules for VM '${vm_name}' (state: ${state}):"
  echo ""
  printf "%-30s  %-12s  %-12s\n" "RULE NAME" "HOST PORT" "GUEST PORT"
  printf "%-30s  %-12s  %-12s\n" "─────────────────────────────" "───────────" "────────────"

  # Parse machinereadable output: Forwarding0="name,tcp,,host_port,,guest_port"
  "${VBOXMANAGE}" showvminfo "${vm_name}" --machinereadable 2>/dev/null \
    | grep '^Forwarding' \
    | sed 's/^Forwarding[0-9]*="//;s/"$//' \
    | while IFS=',' read -r name proto host_ip host_port guest_ip guest_port; do
        printf "%-30s  %-12s  %-12s\n" "${name}" "${host_port:-*}" "${guest_port}"
      done

  echo ""
}

# setup_repo_ports <vm_name> <repo_name> <frontend_port> <api_port> <postgres_port>
setup_repo_ports() {
  local vm_name="$1"
  local repo_name="$2"
  local frontend_port="$3"
  local api_port="$4"
  local postgres_port="$5"

  [[ -z "${vm_name}"      ]] && die "vm_name is required"
  [[ -z "${repo_name}"    ]] && die "repo_name is required"
  [[ -z "${frontend_port}" ]] && die "frontend_port is required"
  [[ -z "${api_port}"     ]] && die "api_port is required"
  [[ -z "${postgres_port}" ]] && die "postgres_port is required"

  log "Setting up port forwarding for repo '${repo_name}' on VM '${vm_name}'..."
  log "  Frontend : host:${frontend_port} -> guest:${frontend_port}"
  log "  API      : host:${api_port}      -> guest:${api_port}"
  log "  Postgres : host:${postgres_port} -> guest:${postgres_port}"

  add_port_forward "${vm_name}" "${repo_name}-fe"  "${frontend_port}" "${frontend_port}"
  add_port_forward "${vm_name}" "${repo_name}-api" "${api_port}"      "${api_port}"
  add_port_forward "${vm_name}" "${repo_name}-pg"  "${postgres_port}" "${postgres_port}"

  log "Repo '${repo_name}' port forwarding is active."
  log "  Access frontend at: http://localhost:${frontend_port}"
  log "  Access API at:      http://localhost:${api_port}/graphql"
}

# teardown_repo_ports <vm_name> <repo_name>
teardown_repo_ports() {
  local vm_name="$1"
  local repo_name="$2"

  [[ -z "${vm_name}"   ]] && die "vm_name is required"
  [[ -z "${repo_name}" ]] && die "repo_name is required"

  log "Removing port forwarding for repo '${repo_name}' from VM '${vm_name}'..."

  remove_port_forward "${vm_name}" "${repo_name}-fe"
  remove_port_forward "${vm_name}" "${repo_name}-api"
  remove_port_forward "${vm_name}" "${repo_name}-pg"

  log "Port forwarding for repo '${repo_name}' removed."
}

# ─── Entry point ──────────────────────────────────────────────────────────────

require_vboxmanage

COMMAND="${1:-help}"
shift || true

case "${COMMAND}" in
  add)
    [[ $# -lt 4 ]] && die "Usage: $0 add <vm> <rule-name> <host-port> <guest-port>"
    add_port_forward "$1" "$2" "$3" "$4"
    ;;

  remove)
    [[ $# -lt 2 ]] && die "Usage: $0 remove <vm> <rule-name>"
    remove_port_forward "$1" "$2"
    ;;

  list)
    [[ $# -lt 1 ]] && die "Usage: $0 list <vm>"
    list_port_forwards "$1"
    ;;

  setup-repo)
    [[ $# -lt 5 ]] && die "Usage: $0 setup-repo <vm> <repo-name> <frontend-port> <api-port> <postgres-port>"
    setup_repo_ports "$1" "$2" "$3" "$4" "$5"
    ;;

  teardown-repo)
    [[ $# -lt 2 ]] && die "Usage: $0 teardown-repo <vm> <repo-name>"
    teardown_repo_ports "$1" "$2"
    ;;

  help|--help|-h)
    usage
    ;;

  *)
    die "Unknown command: '${COMMAND}'. Run '$0 help' for usage."
    ;;
esac

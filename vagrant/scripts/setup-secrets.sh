#!/usr/bin/env bash
# setup-secrets.sh — Interactive secrets setup for AI Fishtank VM
#
# Run this once after `vagrant up` finishes provisioning:
#   sudo /home/agent/ai-fishtank/vagrant/scripts/setup-secrets.sh
#
# What it does:
#   - Prompts for GitHub PAT and Anthropic API key (or reads from env/file)
#   - Writes credentials to agent home with strict permissions
#   - Creates /etc/aifishtank/secrets.env for systemd EnvironmentFile
#   - Validates GitHub token via gh auth status
#   - Prints next steps

set -euo pipefail

AGENT_USER="agent"
AGENT_HOME="/home/${AGENT_USER}"
SECRETS_DIR="/etc/aifishtank"
SECRETS_ENV="${SECRETS_DIR}/secrets.env"

log() {
  echo "[setup-secrets] $*"
}

die() {
  echo "[setup-secrets] ERROR: $*" >&2
  exit 1
}

# ─── Must run as root ─────────────────────────────────────────────────────────

if [[ "${EUID}" -ne 0 ]]; then
  die "This script must be run as root (use sudo)"
fi

# Ensure agent user exists
if ! id "${AGENT_USER}" &>/dev/null; then
  die "User '${AGENT_USER}' does not exist. Run provision.sh first."
fi

# ─── Read GitHub PAT ──────────────────────────────────────────────────────────

log ""
log "==========================================================="
log "  AI Fishtank Secrets Setup"
log "==========================================================="
log ""

GITHUB_TOKEN=""

# Priority 1: environment variable
if [[ -n "${AIHOME_GITHUB_TOKEN:-}" ]]; then
  log "Using GitHub token from AIHOME_GITHUB_TOKEN environment variable."
  GITHUB_TOKEN="${AIHOME_GITHUB_TOKEN}"

# Priority 2: file path passed as first argument
elif [[ -n "${1:-}" && -f "$1" ]]; then
  log "Reading GitHub token from file: $1"
  GITHUB_TOKEN="$(cat "$1" | tr -d '[:space:]')"

# Priority 3: interactive prompt
else
  log "Enter your GitHub Personal Access Token (PAT)."
  log "Required scopes: repo, workflow, read:org"
  log "(Input is hidden)"
  echo -n "[setup-secrets] GitHub PAT: "
  read -rs GITHUB_TOKEN
  echo ""
fi

[[ -z "${GITHUB_TOKEN}" ]] && die "GitHub token cannot be empty."

# ─── Read Anthropic API key ───────────────────────────────────────────────────

ANTHROPIC_KEY=""

# Priority 1: environment variable
if [[ -n "${AIHOME_ANTHROPIC_KEY:-}" ]]; then
  log "Using Anthropic API key from AIHOME_ANTHROPIC_KEY environment variable."
  ANTHROPIC_KEY="${AIHOME_ANTHROPIC_KEY}"

# Priority 2: file path passed as second argument
elif [[ -n "${2:-}" && -f "$2" ]]; then
  log "Reading Anthropic API key from file: $2"
  ANTHROPIC_KEY="$(cat "$2" | tr -d '[:space:]')"

# Priority 3: interactive prompt
else
  log "Enter your Anthropic API key."
  log "(Input is hidden)"
  echo -n "[setup-secrets] Anthropic API key: "
  read -rs ANTHROPIC_KEY
  echo ""
fi

[[ -z "${ANTHROPIC_KEY}" ]] && die "Anthropic API key cannot be empty."

# ─── Write credentials ────────────────────────────────────────────────────────

log "Writing credentials..."

# GitHub token — used by gh CLI directly
GITHUB_TOKEN_FILE="${AGENT_HOME}/.github-token"
printf '%s' "${GITHUB_TOKEN}" > "${GITHUB_TOKEN_FILE}"
chown "${AGENT_USER}:${AGENT_USER}" "${GITHUB_TOKEN_FILE}"
chmod 600 "${GITHUB_TOKEN_FILE}"
log "GitHub token written to ${GITHUB_TOKEN_FILE} (600, owned by ${AGENT_USER})"

# Anthropic API key — used by Claude Code CLI
ANTHROPIC_KEY_FILE="${AGENT_HOME}/.anthropic-key"
printf '%s' "${ANTHROPIC_KEY}" > "${ANTHROPIC_KEY_FILE}"
chown "${AGENT_USER}:${AGENT_USER}" "${ANTHROPIC_KEY_FILE}"
chmod 600 "${ANTHROPIC_KEY_FILE}"
log "Anthropic key written to ${ANTHROPIC_KEY_FILE} (600, owned by ${AGENT_USER})"

# ─── Create systemd EnvironmentFile ───────────────────────────────────────────

log "Creating systemd EnvironmentFile at ${SECRETS_ENV}..."
mkdir -p "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}"

cat > "${SECRETS_ENV}" <<ENVFILE
# AI Fishtank — secrets environment file
# Loaded by: /etc/systemd/system/aifishtank-supervisor.service
# Managed by: vagrant/scripts/setup-secrets.sh
# DO NOT commit this file to Git.
#
# Raw secret values are NOT stored here. The supervisor reads secrets
# directly from the files listed below (config.sh loads them at runtime).

GITHUB_TOKEN_FILE=${GITHUB_TOKEN_FILE}
ANTHROPIC_KEY_FILE=${ANTHROPIC_KEY_FILE}
ENVFILE

chmod 600 "${SECRETS_ENV}"
log "Secrets env file written (600, owned by root)"

# ─── Configure gh CLI for agent user ──────────────────────────────────────────

log "Configuring gh CLI for agent user..."

# gh CLI stores auth in ~/.config/gh/hosts.yml
GH_CONFIG_DIR="${AGENT_HOME}/.config/gh"
mkdir -p "${GH_CONFIG_DIR}"
chown -R "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/.config"

# Use gh auth login with token via stdin (redirect from file — avoids token exposure in process list)
if su -s /bin/bash "${AGENT_USER}" -c \
  "gh auth login --with-token --hostname github.com" < "${GITHUB_TOKEN_FILE}" 2>&1; then
  log "gh CLI authentication successful"
else
  log "WARNING: gh auth login failed — token may be invalid or gh not installed yet"
fi

# ─── Validate GitHub token ────────────────────────────────────────────────────

log "Validating GitHub token..."
if su -s /bin/bash "${AGENT_USER}" -c "gh auth status 2>&1"; then
  log "GitHub token is valid."
else
  log "WARNING: GitHub token validation failed."
  log "         You can re-run this script after verifying the token."
fi

# ─── SSH deploy key (optional) ────────────────────────────────────────────────

SSH_KEY="${AGENT_HOME}/.ssh/id_ed25519"
if [[ ! -f "${SSH_KEY}" ]]; then
  log "Generating SSH deploy key for git operations..."
  mkdir -p "${AGENT_HOME}/.ssh"
  chmod 700 "${AGENT_HOME}/.ssh"
  su -s /bin/bash "${AGENT_USER}" -c \
    "ssh-keygen -t ed25519 -f '${SSH_KEY}' -N '' -C 'ai-fishtank-agent@$(hostname)'"
  log ""
  log "SSH public key (add this as a GitHub deploy key for your repositories):"
  log "─────────────────────────────────────────────────────────────"
  cat "${SSH_KEY}.pub"
  log "─────────────────────────────────────────────────────────────"
else
  log "SSH deploy key already exists at ${SSH_KEY}"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

log ""
log "==========================================================="
log "  Secrets setup complete."
log "==========================================================="
log ""
log "NEXT STEPS:"
log ""
log "  1. Start the AI Fishtank supervisor service:"
log "       sudo systemctl start aifishtank-supervisor"
log "       sudo systemctl status aifishtank-supervisor"
log ""
log "  2. If you generated a new SSH key above, add it as a GitHub"
log "     deploy key for each repository the agents will work with."
log "     Public key: ${SSH_KEY}.pub"
log ""
log "  3. Verify the supervisor is polling GitHub:"
log "       sudo journalctl -u aifishtank-supervisor -f"
log ""

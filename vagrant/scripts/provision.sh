#!/usr/bin/env bash
# provision.sh — AI Fishtank VM provisioning script
#
# Runs on: vagrant up (inside the VM via Vagrant shell provisioner)
# OS:      Ubuntu 24.04 LTS (Noble Numbat)
# Idempotent: yes — safe to run multiple times
#
# Usage: called automatically by Vagrant; can also be re-run manually:
#   sudo /vagrant/vagrant/scripts/provision.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="/var/log/aifishtank"
DATA_DIR="/var/lib/aifishtank"
AGENT_USER="agent"
AGENT_HOME="/home/${AGENT_USER}"

# ─── Helpers ─────────────────────────────────────────────────────────────────

log() {
  echo "[provision] $*"
}

# ─── 1. System update ─────────────────────────────────────────────────────────

log "Updating apt package lists..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

# ─── 2. Core packages (without Docker — added in step 2b) ────────────────────

log "Installing core packages..."
apt-get install -y -qq \
  curl \
  git \
  jq \
  unzip \
  wget \
  ca-certificates \
  gnupg \
  lsb-release \
  software-properties-common \
  postgresql-client \
  dnsmasq \
  conntrack \
  iptables-persistent \
  netfilter-persistent \
  rsync \
  cron \
  htop \
  net-tools

# ─── 2b. Docker from official Docker apt repo ────────────────────────────────

if ! command -v docker &>/dev/null; then
  log "Adding Docker official apt repository..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
    https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  log "Installing Docker Engine + Compose plugin..."
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
else
  log "Docker already installed: $(docker --version)"
fi

# ─── 3. yq (YAML processor) ───────────────────────────────────────────────────

if ! command -v yq &>/dev/null; then
  log "Installing yq..."
  YQ_VERSION="v4.43.1"
  YQ_ARCH="$(dpkg --print-architecture)"  # amd64 or arm64
  wget -qO /usr/local/bin/yq \
    "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${YQ_ARCH}"
  chmod +x /usr/local/bin/yq
else
  log "yq already installed: $(yq --version)"
fi

# ─── 4. Node.js 20.x ──────────────────────────────────────────────────────────

if ! command -v node &>/dev/null || [[ "$(node --version)" != v20* ]]; then
  log "Installing Node.js 20.x via NodeSource..."
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
else
  log "Node.js already installed: $(node --version)"
fi

# ─── 5. GitHub CLI ────────────────────────────────────────────────────────────

if ! command -v gh &>/dev/null; then
  log "Installing GitHub CLI..."
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] \
    https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq
  apt-get install -y -qq gh
else
  log "GitHub CLI already installed: $(gh --version | head -1)"
fi

# ─── 6. Claude Code CLI ───────────────────────────────────────────────────────

if ! command -v claude &>/dev/null; then
  log "Installing Claude Code CLI..."
  npm install -g @anthropic-ai/claude-code
else
  log "Claude Code CLI already installed: $(claude --version 2>/dev/null || echo 'unknown')"
fi

# ─── 7. agent user ────────────────────────────────────────────────────────────

if ! id "${AGENT_USER}" &>/dev/null; then
  log "Creating agent user..."
  useradd -m -s /bin/bash -G docker "${AGENT_USER}"
  # Restrict passwordless sudo to specific commands needed by the supervisor
  echo "${AGENT_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart aifishtank-supervisor, /usr/bin/systemctl status aifishtank-supervisor, /usr/bin/docker, /usr/bin/docker-compose" > "/etc/sudoers.d/${AGENT_USER}"
  chmod 440 "/etc/sudoers.d/${AGENT_USER}"
else
  log "User '${AGENT_USER}' already exists"
  # Ensure group memberships are correct (sudo group not needed — sudoers.d controls access)
  usermod -aG docker "${AGENT_USER}" 2>/dev/null || true
fi

# ─── 8. Directory structure ───────────────────────────────────────────────────

log "Creating directory structure..."

mkdir -p \
  "${DATA_DIR}" \
  "${DATA_DIR}/triggers" \
  "${DATA_DIR}/triggers/processed" \
  "${DATA_DIR}/blobs" \
  "${LOG_DIR}" \
  "${LOG_DIR}/agents" \
  "/var/log/aifishtank-export" \
  "/var/run/aifishtank" \
  "${AGENT_HOME}/repos" \
  "${AGENT_HOME}/config" \
  "${AGENT_HOME}/system" \
  "${AGENT_HOME}/.docker" \
  "/etc/aifishtank"

chown -R "${AGENT_USER}:${AGENT_USER}" \
  "${DATA_DIR}" \
  "${LOG_DIR}" \
  "/var/run/aifishtank" \
  "${AGENT_HOME}/repos" \
  "${AGENT_HOME}/config" \
  "${AGENT_HOME}/system" \
  "${AGENT_HOME}/.docker"

chmod 755 "${DATA_DIR}" "${LOG_DIR}"
chmod 700 "/etc/aifishtank"

# ─── 9. Docker configuration ──────────────────────────────────────────────────

log "Configuring Docker..."
systemctl enable docker
systemctl start docker

# Add vagrant user to docker group too (for convenience during development)
usermod -aG docker vagrant 2>/dev/null || true

# ─── 10. Git configuration for agent user ─────────────────────────────────────

log "Configuring git for agent user..."

# Pre-populate GitHub's SSH host key so StrictHostKeyChecking=yes works
# without manual approval on first connect.
mkdir -p "${AGENT_HOME}/.ssh"
chmod 700 "${AGENT_HOME}/.ssh"
ssh-keyscan github.com >> "${AGENT_HOME}/.ssh/known_hosts" 2>/dev/null
chown "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/.ssh/known_hosts"
chmod 644 "${AGENT_HOME}/.ssh/known_hosts"

if [[ ! -f "${AGENT_HOME}/.gitconfig" ]]; then
  cat > "${AGENT_HOME}/.gitconfig" <<'GITCFG'
[user]
    name = AI Fishtank Agents
    email = ai-fishtank@example.com

[core]
    sshCommand = ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=yes

[pull]
    rebase = false

[push]
    default = current
GITCFG
  chown "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/.gitconfig"
fi

# ─── 11. Network tracking ─────────────────────────────────────────────────────

log "Setting up network tracking..."
# Use the mounted path since Vagrant uploads provisioners to /tmp
bash "${AGENT_HOME}/ai-fishtank/vagrant/scripts/setup-network-tracking.sh"

# ─── 12. Systemd service for supervisor ───────────────────────────────────────

log "Installing aifishtank-supervisor systemd service..."
SYSTEMD_SRC="${AGENT_HOME}/ai-fishtank/supervisor/systemd/aifishtank-supervisor.service"
SYSTEMD_DEST="/etc/systemd/system/aifishtank-supervisor.service"

if [[ -f "${SYSTEMD_SRC}" ]]; then
  cp "${SYSTEMD_SRC}" "${SYSTEMD_DEST}"
  systemctl daemon-reload
  systemctl enable aifishtank-supervisor.service
  systemctl start aifishtank-supervisor.service || true
  log "Supervisor service enabled and started"
else
  log "WARNING: ${SYSTEMD_SRC} not found; skipping service install"
fi

# ─── 12a. Claude auth helper service ─────────────────────────────────────

log "Installing aifishtank-claude-auth systemd service..."
CLAUDE_AUTH_SRC="${AGENT_HOME}/ai-fishtank/supervisor/systemd/aifishtank-claude-auth.service"
CLAUDE_AUTH_DEST="/etc/systemd/system/aifishtank-claude-auth.service"

if [[ -f "${CLAUDE_AUTH_SRC}" ]]; then
  cp "${CLAUDE_AUTH_SRC}" "${CLAUDE_AUTH_DEST}"
  mkdir -p /var/lib/aifishtank/claude-ipc
  chown vagrant:vagrant /var/lib/aifishtank/claude-ipc
  systemctl daemon-reload
  systemctl enable aifishtank-claude-auth.service
  systemctl start aifishtank-claude-auth.service || true
  log "Claude auth helper service enabled and started"
else
  log "WARNING: ${CLAUDE_AUTH_SRC} not found; skipping claude-auth service install"
fi

# ─── 12b. System Docker Compose stack (auto-start on boot) ──────────────────

log "Installing aifishtank-stack systemd service..."
cat > /etc/systemd/system/aifishtank-stack.service <<'STACKUNIT'
[Unit]
Description=AI Fishtank System Docker Compose Stack
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=agent
Group=agent
WorkingDirectory=/home/agent/ai-fishtank/docker
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
STACKUNIT

systemctl daemon-reload
systemctl enable aifishtank-stack.service
systemctl start aifishtank-stack.service || true
log "System Docker Compose stack enabled and started"

# ─── 13. Make all supervisor scripts executable ───────────────────────────────

log "Setting executable permissions on scripts..."
SCRIPTS_BASE="${AGENT_HOME}/ai-fishtank"
if [[ -d "${SCRIPTS_BASE}" ]]; then
  find "${SCRIPTS_BASE}/supervisor/scripts" \
       "${SCRIPTS_BASE}/vagrant/scripts" \
       -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
fi

# ─── 14. Log rotation ─────────────────────────────────────────────────────────

log "Configuring log rotation..."
cat > /etc/logrotate.d/aifishtank <<'LOGROTATE'
/var/log/aifishtank/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
    sharedscripts
    postrotate
        systemctl reload dnsmasq 2>/dev/null || true
    endscript
}

/var/log/aifishtank/agents/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 agent agent
}
LOGROTATE

# ─── 15. Log export cron job ──────────────────────────────────────────────────

log "Installing log-export cron job..."
cat > /etc/cron.d/aifishtank-export-logs <<'CRON'
# Export aifishtank logs to shared folder every 5 minutes for host visibility
*/5 * * * * root rsync -a --delete /var/log/aifishtank/ /var/log/aifishtank-export/ 2>/dev/null
CRON

# ─── Done ─────────────────────────────────────────────────────────────────────

log ""
log "==========================================================="
log "  AI Fishtank provisioning complete."
log "==========================================================="
log ""
log "NEXT STEPS:"
log "  1. Run the secrets setup script (inside the VM):"
log "       sudo /home/agent/ai-fishtank/vagrant/scripts/setup-secrets.sh"
log ""
log "  2. After secrets are set, start the supervisor:"
log "       sudo systemctl start aifishtank-supervisor"
log ""
log "  3. Verify network tracking is active:"
log "       sudo systemctl status dnsmasq"
log "       sudo tail -f /var/log/aifishtank/dns-queries.log"
log ""
log "  4. Access services from the host browser:"
log "       http://localhost:8080  — Web UI dashboard"
log "       http://localhost:3000  — Grafana"
log "       http://localhost:9090  — Prometheus"
log ""

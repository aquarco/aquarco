#!/usr/bin/env bash
# provision.sh — Aquarco VM provisioning script
#
# Runs on: vagrant up (inside the VM via Vagrant shell provisioner)
# OS:      Ubuntu 24.04 LTS (Noble Numbat)
# Idempotent: yes — safe to run multiple times
#
# Usage: called automatically by Vagrant; can also be re-run manually:
#   sudo /vagrant/vagrant/scripts/provision.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="/var/log/aquarco"
DATA_DIR="/var/lib/aquarco"
AGENT_USER="agent"
AGENT_HOME="/home/${AGENT_USER}"

# Dev mode: set by the Vagrantfile via the DEV_MODE env variable.
DEV_MODE="${DEV_MODE:-0}"

# ─── Helpers ─────────────────────────────────────────────────────────────────

log() {
  echo "[provision] $*"
}

# ─── 1. System update ─────────────────────────────────────────────────────────

log "Updating apt package lists..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq

# ─── 1b. DNS — use public resolvers so Docker containers can reach the internet
#     VirtualBox NAT DNS (192.168.12.1) is unreliable; configure systemd-resolved
#     to use Google and Cloudflare DNS directly.

log "Configuring DNS resolvers..."
mkdir -p /etc/systemd/resolved.conf.d
cat > /etc/systemd/resolved.conf.d/public-dns.conf <<'DNS'
[Resolve]
DNS=8.8.8.8 1.1.1.1
FallbackDNS=8.8.4.4 1.0.0.1
DNS

systemctl restart systemd-resolved

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
  if [[ "${DEV_MODE}" == "1" ]]; then
    usermod -aG vboxsf "${AGENT_USER}" 2>/dev/null || true
  fi
else
  log "User '${AGENT_USER}' already exists"
  usermod -aG docker "${AGENT_USER}" 2>/dev/null || true
  if [[ "${DEV_MODE}" == "1" ]]; then
    usermod -aG vboxsf "${AGENT_USER}" 2>/dev/null || true
  fi
fi

# Ensure sudoers entry is always up to date (idempotent, covers already-provisioned VMs)
echo "${AGENT_USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart aquarco-supervisor-python, /usr/bin/systemctl status aquarco-supervisor-python, /usr/bin/docker, /usr/bin/docker-compose" > "/etc/sudoers.d/${AGENT_USER}"
chmod 440 "/etc/sudoers.d/${AGENT_USER}"

# ─── 8. Directory structure ───────────────────────────────────────────────────

log "Creating directory structure..."

mkdir -p \
  "${DATA_DIR}" \
  "${DATA_DIR}/triggers" \
  "${DATA_DIR}/triggers/processed" \
  "${DATA_DIR}/blobs" \
  "${DATA_DIR}/backups" \
  "${LOG_DIR}" \
  "${LOG_DIR}/agents" \
  "/var/log/aquarco-export" \
  "${AGENT_HOME}/repos" \
  "${AGENT_HOME}/config" \
  "${AGENT_HOME}/system" \
  "${AGENT_HOME}/.docker" \
  "${AGENT_HOME}/.claude" \
  "/etc/aquarco"

# /var/run/aquarco is NOT created here — it lives on tmpfs and is managed by
# systemd RuntimeDirectory= in the supervisor service unit (created on every start).

chown "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}"
chown -R "${AGENT_USER}:${AGENT_USER}" \
  "${DATA_DIR}" \
  "${LOG_DIR}" \
  "${AGENT_HOME}/repos" \
  "${AGENT_HOME}/config" \
  "${AGENT_HOME}/system" \
  "${AGENT_HOME}/.docker" \
  "${AGENT_HOME}/.claude"

chmod 755 "${DATA_DIR}" "${LOG_DIR}"
chmod 700 "${DATA_DIR}/backups"
# /etc/aquarco: root:agent 750 — root writes, agent reads (docker-secrets.env, env)
chmod 750 "/etc/aquarco"
chown root:agent "/etc/aquarco"
chmod 700 "${AGENT_HOME}/.claude"

# ─── 8a. Docker compose files (non-dev mode only) ────────────────────────────
# In dev mode the synced folder at /home/agent/aquarco provides these files.
# In production mode they are uploaded to /tmp/aquarco-docker by the Vagrantfile
# file provisioner and installed here.

if [[ "${DEV_MODE}" != "1" ]]; then
  if [[ -d /tmp/aquarco-docker ]]; then
    mkdir -p "${AGENT_HOME}/aquarco/docker"
    cp -r /tmp/aquarco-docker/. "${AGENT_HOME}/aquarco/docker/"
    chown -R "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/aquarco"
    log "Docker compose files installed to ${AGENT_HOME}/aquarco/docker/"
  else
    log "WARNING: /tmp/aquarco-docker not found; aquarco-stack.service may fail to start"
  fi
fi

# ─── 8b. Postgres credentials ─────────────────────────────────────────────────
# Generate once; idempotent — never overwrites an existing file so the DB
# password stays stable across re-provisions.

log "Checking Postgres credentials..."
if [[ ! -f /etc/aquarco/docker-secrets.env ]]; then
  POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 32)
  cat > /etc/aquarco/docker-secrets.env <<EOF
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DATABASE_URL=postgresql://aquarco:${POSTGRES_PASSWORD}@postgres:5432/aquarco
EOF
  chown root:agent /etc/aquarco/docker-secrets.env
  chmod 640 /etc/aquarco/docker-secrets.env
  log "Postgres credentials generated at /etc/aquarco/docker-secrets.env"
else
  log "Postgres credentials already exist — skipping"
fi

# Generate secrets.env for the supervisor (DATABASE_URL pointing to localhost for host-side services)
if [[ ! -f /etc/aquarco/secrets.env ]]; then
  source /etc/aquarco/docker-secrets.env
  PG_PASS="${POSTGRES_PASSWORD:-aquarco}"
  cat > /etc/aquarco/secrets.env <<EOF
DATABASE_URL=postgresql://aquarco:${PG_PASS}@localhost:5432/aquarco
EOF
  chown root:agent /etc/aquarco/secrets.env
  chmod 640 /etc/aquarco/secrets.env
  log "Supervisor secrets.env generated at /etc/aquarco/secrets.env"
fi

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
chmod 644 "${AGENT_HOME}/.ssh/known_hosts"
# Ensure .ssh and all contents are owned by agent (this script runs as root)
chown -R "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/.ssh"

if [[ ! -f "${AGENT_HOME}/.gitconfig" ]]; then
  cat > "${AGENT_HOME}/.gitconfig" <<'GITCFG'
[user]
    name = Aquarco Agents
    email = aquarco@example.com

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
if [[ "${DEV_MODE}" == "1" ]]; then
  bash "${AGENT_HOME}/aquarco/vagrant/scripts/setup-network-tracking.sh"
else
  bash "${SCRIPT_DIR}/setup-network-tracking.sh"
fi

# ─── 11b. Install Python supervisor package ──────────────────────────────────

log "Installing aquarco-supervisor Python package..."
apt-get install -y -qq python3-pip python3-venv
python3 -m venv "${AGENT_HOME}/.venv"
chown -R "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/.venv"
chmod -R u+w "${AGENT_HOME}/.venv/lib/"

if [[ "${DEV_MODE}" == "1" ]]; then
  log "  [dev] editable install from mounted source..."
  su - "${AGENT_USER}" -c "${AGENT_HOME}/.venv/bin/pip install -e /home/agent/aquarco/supervisor/python/" || {
    log "WARNING: pip install failed; supervisor CLI may not be available"
  }
else
  log "  [prod] installing aquarco-supervisor from bundled package..."
  su - "${AGENT_USER}" -c "${AGENT_HOME}/.venv/bin/pip install /tmp/aquarco-supervisor-python/" || {
    log "WARNING: pip install failed; supervisor CLI may not be available"
  }
fi

# Lock the supervisor venv so agents cannot accidentally mutate it.
# NOTE: To upgrade supervisor dependencies, temporarily restore write
# permissions first:  chmod -R u+w "${AGENT_HOME}/.venv/lib/"
chmod -R a-w "${AGENT_HOME}/.venv/lib/"
log "Supervisor venv locked (read-only lib/)"

# ─── 11c. Separate virtualenv for agent task execution ───────────────────────
# Agents run pip install / pip install -e inside tasks. Giving them their own
# venv keeps the supervisor runtime untouched.

log "Creating agent task-execution venv..."
python3 -m venv "${AGENT_HOME}/.agent-venv"
chown -R "${AGENT_USER}:${AGENT_USER}" "${AGENT_HOME}/.agent-venv"
log "Agent venv ready at ${AGENT_HOME}/.agent-venv"

# ─── 12. Systemd service for supervisor ───────────────────────────────────────

log "Installing aquarco-supervisor (Python) systemd service..."
SYSTEMD_DEST="/etc/systemd/system/aquarco-supervisor-python.service"

# Disable the old bash supervisor service if it exists
systemctl disable --now aquarco-supervisor.service 2>/dev/null || true
rm -f /etc/systemd/system/aquarco-supervisor.service

if [[ "${DEV_MODE}" == "1" ]]; then
  SYSTEMD_SRC="${AGENT_HOME}/aquarco/supervisor/systemd/aquarco-supervisor-python.service"
  if [[ -f "${SYSTEMD_SRC}" ]]; then
    cp "${SYSTEMD_SRC}" "${SYSTEMD_DEST}"
  else
    log "WARNING: ${SYSTEMD_SRC} not found; skipping service install"
  fi
else
  # Production: write service file inline (config lives at /etc/aquarco/)
  cat > "${SYSTEMD_DEST}" <<'SVCUNIT'
[Unit]
Description=Aquarco Agent Supervisor (Python)
After=aquarco-stack.service docker.service network.target
Wants=aquarco-stack.service

[Service]
Type=simple
User=agent
Group=agent
ExecStart=/home/agent/.venv/bin/aquarco-supervisor run --config /etc/aquarco/supervisor.yaml
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=10
TimeoutStopSec=60
WorkingDirectory=/home/agent
Environment=PATH=/home/agent/.venv/bin:/usr/local/bin:/usr/bin:/bin:/home/agent/.npm-global/bin
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/aquarco/secrets.env
MemoryMax=4G
TasksMax=128
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/agent /tmp
RuntimeDirectory=aquarco
LogsDirectory=aquarco aquarco/agents
StateDirectory=aquarco
StandardOutput=journal
StandardError=journal
SyslogIdentifier=aquarco-supervisor-python

[Install]
WantedBy=multi-user.target
SVCUNIT
fi

if [[ -f "${SYSTEMD_DEST}" ]]; then
  systemctl daemon-reload
  systemctl enable aquarco-supervisor-python.service
  systemctl start aquarco-supervisor-python.service || true
  log "Supervisor (Python) service enabled and started"
fi

# ─── 12a. Claude auth helper service ─────────────────────────────────────

log "Installing aquarco-claude-auth systemd service..."
CLAUDE_AUTH_DEST="/etc/systemd/system/aquarco-claude-auth.service"

if [[ "${DEV_MODE}" == "1" ]]; then
  CLAUDE_AUTH_SRC="${AGENT_HOME}/aquarco/supervisor/systemd/aquarco-claude-auth.service"
  if [[ -f "${CLAUDE_AUTH_SRC}" ]]; then
    cp "${CLAUDE_AUTH_SRC}" "${CLAUDE_AUTH_DEST}"
  else
    log "WARNING: ${CLAUDE_AUTH_SRC} not found; skipping claude-auth service install"
  fi
else
  # Production: script is installed by the pip package to ~/.local/bin or .venv/bin
  cat > "${CLAUDE_AUTH_DEST}" <<'AUTHUNIT'
[Unit]
Description=Aquarco Claude Auth Helper
After=network.target

[Service]
Type=simple
User=agent
Group=agent
ExecStartPre=+/bin/bash -c "mkdir -p /home/agent/.claude && chown -R agent:agent /home/agent/.claude && chmod 700 /home/agent/.claude"
ExecStart=/home/agent/.venv/bin/aquarco-claude-auth
Restart=on-failure
RestartSec=5
WorkingDirectory=/home/agent
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/home/agent/.local/bin:/home/agent/.npm-global/bin
MemoryMax=512M
TasksMax=128
NoNewPrivileges=true
ProtectSystem=full
StandardOutput=journal
StandardError=journal
SyslogIdentifier=aquarco-claude-auth

[Install]
WantedBy=multi-user.target
AUTHUNIT
fi

if [[ -f "${CLAUDE_AUTH_DEST}" ]]; then
  mkdir -p /var/lib/aquarco/claude-ipc
  chown agent:agent /var/lib/aquarco/claude-ipc
  chmod 0770 /var/lib/aquarco/claude-ipc
  systemctl daemon-reload
  systemctl enable aquarco-claude-auth.service
  systemctl start aquarco-claude-auth.service || true
  log "Claude auth helper service enabled and started"
fi

# ─── 12b. System Docker Compose stack (auto-start on boot) ──────────────────

log "Writing /etc/aquarco/env (build environment marker)..."
if [[ ! -f /etc/aquarco/env ]]; then
  echo "${AQUARCO_DOCKER_MODE:-development}" > /etc/aquarco/env
fi

log "Installing aquarco-stack systemd service..."
cat > /etc/systemd/system/aquarco-stack.service <<'STACKUNIT'
[Unit]
Description=Aquarco System Docker Compose Stack
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
User=agent
Group=agent
WorkingDirectory=/home/agent/aquarco/docker
ExecStart=/bin/bash -c '\
  AQUARCO_ENV=$(cat /etc/aquarco/env 2>/dev/null || echo development); \
  set -a; \
  [ -f /etc/aquarco/docker-secrets.env ] && . /etc/aquarco/docker-secrets.env; \
  [ -f /home/agent/aquarco/docker/versions.env ] && . /home/agent/aquarco/docker/versions.env; \
  if [ "$AQUARCO_ENV" = "production" ]; then \
    exec /usr/bin/docker compose -f compose.prod.yml up -d; \
  else \
    exec /usr/bin/docker compose -f compose.yml -f compose.dev.yml up -d; \
  fi'
ExecStop=/bin/bash -c '\
  AQUARCO_ENV=$(cat /etc/aquarco/env 2>/dev/null || echo development); \
  if [ "$AQUARCO_ENV" = "production" ]; then \
    /usr/bin/docker compose -f compose.prod.yml down; \
  else \
    /usr/bin/docker compose -f compose.yml -f compose.dev.yml down; \
  fi'
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
STACKUNIT

systemctl daemon-reload
systemctl enable aquarco-stack.service
# Use restart (not start) so that re-provisions pick up an updated versions.env.
# On a fresh VM this is equivalent to start; on a running VM it forces a reload.
systemctl restart aquarco-stack.service || true
log "System Docker Compose stack enabled and started"

# ─── 13. Make all supervisor scripts executable ───────────────────────────────

if [[ "${DEV_MODE}" == "1" ]]; then
  log "Setting executable permissions on scripts..."
  SCRIPTS_BASE="${AGENT_HOME}/aquarco"
  if [[ -d "${SCRIPTS_BASE}" ]]; then
    find "${SCRIPTS_BASE}/supervisor/scripts" \
         "${SCRIPTS_BASE}/vagrant/scripts" \
         -name "*.sh" -exec chmod +x {} \; 2>/dev/null || true
  fi
fi

# ─── 14. Log rotation ─────────────────────────────────────────────────────────

log "Configuring log rotation..."
cat > /etc/logrotate.d/aquarco <<'LOGROTATE'
/var/log/aquarco/*.log {
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

/var/log/aquarco/agents/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0644 agent agent
}
LOGROTATE

# ─── 15. Log export cron job (dev only — requires host-mounted log folder) ────

if [[ "${DEV_MODE}" == "1" ]]; then
  log "Installing log-export cron job..."
  cat > /etc/cron.d/aquarco-export-logs <<'CRON'
# Export aquarco logs to shared folder every 5 minutes for host visibility
*/5 * * * * root rsync -a --delete /var/log/aquarco/ /var/log/aquarco-export/ 2>/dev/null
CRON
fi

# ─── Done ─────────────────────────────────────────────────────────────────────

log ""
log "==========================================================="
log "  Aquarco provisioning complete."
log "==========================================================="
log ""
log "Access the Web UI to log in to GitHub and Claude:"
log "  http://localhost:8080"
log ""
log "Other services:"
log "  http://localhost:13000 — Grafana"
log "  http://localhost:9090  — Prometheus"
log "  http://localhost:8081  — Adminer (DB UI)"
log ""

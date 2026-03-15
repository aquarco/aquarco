# TASK-001: VirtualBox Sandbox Architecture Design

**Status**: open
**Created**: 2026-03-14
**Triggered by**: manual (initial architecture design request)
**Agents involved**: solution-architect, dev-infra, security, scripting

## Context

AI Fishtank is a sandboxed VirtualBox VM where multiple specialized AI agent groups operate with no restrictions internally. The sole external interface is a GitHub repository — this is the security boundary. The project has a multi-agent system with 13 agents that need to run autonomously inside the VM.

The VM must host:
- Docker Compose stacks (one per target repo, dev-style with source mounts and hot reload)
- PostgreSQL databases (per-repo)
- Next.js + React + MUI frontends
- GraphQL API backends
- All 13 AI agents operating autonomously
- Monitoring stack (Prometheus, Grafana, Loki)

## Objective

Design a complete, reproducible VirtualBox sandbox architecture that:
1. Provides full autonomy to AI agents inside the VM
2. Enforces GitHub as the only external communication channel
3. Supports multiple target repos, each with its own Docker Compose stack
4. Uses dev-style source mounts with hot reload (no docker build)
5. Is fully reproducible via Infrastructure as Code

---

## 1. VM Configuration

### Base OS
**Ubuntu Server 24.04 LTS (Noble Numbat)** — Recommended

Rationale:
- Long-term support (until 2029)
- Excellent Docker support
- Minimal footprint (server edition, no GUI)
- Wide community support for automation tooling
- Cloud-init native support for provisioning

### Resource Allocation

| Resource | Minimum | Recommended | Notes |
|----------|---------|-------------|-------|
| CPU | 4 cores | 6 cores | Docker Compose stacks need parallelism |
| RAM | 8 GB | 12 GB | PostgreSQL, Node.js apps are memory-hungry |
| Disk | 50 GB | 80 GB | Multiple repos, database, logs grow over time |
| Video | 16 MB | 16 MB | Headless, no GUI needed |
| GPU | None | None | Not needed for current agent workloads, may be added later |

### VirtualBox Settings

```
VM Type: Linux / Ubuntu (64-bit)
Chipset: ICH9 (for modern guest additions)
EFI: Enabled (UEFI boot)
Paravirtualization: KVM
Nested VT-x/AMD-V: Enabled (if running nested containers)
Audio: Disabled
USB: Disabled
Serial Port: Disabled (reduce attack surface)
```

### NAT Port Forwarding

Port forwarding enables access to repo UIs from the host browser:

```bash
# Web UI dashboard (TASK-002)
VBoxManage modifyvm "aifishtank" --natpf1 "webui,tcp,,8080,,8080"

# Per-repo ports (added dynamically by supervisor when repos are registered)
VBoxManage modifyvm "aifishtank" --natpf1 "repo1-fe,tcp,,3001,,3001"
VBoxManage modifyvm "aifishtank" --natpf1 "repo1-api,tcp,,4001,,4001"
VBoxManage modifyvm "aifishtank" --natpf1 "repo2-fe,tcp,,3002,,3002"
VBoxManage modifyvm "aifishtank" --natpf1 "repo2-api,tcp,,4002,,4002"
# ... dynamic, added by supervisor when repos are registered
```

---

## 2. Network Access: Open with Tracking

### Network Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      HOST MACHINE                         │
│                                                           │
│  localhost:8080 ──┐                                       │
│  localhost:3001 ──┤  VirtualBox NAT                       │
│  localhost:4001 ──┤  Port Forwarding                      │
│  ...            ──┤                                       │
│                   │                                       │
│  ┌────────────────┴───────────────────────────────────┐   │
│  │              AI HOME VM                             │   │
│  │                                                     │   │
│  │  ┌─────────────────────────────────────────────┐   │   │
│  │  │  dnsmasq (local resolver + DNS logger)       │   │   │
│  │  │  ┌─────────────────────────────────────┐     │   │   │
│  │  │  │  Logs all DNS queries               │     │   │   │
│  │  │  │  → /var/log/aifishtank/dns-queries.log  │     │   │   │
│  │  │  └─────────────────────────────────────┘     │   │   │
│  │  └─────────────────────────────────────────────┘   │   │
│  │                                                     │   │
│  │  ┌─────────────────────────────────────────────┐   │   │
│  │  │  conntrack (connection logger)               │   │   │
│  │  │  → /var/log/aifishtank/connections.log           │   │   │
│  │  └─────────────────────────────────────────────┘   │   │
│  │                                                     │   │
│  │  Full outbound internet access (no restrictions)    │   │
│  │  All traffic tracked for observability              │   │
│  └─────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

### VirtualBox Network Mode

**NAT** (not NAT Network or Bridged)

Rationale:
- VM gets internet through host's NAT
- No direct exposure to LAN
- Port forwarding allows host browser access to repo UIs

### Network Tracking Strategy

The VM has unrestricted outbound internet access. Instead of blocking, all network activity is logged and tracked for observability.

**What is tracked:**
- All DNS queries are logged for domain tracking
- HTTP/HTTPS traffic is logged by domain
- A tracking dashboard shows which domains are accessed, how often, and by which process

### Implementation Options

**Option 1 — DNS logging via local resolver (Recommended):**
- Run a local DNS resolver (dnsmasq or unbound) that logs all queries
- All VM DNS goes through this resolver
- Parse logs to build domain usage report

**Option 2 — conntrack + iptables logging (no blocking, just logging):**
```bash
# Log all outbound connections (don't block, just log)
iptables -A OUTPUT -m state --state NEW -j LOG --log-prefix "OUTBOUND: " --log-level info
```

**Option 3 — Transparent proxy logging:**
- mitmproxy or squid in logging-only mode
- Captures full URLs, not just domains
- More detail but more overhead

**Recommended approach**: DNS logging via dnsmasq (lightweight, captures all domain lookups) + conntrack for connection-level tracking. No proxy overhead.

### Tracking Output

**Real-time log**: `/var/log/aifishtank/network-access.log`

```
# Example network-access.log format
2026-03-14T10:30:00Z DNS github.com 140.82.121.4 pid=1234 cmd=git
2026-03-14T10:30:01Z DNS registry.npmjs.org 104.16.25.35 pid=5678 cmd=npm
2026-03-14T10:30:05Z DNS api.anthropic.com 104.18.32.68 pid=9012 cmd=claude
```

**Daily summary report**: domains accessed, frequency, first/last seen

**Web UI integration**: Available in the Web UI (from TASK-002) as a "Network" tab

**Future use**: Once enough data is collected, the tracking data can be used to build an informed firewall whitelist if desired.

---

## 3. Internal Architecture

### Layered Service Stack

```
┌────────────────────────────────────────────────────────────────┐
│                         AI HOME VM                             │
├────────────────────────────────────────────────────────────────┤
│  Layer 4: AI Agent Runtime                                     │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Claude Code CLI (13 agents)                             │  │
│  │  Supervisor service (manages agents + Docker stacks)     │  │
│  │  - solution-architect (coordinator)                      │  │
│  │  - ralph, docs, e2e, database, qa, testing, security,   │  │
│  │    scripting, dev-infra, graphql, frontend               │  │
│  └──────────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────────┤
│  Layer 3: Application Stacks (Docker Compose per repo)        │
│  ┌─────────────────────┐  ┌─────────────────────────────────┐  │
│  │  my-saas-app        │  │  internal-api                   │  │
│  │  ├─ frontend :3001  │  │  ├─ frontend :3002              │  │
│  │  ├─ api      :4001  │  │  ├─ api      :4002              │  │
│  │  └─ postgres :5433  │  │  └─ postgres :5434              │  │
│  └─────────────────────┘  └─────────────────────────────────┘  │
│                                                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  System Stacks (monitoring, web-ui)                      │  │
│  │  ├─ web-ui      :8080                                    │  │
│  │  ├─ prometheus  :9090                                    │  │
│  │  ├─ grafana     :3000                                    │  │
│  │  └─ loki        :3100                                    │  │
│  └──────────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────────┤
│  Layer 2: Docker Engine                                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Docker Engine with Compose plugin                       │  │
│  │  - Source mounts for hot reload                          │  │
│  │  - Named volumes for node_modules and data               │  │
│  └──────────────────────────────────────────────────────────┘  │
├────────────────────────────────────────────────────────────────┤
│  Layer 1: Base OS                                              │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Ubuntu Server 24.04 LTS                                 │  │
│  │  - systemd, iptables, sshd (local only), cron            │  │
│  └──────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

### Docker Compose as the ONLY Runtime

Key principles:
- **Dev-style with source mounts and hot reload** — no docker build required
- Each service uses a base image (node:20, postgres:16, etc.) with source code mounted in
- Changes to source code are immediately reflected via hot reload (nodemon, next dev, etc.)
- `node_modules` in named volumes (not mounted from host) to avoid platform mismatch

### Multi-Repo Docker Compose Support

Each target repo gets its own Docker Compose stack. Directory structure:

```
/home/agent/repos/
├── my-saas-app/
│   ├── docker-compose.yaml      ← per-repo compose file
│   ├── frontend/
│   │   └── src/
│   ├── api/
│   │   └── src/
│   └── ...
├── internal-api/
│   ├── docker-compose.yaml
│   ├── frontend/
│   ├── api/
│   └── ...
└── ...
```

Each compose stack runs independently with:
- Its own frontend (Next.js) on a unique port
- Its own API (GraphQL) on a unique port
- Its own PostgreSQL instance (or shared, configurable)
- Its own volumes

### Port Allocation Strategy

Each repo gets a port range. UIs and APIs are accessible from the host via VirtualBox NAT port forwarding:

```
Host browser → localhost:3001 → VirtualBox NAT → VM:3001 → my-saas-app frontend
Host browser → localhost:3002 → VirtualBox NAT → VM:3002 → internal-api frontend
Host browser → localhost:4001 → VirtualBox NAT → VM:4001 → my-saas-app API
Host browser → localhost:4002 → VirtualBox NAT → VM:4002 → internal-api API
```

Port ranges per repo (configurable in supervisor.yaml):

```yaml
# /home/agent/config/supervisor.yaml
repositories:
  - name: my-saas-app
    path: /home/agent/repos/my-saas-app
    ports:
      frontend: 3001
      api: 4001
      postgres: 5433
  - name: internal-api
    path: /home/agent/repos/internal-api
    ports:
      frontend: 3002
      api: 4002
      postgres: 5434
```

The Web UI (from TASK-002) runs on port 8080 as the central dashboard.

### Docker Compose Template

Template `docker-compose.yaml` that repos can customize:

```yaml
# Template docker-compose for a target repo
services:
  frontend:
    image: node:20-alpine
    working_dir: /app
    command: npm run dev
    ports:
      - "${FRONTEND_PORT:-3000}:3000"
    volumes:
      - ./frontend:/app          # Source mount, hot reload
      - frontend_modules:/app/node_modules
    environment:
      - GRAPHQL_URL=http://api:4000/graphql
    depends_on:
      - api

  api:
    image: node:20-alpine
    working_dir: /app
    command: npm run dev
    ports:
      - "${API_PORT:-4000}:4000"
    volumes:
      - ./api:/app               # Source mount, hot reload
      - api_modules:/app/node_modules
    environment:
      - DATABASE_URL=postgresql://dev:dev@postgres:5432/dev

  postgres:
    image: postgres:16-alpine
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    environment:
      - POSTGRES_USER=dev
      - POSTGRES_PASSWORD=dev
      - POSTGRES_DB=dev

volumes:
  frontend_modules:
  api_modules:
  pgdata:
```

Key principles:
- **NO Dockerfile needed** — use base images with volume mounts
- `node_modules` in named volumes (not mounted from host) to avoid platform mismatch
- Environment variables for port configuration
- Hot reload via `npm run dev` (next dev, nodemon, ts-node-dev, etc.)

### Docker Compose Lifecycle Management

The supervisor manages Docker Compose stacks:

| Action | Command |
|--------|---------|
| Start stack | `docker compose -f /home/agent/repos/<repo>/docker-compose.yaml up -d` |
| Restart service | `docker compose -f ... restart <service>` |
| Stop stack | `docker compose -f ... down` |
| Health check | `docker compose -f ... ps` |
| Logs | `docker compose -f ... logs -f <service>` |

The supervisor automatically:
- Runs `docker compose up -d` when a repo is registered
- Runs `docker compose restart <service>` after significant changes
- Runs `docker compose down` when a repo is removed
- Monitors health via `docker compose ps`

---

## 4. Agent Execution Model

### Agent Runtime Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT SUPERVISOR                         │
│                    (systemd service)                        │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  agent-supervisor.service                             │  │
│  │  - Monitors GitHub for events (polling or webhooks)   │  │
│  │  - Spawns Claude Code CLI with appropriate agent      │  │
│  │  - Manages agent lifecycles                           │  │
│  │  - Manages Docker Compose stacks per repo             │  │
│  │  - Logs all agent activity                            │  │
│  └───────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│                    AGENT INSTANCES                          │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐            │
│  │ solution-   │ │  database   │ │  frontend   │  ...       │
│  │ architect   │ │   agent     │ │   agent     │            │
│  │ (claude)    │ │  (claude)   │ │  (claude)   │            │
│  └─────────────┘ └─────────────┘ └─────────────┘            │
├─────────────────────────────────────────────────────────────┤
│                    SHARED RESOURCES                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  - Git working directory: /home/agent/repos/          │  │
│  │  - Task files: /home/agent/ai-fishtank/tasks/             │  │
│  │  - PRD: /home/agent/ai-fishtank/prd.json                  │  │
│  │  - Supervisor config: /home/agent/config/supervisor.yaml│  │
│  │  - Logs: /var/log/aifishtank/agents/                      │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Agent Supervisor Service

```ini
# /etc/systemd/system/aifishtank-supervisor.service
[Unit]
Description=AI Fishtank Agent Supervisor
After=network.target docker.service

[Service]
Type=simple
User=agent
Group=agent
WorkingDirectory=/home/agent/ai-fishtank
ExecStart=/usr/local/bin/aifishtank-supervisor
Restart=always
RestartSec=10
Environment=GITHUB_TOKEN_FILE=/home/agent/.github-token
Environment=ANTHROPIC_API_KEY_FILE=/home/agent/.anthropic-key

[Install]
WantedBy=multi-user.target
```

### Event Loop (Supervisor Logic)

```
LOOP forever:
    1. git fetch origin
    2. IF new commits on main:
        - git pull
        - Detect changed files
        - Invoke solution-architect agent with change context
    3. IF new GitHub issues/PRs (via gh CLI):
        - Parse event type
        - Route to appropriate agent
    4. FOR each registered repo:
        - Check Docker Compose stack health
        - Restart unhealthy services
    5. Sleep 60 seconds (polling interval)
```

### Agent Permissions

| Agent | File Access | Docker Compose | Git Push |
|-------|-------------|----------------|----------|
| solution-architect | Full | No | Yes |
| database | /migrations, prd.json | Yes | Yes |
| dev-infra | docker-compose.yml, config/ | Yes | Yes |
| security | Full (read) | No | Yes |
| frontend | /frontend, prd.json | Yes | Yes |
| graphql | /api, prd.json | Yes | Yes |
| testing | /tests, prd.json | Yes | Yes |
| scripting | /scripts, Makefile | Yes | Yes |
| docs | *.md, prd.json | No | Yes |
| qa | Full (read) | No | Yes |
| e2e | /e2e, playwright | Yes | Yes |
| ralph | prd.json only | No | Yes |

---

## 5. GitHub Integration

### Authentication

**Deploy Key** (SSH) — For git operations
- Read/write access to the repository
- Stored at `/home/agent/.ssh/id_ed25519`
- No passphrase (automated access)

**Personal Access Token (PAT)** — For GitHub API
- Scope: `repo`, `workflow`, `read:org`
- Stored at `/home/agent/.github-token`
- Used by `gh` CLI

### Git Configuration

```bash
# /home/agent/.gitconfig
[user]
    name = AI Fishtank Agents
    email = ai-fishtank@example.com

[core]
    sshCommand = ssh -i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new

[pull]
    rebase = false

[push]
    default = current
```

### Communication Patterns

**Inbound (GitHub -> VM):**
1. **Polling** (Recommended for simplicity)
   - Supervisor polls `git fetch` + `gh api` every 60 seconds
   - No inbound ports needed
   - Works with NAT network mode

2. **Webhook** (Higher complexity)
   - Requires exposing a port or using smee.io relay
   - Adds attack surface
   - Not recommended for this architecture

**Outbound (VM -> GitHub):**
- `git push` for code changes
- `gh pr create`, `gh issue comment` for interactions
- All via HTTPS or SSH (allowed by firewall)

### Commit Signing

```bash
# Optional: Sign commits with SSH key
[gpg]
    format = ssh

[gpg "ssh"]
    defaultKeyCommand = ssh-add -L

[commit]
    gpgsign = true
```

---

## 6. Provisioning & Reproducibility

### Tooling Stack

```
Vagrant (VM orchestration)
    └── cloud-init (OS provisioning)
        └── Ansible (configuration management)
            └── Shell scripts (application setup)
```

### Vagrantfile

```ruby
# Vagrantfile
Vagrant.configure("2") do |config|
  config.vm.box = "ubuntu/noble64"
  config.vm.hostname = "aifishtank"

  config.vm.provider "virtualbox" do |vb|
    vb.name = "ai-fishtank"
    vb.memory = 12288
    vb.cpus = 6
    vb.customize ["modifyvm", :id, "--nested-hw-virt", "on"]
  end

  # NAT port forwarding for web UI and repo UIs
  config.vm.network "forwarded_port", guest: 8080, host: 8080  # Web UI
  config.vm.network "forwarded_port", guest: 3001, host: 3001  # Repo 1 frontend
  config.vm.network "forwarded_port", guest: 4001, host: 4001  # Repo 1 API
  config.vm.network "forwarded_port", guest: 3002, host: 3002  # Repo 2 frontend
  config.vm.network "forwarded_port", guest: 4002, host: 4002  # Repo 2 API
  config.vm.network "forwarded_port", guest: 9090, host: 9090  # Prometheus
  config.vm.network "forwarded_port", guest: 3000, host: 3000  # Grafana

  # Provisioning
  config.vm.provision "file", source: "cloud-init.yaml", destination: "/tmp/cloud-init.yaml"
  config.vm.provision "shell", path: "scripts/provision.sh"
end
```

### Cloud-Init Configuration

```yaml
# cloud-init.yaml
#cloud-config
package_update: true
package_upgrade: true

packages:
  - curl
  - git
  - docker.io
  - docker-compose-v2
  - dnsmasq
  - conntrack
  - fail2ban
  - jq

users:
  - name: agent
    groups: [docker, sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys: []  # No SSH access from outside

write_files:
  - path: /etc/dnsmasq.d/logging.conf
    content: |
      # Log all DNS queries for network tracking
      log-queries
      log-facility=/var/log/aifishtank/dns-queries.log

runcmd:
  - systemctl enable docker
  - systemctl start docker
  - systemctl enable dnsmasq
  - systemctl start dnsmasq
  - mkdir -p /var/log/aifishtank
```

### Ansible Playbook Structure

```
ansible/
├── playbook.yml
├── inventory.yml
├── roles/
│   ├── base/           # OS hardening, packages
│   ├── docker/         # Docker + Compose
│   ├── network-tracking/  # dnsmasq, conntrack, logging setup
│   ├── agent-user/     # Agent user, SSH keys
│   ├── github-cli/     # gh CLI installation
│   ├── claude-cli/     # Claude Code CLI
│   ├── supervisor/     # Agent supervisor service
│   └── monitoring/     # Prometheus, Grafana, Loki (Docker Compose)
└── vars/
    └── secrets.yml     # Encrypted with ansible-vault
```

### Bootstrap Script

```bash
#!/bin/bash
# scripts/bootstrap.sh — Run on host to create VM

set -euo pipefail

# Prerequisites check
command -v vagrant >/dev/null || { echo "Install Vagrant first"; exit 1; }
command -v VBoxManage >/dev/null || { echo "Install VirtualBox first"; exit 1; }

# Generate SSH keypair for deploy key
if [[ ! -f keys/deploy_key ]]; then
    mkdir -p keys
    ssh-keygen -t ed25519 -f keys/deploy_key -N "" -C "ai-fishtank-deploy"
    echo "Add this deploy key to GitHub:"
    cat keys/deploy_key.pub
fi

# Create secrets file
if [[ ! -f ansible/vars/secrets.yml ]]; then
    echo "Creating secrets file..."
    cat > ansible/vars/secrets.yml << 'EOF'
github_token: "YOUR_PAT_HERE"
anthropic_api_key: "YOUR_API_KEY_HERE"
EOF
    echo "Edit ansible/vars/secrets.yml with your credentials"
fi

# Start VM
vagrant up

# Run Ansible
ansible-playbook -i ansible/inventory.yml ansible/playbook.yml
```

---

## 7. Monitoring & Observability

### Internal Monitoring Stack (Docker Compose)

```yaml
# /home/agent/system/monitoring/docker-compose.yaml
services:
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin

  loki:
    image: grafana/loki:latest
    ports:
      - "3100:3100"
    volumes:
      - loki_data:/loki

  alertmanager:
    image: prom/alertmanager:latest
    ports:
      - "9093:9093"
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml

volumes:
  prometheus_data:
  grafana_data:
  loki_data:
```

### External Observability (from Host)

Since the VM has no inbound ports (except forwarded ones), observability from host includes:

1. **Port-forwarded Grafana** — Access via localhost:3000
2. **VirtualBox Console** — Direct VM console access
3. **Shared Folder** — Mount a host folder for log export
4. **GitHub as Observability Channel** — Agents can post status to GitHub

### Shared Folder for Logs

```ruby
# Vagrantfile addition
config.vm.synced_folder "./logs", "/var/log/aifishtank-export",
  type: "virtualbox",
  mount_options: ["dmode=755", "fmode=644"]
```

Cron job inside VM:

```bash
# /etc/cron.d/export-logs
*/5 * * * * root rsync -a /var/log/aifishtank/ /var/log/aifishtank-export/
```

### GitHub-Based Status Reporting

Agents can post status updates to a dedicated GitHub Issue or Discussion:

```bash
# Heartbeat script
gh issue comment 1 --body "$(cat << EOF
## Agent Status Report - $(date -Iseconds)

| Agent | Status | Last Activity |
|-------|--------|---------------|
| solution-architect | running | 2 min ago |
| database | idle | 15 min ago |
...

### System Metrics
- CPU: 45%
- RAM: 8.2/12 GB
- Disk: 34/80 GB
- Docker containers: 12/12 Running

### Repo Stacks
| Repo | Frontend | API | Postgres |
|------|----------|-----|----------|
| my-saas-app | Up :3001 | Up :4001 | Up :5433 |
| internal-api | Up :3002 | Up :4002 | Up :5434 |
EOF
)"
```

### Alert Routing

```
Alert triggered in VM
    └── Alertmanager fires webhook
        └── Local script catches webhook
            └── gh issue create --title "ALERT: ..."
                └── Visible on GitHub
```

---

## 8. Backup & Recovery

### Snapshot Strategy

| Snapshot Type | Frequency | Retention | Trigger |
|--------------|-----------|-----------|---------|
| Base | Once | Forever | After initial provision |
| Daily | Daily 02:00 | 7 days | Cron on host |
| Pre-deploy | Before major changes | 3 | Agent action |
| Manual | On demand | Unlimited | User action |

### VirtualBox Snapshot Commands

```bash
# Create snapshot
VBoxManage snapshot "ai-fishtank" take "daily-$(date +%Y%m%d)" \
    --description "Automated daily snapshot"

# List snapshots
VBoxManage snapshot "ai-fishtank" list

# Restore snapshot
VBoxManage snapshot "ai-fishtank" restore "snapshot-name"

# Delete old snapshots (keep last 7)
VBoxManage snapshot "ai-fishtank" delete "old-snapshot-name"
```

### Host-Side Backup Script

```bash
#!/bin/bash
# scripts/backup.sh — Run on host

SNAPSHOT_NAME="daily-$(date +%Y%m%d-%H%M%S)"
VM_NAME="ai-fishtank"

# Pause VM for consistent snapshot
VBoxManage controlvm "$VM_NAME" savestate

# Take snapshot
VBoxManage snapshot "$VM_NAME" take "$SNAPSHOT_NAME"

# Resume VM
VBoxManage startvm "$VM_NAME" --type headless

# Cleanup old snapshots (keep 7)
VBoxManage snapshot "$VM_NAME" list --machinereadable | \
    grep SnapshotName | head -n -7 | \
    while read line; do
        name=$(echo $line | cut -d'"' -f2)
        VBoxManage snapshot "$VM_NAME" delete "$name"
    done
```

### Persistent Data Strategy

| Data | Location | Backup Method |
|------|----------|---------------|
| Git repos | /home/agent/repos/ | GitHub (source of truth) |
| PostgreSQL (per-repo) | Docker volumes | pg_dump to Git repo |
| Container images | Docker cache | Pulled from registries (pre-provisioned) |
| Secrets | /home/agent/.* | Manual backup / Vault |
| Logs | /var/log/aifishtank | Export to shared folder |

### PostgreSQL Backup (Docker Compose)

```bash
#!/bin/bash
# Runs inside VM, commits backup to Git

BACKUP_DIR="/home/agent/ai-fishtank/backups/postgres"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
REPO_NAME="$1"  # e.g., "my-saas-app"

mkdir -p "$BACKUP_DIR"

# Dump from Docker Compose PostgreSQL
docker compose -f /home/agent/repos/$REPO_NAME/docker-compose.yaml \
    exec -T postgres pg_dump -U dev dev | gzip > "$BACKUP_DIR/$REPO_NAME-$TIMESTAMP.sql.gz"

# Keep only last 5 backups per repo in Git
ls -t "$BACKUP_DIR/$REPO_NAME-"*.sql.gz | tail -n +6 | xargs rm -f

# Commit and push
cd /home/agent/ai-fishtank
git add backups/
git commit -m "chore: automated postgres backup $REPO_NAME $TIMESTAMP"
git push origin main
```

---

## Subtasks

- [ ] Create Vagrantfile with VM configuration — assigned to: dev-infra
- [ ] Create cloud-init.yaml for base provisioning — assigned to: dev-infra
- [ ] Create Ansible playbook structure — assigned to: dev-infra
- [ ] Implement DNS logging via dnsmasq — assigned to: dev-infra
- [ ] Implement connection tracking (conntrack/iptables logging) — assigned to: dev-infra
- [ ] Add network tracking page to Web UI — assigned to: frontend
- [ ] Create daily network usage report script — assigned to: scripting
- [ ] Create agent supervisor service — assigned to: scripting
- [ ] Create Docker Compose template for target repos — assigned to: dev-infra
- [ ] Implement per-repo port allocation and management — assigned to: scripting
- [ ] Create monitoring Docker Compose stack — assigned to: dev-infra
- [ ] Implement VirtualBox NAT port forwarding automation — assigned to: scripting
- [ ] Create Docker Compose lifecycle management in supervisor — assigned to: scripting
- [ ] Create backup scripts for host — assigned to: scripting
- [ ] Document GitHub integration setup — assigned to: docs
- [ ] Security audit of the architecture — assigned to: security

## Acceptance Criteria

- VM can be created from scratch with `vagrant up && ansible-playbook`
- All outbound DNS queries and connections are logged and reportable
- Network tracking data is viewable in Web UI
- Docker Compose stacks start for registered repos with source mounts and hot reload
- Frontend UIs are accessible from host browser via port forwarding
- Adding a new repo auto-provisions a Docker Compose stack with allocated ports
- Source code changes are reflected immediately via hot reload (no rebuild needed)
- Agent supervisor service starts on boot and polls GitHub
- Monitoring stack (Prometheus/Grafana/Loki) is operational in Docker Compose
- Snapshots can be created and restored
- Logs are accessible from host via shared folder
- All secrets are stored securely (not in Git, encrypted at rest)

## Notes

### Resolved Questions
1. ~~Should we use a local container registry inside the VM, or always build from source?~~ **RESOLVED**: No container registry needed. No docker build. Use base images with source mounts.
2. ~~Do we need GPU passthrough for any AI workloads?~~ **RESOLVED**: Not for now, may be added later.
3. ~~Should the VM support multiple repositories or just one?~~ **RESOLVED**: Multiple repos, each with its own Docker Compose stack. Designed in TASK-002 repo topology.

### Resolved Questions
4. ~~Should the firewall allow outbound to npm registry (registry.npmjs.org) and Docker Hub (registry-1.docker.io) for package install and image pulls, or should everything be pre-provisioned?~~ **RESOLVED**: No firewall restrictions. VM has full internet access with network tracking. npm, Docker Hub, and all other services are accessible. Domain usage is logged for future analysis.

### Design Decisions
- **Docker Compose only, no Kubernetes** — simplicity, dev-style hot reload, no orchestrator overhead
- **Source mounts with hot reload, no docker build** — fastest feedback loop for agent development
- **Per-repo port allocation** — enables multi-repo with independent UI access from host browser
- **GPU deferred** — not needed for current agent workloads, can be added later
- **Open internet access with tracking** — no firewall restrictions initially. DNS and connection logging provide visibility into what the VM accesses. This data can be used later to build an informed whitelist if needed.

### Risks
- Polling every 60 seconds may miss rapid events — acceptable tradeoff for simplicity
- VM disk can fill up with container images — need cleanup cron job
- Anthropic API key stored in VM is sensitive — consider short-lived tokens

### Dependencies
- VirtualBox 7.x installed on host
- Vagrant installed on host
- GitHub repository exists with deploy key configured
- Anthropic API key available

### Future Enhancements
- Multiple VM support for scaling
- Terraform provider for VirtualBox (alternative to Vagrant)
- HashiCorp Vault for secrets management
- Prometheus federation to external monitoring
- GPU passthrough for AI workloads

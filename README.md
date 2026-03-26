# Aquarco

Sandboxed VirtualBox VM for autonomous AI agents. Agents watch GitHub
repositories for issues and PRs, run multi-stage pipelines (analyze, design,
implement, test, review), and submit pull requests — all inside an isolated VM.

## Quick Start

```bash
cd vagrant
vagrant up          # Provision Ubuntu 24.04 VM (~5 min)
```

Open http://localhost:8080, log in to GitHub and Claude, then add a repository.

## Architecture

```
┌─ Host (macOS) ────────────────────────────────────────────────────┐
│  vagrant/Vagrantfile          Port forwards: 8080,4000,15432,...  │
│                                                                   │
│  ┌─ VirtualBox VM (Ubuntu 24.04) ──────────────────────────────┐  │
│  │                                                             │  │
│  │  ┌─ Docker Compose ──────────────────────────────────────┐  │  │
│  │  │  postgres:16  (5432)    ← data storage                 │  │  │
│  │  │  migrations   (oneshot) ← yoyo apply on each start     │  │  │
│  │  │  api:node:20  (4000)    ← GraphQL, auth handlers      │  │  │
│  │  │  web:node:20  (8080)    ← Next.js dashboard           │  │  │
│  │  └───────────────────────────────────────────────────────┘  │  │
│  │                                                             │  │
│  │  ┌─ Systemd Services (agent:agent) ──────────────────────┐  │  │
│  │  │  aquarco-supervisor-python  ← Python supervisor    │  │  │
│  │  │  aquarco-claude-auth        ← Claude IPC helper    │  │  │
│  │  │  aquarco-stack              ← docker compose up/dn │  │  │
│  │  └───────────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────┘
```

## Components

### Docker Compose Stack (`docker/compose.yml`)

| Service    | Image              | Port            | User     | Purpose                          |
|------------|--------------------|-----------------|----------|----------------------------------|
| postgres   | postgres:16-alpine | 5432 (internal) | postgres | Database                         |
| migrations | python:3.12-alpine | —               | root     | yoyo-migrations (runs on each up)|
| api        | node:20-alpine     | 4000            | node     | GraphQL API, auth endpoints      |
| web        | node:20-alpine     | 8080 (→3000)    | node     | Next.js dashboard                |

Source code is bind-mounted for hot reload; `node_modules` use named volumes.

### Systemd Services

| Service                      | User:Group  | Entry Point                                       |
|------------------------------|-------------|---------------------------------------------------|
| aquarco-stack             | agent:agent | `docker compose up -d` (oneshot)                  |
| aquarco-supervisor-python | agent:agent | `/home/agent/.venv/bin/aquarco-supervisor run` |
| aquarco-claude-auth       | agent:agent | `supervisor/scripts/claude-auth-helper.sh`        |

All three are enabled on boot. The supervisor waits for the Docker stack
(`After=aquarco-stack.service`).

### Python Supervisor (`supervisor/python/`)

Async Python process running the main loop every ~5 seconds:

1. **Refresh secrets** — re-read token files (picks up logins without restart)
2. **Clone pending repos** — `git clone` with GitHub token auth
3. **Pull ready repos** — `git fetch/pull` every 30s
4. **Run pollers** — watch GitHub issues (60s), PRs (30s), file triggers (10s)
5. **Dispatch tasks** — assign to agents (max 3 concurrent)
6. **Health report** — post to GitHub issue every 30 min

## Port Forwarding (VM → Host)

| Guest | Host  | Service    |
|-------|-------|------------|
| 8080  | 8080  | Web UI     |
| 4000  | 4000  | GraphQL API|
| 5432  | 15432 | PostgreSQL |
| 9090  | 9090  | Prometheus |
| 3000  | 13000 | Grafana    |
| 8081  | 8081  | Adminer    |

## File Layout

### VM Host Filesystem

```
/home/agent/
  .ssh/
    github-token              ← GitHub OAuth token (written by API container)
    deploy-keys/{repo}/       ← per-repo SSH deploy keys (generated on clone failure)
  .claude/
    .credentials.json         ← Claude auth token (written by auth helper)
  .venv/                      ← Python virtualenv with supervisor CLI
  aquarco/                ← synced from host git repo (VirtualBox shared folder)
  repos/{repo-name}/          ← cloned target repositories

/var/lib/aquarco/
  claude-ipc/                 ← file-based IPC between API container and auth helper
  triggers/                   ← external trigger drop directory

/var/log/aquarco/
  supervisor.log              ← JSON structured logs
  agents/*.log                ← per-agent execution logs
```

### API Container Mounts

| Host Path                      | Container Path | Purpose                 |
|--------------------------------|----------------|-------------------------|
| /home/agent/.ssh               | /agent-ssh     | GitHub token read/write |
| /home/agent/repos              | /repos         | Cloned repos access     |
| /var/lib/aquarco/claude-ipc | /claude-ipc    | Claude auth IPC         |
| api/src                        | /app/src       | Hot-reloaded source     |

## Authentication Flows

### GitHub Login (Device Flow OAuth)

User clicks **GitHub Login** on the Repositories page.

```
User          Web UI           API Container          GitHub            VM Host
 │              │                    │                    │                │
 │ click login ─→                    │                    │                │
 │              ├─ githubLoginStart ─→                    │                │
 │              │                    ├─ POST /login/device/code ──────────→│
 │              │                    │← device_code, user_code ────────────┤
 │              │← user_code, URL ───┤                    │                │
 │              │                    │                    │                │
 │ copy+open ───→ (copies code, opens GitHub) ───────────→│                │
 │ paste code, approve ──────────────────────────────────→│                │
 │              │                    │                    │                │
 │              ├─ githubLoginPoll ──→ (respects rate limit)               │
 │              │                    │←── access_token ───┤                │
 │              │                    ├── write /agent-ssh/github-token ───→│
 │              │                    │         (mode 0600, agent:agent)    │
 │              │← success, username ┤                    │                │
 │              │                    │                    │                │
 │              │                    │                    │  supervisor:   │
 │              │                    │                    │  _refresh_secrets()
 │              │                    │                    │  detects token │
 │              │                    │                    │  sets GH_TOKEN,│
 │              │                    │                    │  GIT_ASKPASS   │
```

**Token path**: API writes `/agent-ssh/github-token` (container) →
`/home/agent/.ssh/github-token` (host) → supervisor reads on next loop (~5s).

**Git HTTPS auth**: The supervisor creates a `GIT_ASKPASS` helper script that
returns `x-access-token` as username and the OAuth token as password. All
`git` and `gh` subprocesses inherit these env vars.

### Agent Management

The Agents page provides two tabs for managing agent definitions:

- **Global Agents** — lists all default agents and agents from global config repositories. Each agent can be individually disabled/enabled, and non-default agents can be edited. Modified agents are stored in the `agent_overrides` table. A "Create PR" button commits changes back to the config repository.
- **Repository Agents** — lists repositories with custom agents in an accordion layout. Agents can be disabled, edited, and reset. PR creation targets the specific repository.

Agent sources are distinguished by an `AgentSource` enum: `DEFAULT` (built-in), `GLOBAL_CONFIG` (from a global config repo), and `REPOSITORY` (repo-specific).

### Claude Login (PKCE OAuth via IPC)

User clicks **Claude Login** on the Agents page.

```
User          Web UI           API Container            IPC Files         Auth Helper
 │              │                    │                       │                 │
 │ click login ─→                    │                       │                 │
 │              ├─ claudeLoginStart ─→                       │                 │
 │              │                    ├─ write login-request ─→                 │
 │              │                    │                       │←── detect ──────┤
 │              │                    │                       │  launch OAuth ──┤
 │              │                    │←─── login-response ───┤                 │
 │              │←─ authorize URL ───┤                       │                 │
 │              │                    │                       │                 │
 │ visit URL, approve ───────────────────────────────────────→ Anthropic OAuth │
 │              │                    │                       │                 │
 │ paste code ──→                    │                       │                 │
 │              ├─ claudeSubmitCode ─→                       │                 │
 │              │                    ├─ write code-submit ───→                 │
 │              │                    │                       │←──── submit ────┤
 │              │                    │                       │  write creds ───┤
 │              │                    │                       │──→ ~/.claude/   │
 │              │                    │←─── code-complete ────┤    .credentials │
 │              │←─ success, email ──┤                       │                 │
```

**IPC mechanism**: The API container cannot run the `claude` CLI directly
(it runs inside Docker). It writes request files to `/claude-ipc/` (mounted
from `/var/lib/aquarco/claude-ipc/`). The `claude-auth-helper.sh` systemd
service watches that directory and handles requests using Python PKCE OAuth.

### Repository Clone

User clicks **Add Repository** on the Repositories page.

```
User          Web UI           API Container        PostgreSQL        Supervisor
 │              │                    │                   │                 │
 │ add repo ────→                    │                   │                 │
 │              ├─ registerRepo ────→│                   │                 │
 │              │                    ├── INSERT repos ──→│                 │
 │              │                    │  (status=pending) │                 │
 │              │← success ──────────┤                   │                 │
 │              │                    │                   │                 │
 │              │                    │                   │←── next loop ───┤
 │              │                    │                   │  SELECT pending │
 │              │                    │                   │  UPDATE cloning │
 │              │                    │                   │  git clone      │
 │              │                    │                   │  (HTTPS+token)  │
 │              │                    │                   │  UPDATE ready   │
 │              │                    │                   │  (+ head_sha)   │
 │              │                    │                   │                 │
 │              │ poll (3s) ────────→│←── status=READY ──┤                 │
 │              │←─ READY ───────────┤                   │                 │
```

When logged into GitHub, the Add Repository dialog shows an autocomplete
with the user's repositories. Selecting one auto-fills name, URL, and default
branch. Free-text entry is always available.

If HTTPS clone fails, the supervisor generates an SSH deploy key and stores
the public key in the database for display in the UI.

## Process Ownership

| What              | User:Group  | Where           | Reads                       | Writes                         |
|-------------------|-------------|-----------------|-----------------------------|--------------------------------|
| PostgreSQL        | postgres    | Docker          | —                           | pgdata volume                  |
| GraphQL API       | node        | Docker          | /agent-ssh/github-token     | /agent-ssh/github-token        |
|                   |             |                 | /claude-ipc/* responses     | /claude-ipc/* requests         |
| Web UI            | node        | Docker          | —                           | —                              |
| Python Supervisor | agent:agent | VM host systemd | ~/.ssh/github-token         | /var/log/aquarco/           |
|                   |             |                 | ~/.anthropic-key            | ~/repos/                       |
|                   |             |                 | supervisor.yaml             | /tmp/git-askpass-helper.sh     |
| Claude Auth       | agent:agent | VM host systemd | /claude-ipc/* requests      | ~/.claude/.credentials.json    |
|                   |             |                 | ~/.claude/.credentials.json | /claude-ipc/* responses        |
| Clone Worker      | agent:agent | in supervisor   | GITHUB_TOKEN env            | ~/repos/{name}/                |
|                   |             |                 |                             | ~/.ssh/deploy-keys/            |
| Pull Worker       | agent:agent | in supervisor   | GITHUB_TOKEN env            | ~/repos/{name}/ (git pull)     |
| Pollers           | agent:agent | in supervisor   | GH_TOKEN env                | DB (tasks table)               |

## Configuration

### Supervisor (`supervisor/config/supervisor.yaml`)

```yaml
spec:
  database:
    url: postgresql://aquarco:aquarco@localhost:5432/aquarco
  globalLimits:
    maxConcurrentAgents: 3
    maxTokensPerHour: 1000000
    cooldownBetweenTasksSeconds: 5
  secrets:
    githubTokenFile: /home/agent/.ssh/github-token
    anthropicKeyFile: /home/agent/.anthropic-key
  pipelinesFile: /home/agent/aquarco/config/pipelines.yaml
  agentsDir: /home/agent/aquarco/config/agents/definitions
  promptsDir: /home/agent/aquarco/config/agents/prompts
```

Secrets are re-read from disk every loop iteration (~5s). When a token file
appears after the user logs in via web UI, the supervisor detects it
automatically and logs `github_token_detected`.

SIGHUP triggers a full config reload: `systemctl reload aquarco-supervisor-python`

### Agent Definitions (`config/agents/definitions/`)

Kubernetes-style YAML files defining each agent's tools, environment variables,
system prompt path, and output schema. Key fields under `spec`:

- `tools.allowed` / `tools.denied` — tool access control for Claude CLI
- `environment` — env vars passed to Claude CLI subprocess
- `systemPrompt` — path to agent's markdown prompt template
- `outputSchema` — structured output contract

### Pipelines (`config/pipelines.yaml`)

Standalone file with pipeline definitions. Each pipeline has ordered stages
specifying which agent runs, what tools it uses, and what context it
produces/consumes.

Stages support **conditional loops** — a `loop` block causes a stage (or group
of stages) to repeat until an exit condition is met or `max_repeats` is reached:

```yaml
- category: review
  loop:
    condition: "recommendation == approve"   # exit when true
    max_repeats: 3                           # safety cap (1–10)
    eval_mode: simple                        # "simple" or "ai"
    loopStages: [implementation, review]     # stages to repeat
```

- **`eval_mode: simple`** — field comparison against the previous stage's structured output (e.g. `recommendation == approve`).
- **`eval_mode: ai`** — natural-language predicate evaluated by Claude CLI, allowing rich conditions like *"All risks mentioned in STAGE_0.risks are avoided or mitigated"*.

The web UI task detail page visualizes pipeline definitions including loop
back-edges and conditional paths.

### Repositories

Repositories are managed **only via the database** (added through the web UI).
Pollers query the DB for repos with `clone_status = 'ready'`.

## Claude Code Agent System

The project uses a multi-agent system coordinated by the Solution Architect.

```
solution-architect  ←── coordinates all agents
     │
     ├── ralph           ── writes decisions to prd.json (on request only)
     ├── docs            ── keeps CLAUDE.md, README.md, CHANGELOG.md current
     ├── e2e             ── Playwright tests for critical flows
     ├── database        ── PostgreSQL schema, migrations, queries
     ├── qa              ── code quality, standards
     ├── testing         ── unit / integration / e2e tests
     ├── security        ── OWASP, auth, secrets, audits
     ├── scripting       ── shell scripts, CI/CD
     ├── dev-infra       ── Docker Compose, dev containers
     ├── graphql         ── GraphQL API development
     └── frontend        ── React + MUI + Next.js
```

Agent definitions live in `config/agents/definitions/` (K8s-style YAML) and
`.claude/agents/` (Claude Code agent system prompts). Slash commands:

| Command                       | Description                                 |
|-------------------------------|---------------------------------------------|
| `/architect-init`             | Initialize project, set up prd.json         |
| `/new-feature <description>`  | Implement feature with full agent pipeline  |
| `/review`                     | Multi-agent review: QA + Security + Testing |
| `/deploy-check`               | Pre-deployment validation checklist         |
| `/prd`                        | Show human-readable PRD summary             |

## Development

The codebase is synced into the VM via VirtualBox shared folder.
Source changes on the host are immediately visible inside the VM.

- **API/Web**: Hot-reloaded automatically (Docker bind mounts + polling watchers)
- **Supervisor**: Editable pip install (`pip install -e`); restart service to pick up changes
- **DB schema**: Migrations in `db/migrations/` are applied via [yoyo-migrations](https://ollama.com/library/yoyo) on every `docker compose up`. Use `db/migrate.sh` for manual operations:
  ```bash
  # Inside the VM
  docker compose -f /home/agent/aquarco/docker/compose.yml run --rm migrations list      # show status
  docker compose -f /home/agent/aquarco/docker/compose.yml run --rm migrations rollback   # undo last
  docker compose -f /home/agent/aquarco/docker/compose.yml run --rm migrations reapply    # rollback + apply
  ```

### Useful Commands

```bash
# VM access
cd vagrant && vagrant ssh

# Inside VM — supervisor
sudo journalctl -u aquarco-supervisor-python -f     # follow logs
sudo systemctl restart aquarco-supervisor-python     # restart
sudo systemctl reload aquarco-supervisor-python      # reload config (SIGHUP)

# Inside VM — Docker stack
sudo docker compose -f /home/agent/aquarco/docker/compose.yml logs -f api

# DB access from host
psql postgresql://aquarco:aquarco@localhost:15432/aquarco
```

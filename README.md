# Aquarco

Sandboxed VirtualBox VM for autonomous AI agents. Agents watch GitHub
repositories for issues and PRs, run multi-stage pipelines (analyze, design,
implement, test, review), and submit pull requests — all inside an isolated VM.

## Quick Start

```bash
pip install -e cli/        # Install the aquarco CLI
aquarco install            # Bootstrap the VM (~5 min)
aquarco auth github        # Authenticate GitHub
aquarco auth claude        # Authenticate Claude
aquarco watch add https://github.com/user/repo  # Watch a repo
```

Open http://localhost:8080 or run `aquarco ui --open` to launch the dashboard.

## CLI Reference

The `aquarco` CLI runs on the **host** (macOS) and manages the VM via Vagrant SSH and the GraphQL API.

Install: `pip install -e cli/` (requires Python 3.10+)

| Command | Description |
|---------|-------------|
| `aquarco install` | Bootstrap the Aquarco VM (checks VirtualBox + Vagrant, runs `vagrant up`, verifies health) |
| `aquarco update` | Update VM: pull source, Docker images, run migrations, restart services |
| `aquarco auth claude` | Authenticate Claude via OAuth PKCE flow |
| `aquarco auth github` | Authenticate GitHub via device flow |
| `aquarco auth status` | Check Claude and GitHub auth status |
| `aquarco watch add <url>` | Register a repository for autonomous watching |
| `aquarco watch list` | List all watched repositories |
| `aquarco watch remove <name>` | Remove a watched repository |
| `aquarco run <title> -r <repo>` | Create a task for agent execution |
| `aquarco status` | Dashboard overview (task counts, agents, cost) |
| `aquarco status <id>` | Detailed task status with stage history |
| `aquarco ui` | Start web UI services |
| `aquarco ui stop` | Stop web UI services |

Common flags: `--follow` / `-f` (stream task progress), `--json` (machine-readable output), `--dry-run` (preview update steps).

## Architecture

```
┌─ Host (macOS) ────────────────────────────────────────────────────┐
│  vagrant/Vagrantfile          Port forwards: 8080, 15432         │
│                                                                   │
│  ┌─ VirtualBox VM (Ubuntu 24.04) ──────────────────────────────┐  │
│  │                                                             │  │
│  │  ┌─ Docker Compose ──────────────────────────────────────┐  │  │
│  │  │  caddy:2       (8080)    ← reverse proxy (single port)│  │  │
│  │  │  postgres:16   (5432)    ← data storage                │  │  │
│  │  │  migrations    (oneshot) ← yoyo apply on each start    │  │  │
│  │  │  api:node:20   (4000)    ← GraphQL, auth handlers     │  │  │
│  │  │  web:node:20   (3000)    ← Next.js dashboard          │  │  │
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
| caddy      | caddy:2-alpine     | 8080 (external) | root     | Reverse proxy (single entry point)|
| postgres   | postgres:16-alpine | 5432 (internal) | postgres | Database                         |
| migrations | python:3.12-alpine | —               | root     | yoyo-migrations (runs on each up)|
| api        | node:20-alpine     | 4000 (internal) | node     | GraphQL API, auth endpoints      |
| web        | node:20-alpine     | 3000 (internal) | node     | Next.js dashboard                |

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

All services are accessed through the Caddy reverse proxy on a single port:

| Guest | Host  | Service              |
|-------|-------|----------------------|
| 8080  | 8080  | Caddy reverse proxy  |
| 5432  | 15432 | PostgreSQL (direct)  |

### URL Routing (via Caddy on port 8080)

| URL Path         | Backend Service  | Notes                    |
|------------------|------------------|--------------------------|
| `/`              | web:3000         | Next.js dashboard        |
| `/api/*`         | api:4000         | GraphQL API (path stripped)|
| `/adminer/*`     | adminer:8080     | DB admin (path stripped) |
| `/grafana/*`     | grafana:3000     | Grafana (subpath-aware)  |
| `/prometheus/*`  | prometheus:9090  | Prometheus (subpath-aware)|

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

- **Global Agents** — lists all agents in two sections:
  - *Pipeline Agents* — agents that execute pipeline stages (analyze, design, implement, test, review, docs). Full resource/capability details shown.
  - *System Infrastructure* — agents that orchestrate pipeline execution (planner, condition evaluator, repo descriptor). Displayed with visual de-emphasis.
  Each agent can be individually disabled/enabled, and non-default agents can be edited. Modified agents are stored in the `agent_overrides` table. A "Create PR" button commits changes back to the config repository.
- **Repository Agents** — lists repositories with custom agents in an accordion layout. Agents can be disabled, edited, and reset. PR creation targets the specific repository.

Agent sources are distinguished by an `AgentSource` enum: `DEFAULT` (built-in), `GLOBAL_CONFIG` (from a global config repo), `REPOSITORY` (repo-specific), and `AUTOLOADED` (discovered from `.claude/agents/`).

Agent groups are distinguished by an `AgentGroup` enum: `SYSTEM` (orchestration agents) and `PIPELINE` (stage execution agents). Autoloaded agents are always `PIPELINE`.

### Agent Autoloading

Repositories containing a `.claude/agents/` directory with `.md` prompt files are automatically detected. The autoloader:

1. **Scans** the directory for valid `.md` files (max 20 per repo, 50KB size limit)
2. **Analyzes** each prompt via Claude CLI to infer agent metadata (categories, tools, description)
3. **Generates** aquarco agent YAML definitions and writes them to `aquarco-config/agents/` in the repo
4. **Stores** agents in the database with `source='autoload:<repo_name>'`

Autoloaded agents are merged as a 4th config layer: `default → global_overlay → repo_overlay → autoloaded`. They inherit conservative default tools (`Read`, `Grep`, `Glob`) which can be overridden via the `modifyAgent` mutation.

A **Reload Agents** button on the Repositories page allows on-demand rescanning. Scans are rate-limited to once per 5 minutes per repository.

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
and system prompt path. Agents are split into two subdirectories:

| Directory | Schema | Contains |
|-----------|--------|----------|
| `definitions/system/` | `system-agent-v1.json` | `planner-agent`, `condition-evaluator-agent`, `repo-descriptor-agent` |
| `definitions/pipeline/` | `pipeline-agent-v1.json` | `analyze-agent`, `design-agent`, `implementation-agent`, `review-agent`, `test-agent`, `docs-agent` |

**System agents** orchestrate pipeline execution and are invoked directly by the executor. They use `spec.role` (e.g., `planner`, `condition-evaluator`) instead of `spec.categories`, and have lower resource defaults (max 20 turns, 0.5 USD default cost cap). System agents are never eligible for category-based stage selection.

**Pipeline agents** execute pipeline stages and are selected by category. They use `spec.categories` and `spec.priority` for stage-to-agent mapping.

Key fields under `spec`:

- `model` — Claude model to use for this agent (e.g., `claude-sonnet-4-6`, `claude-haiku-4-5`, `sonnet`, `opus`). When omitted, the CLI uses its default model. Can be overridden per-repo via the config overlay system.
- `tools.allowed` / `tools.denied` — tool access control for Claude CLI
- `environment` — env vars passed to Claude CLI subprocess
- `promptFile` — filename (relative to `prompts/`) of the agent's markdown prompt template
- `role` *(system agents only)* — well-known values: `planner`, `condition-evaluator`, `repo-descriptor`
- `categories` *(pipeline agents only)* — pipeline stage categories this agent handles

> **Notes:**
> - Output schemas are defined in `pipelines.yaml` under `categories:` rather than in agent definitions. Agent-level `outputSchema` is still supported as a fallback for autoloaded agents.
> - Autoloaded agents (from repository `.claude/agents/`) always validate against the pipeline schema and are tagged as pipeline agents.
> - A flat `definitions/*.yaml` layout is still supported for backward compatibility; all flat-scanned agents are treated as pipeline agents.

### Pipelines (`config/pipelines.yaml`)

Standalone file with pipeline definitions. The file contains two top-level sections:

- **`categories:`** — defines output schemas per stage category (e.g., `analyze`, `design`, `implementation`). Each category has a `name` and `outputSchema` (JSON Schema). Output schemas were moved here from agent definitions to decouple schema contracts from individual agents.
- **`pipelines:`** — ordered stages with named-stage execution. Each stage has a `name`, `category`, and optional structured `conditions` that act as exit gates.

#### Stage Conditions

Conditions are evaluated after each stage completes. Each condition object has one of:
- `simple:` — expression evaluated against stage outputs (supports `==`, `!=`, `>=`, `>`, `<=`, `<`, `&&`, `||`, parentheses, and dotted field paths like `analysis.risks`)
- `ai:` — natural-language prompt evaluated by Claude CLI against accumulated pipeline context

Each condition can specify:
- `yes:` — stage name to jump to if the condition is true
- `no:` — stage name to jump to if the condition is false
- `maxRepeats:` — maximum times a jump target can be visited before falling through

If no condition matches, execution advances to the next stage in order (preserving linear flow as the default).

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

### Host-side CLI (`cli/`)

| Module | Purpose |
|--------|---------|
| `main.py` | Typer app, command registration, `--version` |
| `commands/install.py` | Prerequisite checks, `vagrant up`, health verification |
| `commands/update.py` | Git pull, Docker pull, migrations, service restart, `--dry-run` |
| `commands/auth.py` | Claude OAuth + GitHub device flow via GraphQL API |
| `commands/watch.py` | Repository add/list/remove via GraphQL mutations |
| `commands/run.py` | Task creation with optional `--follow` progress polling |
| `commands/status.py` | Dashboard + task detail views, `--json` output |
| `commands/ui.py` | Start/stop web UI Docker services |
| `vagrant.py` | `VagrantHelper` — SSH, provision, status via `vagrant` CLI |
| `graphql_client.py` | httpx-based GraphQL client targeting `localhost:8080/api/graphql` |
| `config.py` | `AquarcoConfig` — discovers Vagrantfile, resolves paths |
| `console.py` | Rich console helpers (tables, error/info/success output) |
| `health.py` | HTTP health checks for web, API, and PostgreSQL |

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

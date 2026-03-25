# Project Intelligence & Agent System

## Architecture Decision Record
All architectural decisions are logged to `prd.json` via the Ralph agent.
Read `prd.json` before starting any task to understand current project state.

## Agent System Overview
This project uses a multi-agent system coordinated by the Solution Architect.
Each agent has a dedicated `.claude/agents/` file defining its system prompt and tools.

### Active Agents
| Agent | File | Role |
|-------|------|------|
| solution-architect | agents/solution-architect.md | Coordinates all agents, owns task files |
| ralph | agents/ralph.md | Writes architectural decisions to prd.json (on request only) |
| docs | agents/docs.md | Keeps CLAUDE.md, README.md, CHANGELOG.md up to date |
| e2e | agents/e2e.md | Playwright e2e tests for mission-critical flows |
| database | agents/database.md | PostgreSQL schema, migrations, queries |
| qa | agents/qa.md | Code quality, reviews, standards |
| testing | agents/testing.md | Unit/integration/e2e tests |
| security | agents/security.md | Auth, OWASP, secrets, audits |
| scripting | agents/scripting.md | Automation scripts, CI/CD tasks |
| dev-infra | agents/dev-infra.md | Docker Compose, dev containers |
| graphql | agents/graphql.md | GraphQL API development |
| frontend | agents/frontend.md | React + MUI + Next.js |

## Automatic Triggers (Hooks)
After every file change the orchestrator hook fires and delegates to the relevant agent.
See `.claude/settings.json` for hook configuration.

## PRD
Always check `prd.json` for current requirements, architecture decisions, and status.

## Tech Stack
- **Backend API**: GraphQL (Node.js / .NET)
- **Frontend**: Next.js, React, MUI
- **Database**: PostgreSQL
- **Dev Infra**: Docker Compose with source code mounts (no docker build, hot reload)
- **Runtime**: Docker Compose only (no Kubernetes/k3s)
- **CI/CD**: Scripts managed by scripting agent

## Host ↔ VM File Sync & Hot Reload

Source code lives on the macOS host and is synced into the VirtualBox VM via a
shared folder (`vboxsf`). The mount is configured in `vagrant/Vagrantfile`:

```
config.vm.synced_folder "..", "/home/agent/aquarco", type: "virtualbox"
```

**Important:** `vboxsf` does **not** propagate filesystem events (inotify/fswatch)
from host to guest. All file watchers inside the VM must use **polling** to detect
changes.

### Hot Reload Status per Component

| Component | Container/Service | Hot Reload | Mechanism | Polling Env Var |
|-----------|-------------------|------------|-----------|-----------------|
| **Web (Next.js)** | `web` (Docker) | ✅ Works | `next dev` + watchpack | `WATCHPACK_POLLING=true` |
| **API (GraphQL)** | `api` (Docker) | ✅ Works | `tsx watch` + chokidar | `CHOKIDAR_USEPOLLING=true` |
| **PostgreSQL** | `postgres` (Docker) | N/A | yoyo-migrations applied on each `docker compose up` | — |
| **Supervisor** | systemd service | ❌ Manual | Config reload via `SIGHUP`; **code changes require restart** | — |
| **Monitoring** | Prometheus/Grafana/Loki | ❌ Manual | Container restart required | — |

- **Web & API** — edit files on the host; containers detect changes via polling
  (default interval ~1-5 s) and auto-rebuild. No restart needed.
- **Supervisor** — after editing `supervisor/python/src/`, restart manually:
  ```bash
  vagrant ssh d2a20a4 -- -t "sudo systemctl restart aquarco-supervisor-python"
  ```
- **Monitoring** — after config changes, restart the stack:
  ```bash
  vagrant ssh d2a20a4 -- -t "cd /home/agent/aquarco/docker && sudo docker compose -f compose.yml -f compose.monitoring.yml restart prometheus grafana loki"
  ```

## Configuration Layout

```
config/
  agents/
    definitions/   ← Kubernetes-style YAML agent definitions (apiVersion, kind, metadata, spec)
    prompts/       ← Markdown prompt templates per agent
  pipelines.yaml   ← Pipeline definitions (stages, agents, tools)
  schemas/         ← JSON schemas for agent definitions
supervisor/config/
  supervisor.yaml  ← Main supervisor config (database, limits, secrets, pollers)
```

### Agent Definitions (`config/agents/definitions/*.yaml`)
Each agent is defined as a Kubernetes-style resource with `spec.tools.allowed`/`spec.tools.denied`
and `spec.environment` (env vars passed to Claude CLI). The `spec.outputSchema` and pipeline
`produces`/`consumes` fields define structured contracts between stages.

### Pipelines (`config/pipelines.yaml`)
Pipeline definitions are loaded from this standalone file (path configured via `pipelinesFile`
in `supervisor.yaml`). Each pipeline defines ordered stages with agent assignments.

### Repositories
Repositories are stored **only in the database** (not in config). Pollers query the DB
for repos with `clone_status = 'ready'` and matching poller name in the `pollers` array.

## Supervisor (Python)
The supervisor system manages autonomous AI agent pipelines. It was rewritten from
~2,500 lines of bash to a Python async package at `supervisor/python/`.

### Key Modules
| Module | Responsibility |
|--------|---------------|
| `main.py` | Entry point, main loop, signal handling, health reporting |
| `config.py` | YAML config loading, pipeline loading, Pydantic validation |
| `database.py` | Async PostgreSQL pool (psycopg) |
| `task_queue.py` | Task CRUD, status transitions, poll state |
| `pipeline/executor.py` | Multi-stage pipeline execution, git branching, PR creation |
| `pipeline/agent_registry.py` | Agent discovery, capacity management, env/tools resolution |
| `pipeline/context.py` | Context accumulation for stages |
| `cli/claude.py` | Claude CLI subprocess wrapper (with `extra_env` support) |
| `pollers/` | GitHub issues, PRs, commits, file-drop triggers (repos from DB) |
| `workers/` | Git clone and pull workers |

### Running
```bash
cd supervisor/python && pip install -e ".[dev]"
aquarco-supervisor --config supervisor/config/supervisor.yaml
# Or via systemd: aquarco-supervisor-python.service
```

### Testing
```bash
cd supervisor/python && python -m pytest tests/ -v
```

## Mission-Critical Flows (E2E)
The `e2e` agent owns Playwright tests for three non-negotiable areas:
- **User registration** — full signup flow, validation, duplicate handling
- **User portfolio management** — create, view, edit, delete portfolios; auth guards
- **Public pages smoke tests** — every public route renders without JS errors or failed requests

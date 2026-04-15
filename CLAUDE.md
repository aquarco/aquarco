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
- **Reverse Proxy**: Caddy (single-port entry on `:8080`, path-based routing to all services)
- **Dev Infra**: Docker Compose with source code mounts (no docker build, hot reload)
- **Runtime**: Docker Compose only (no Kubernetes/k3s)
- **CI/CD**: Scripts managed by scripting agent


## Configuration Layout

```
config/
  agents/
    definitions/
      system/    ← System agents (planner, condition-evaluator) — hybrid .md files with YAML frontmatter
      pipeline/  ← Pipeline stage agents (analyze, design, implement, test, review, docs) — hybrid .md files with YAML frontmatter
  pipelines.yaml   ← Pipeline definitions (stages, agents, tools)
  schemas/         ← JSON schemas for agent definition frontmatter
supervisor/config/
  supervisor.yaml  ← Main supervisor config (database, limits, secrets, pollers)
```

### Agent Definitions (`config/agents/definitions/`)
Each agent is a single hybrid `.md` file containing YAML frontmatter followed by the markdown system prompt.
Agents are organized into two subdirectories by role:

- **`system/`** — Orchestration agents invoked directly by the executor. Frontmatter includes `role` field (e.g., `planner`, `condition-evaluator`) instead of `categories`. Never selected for category-based stage dispatch. Validated against `config/schemas/system-agent-v1.json`.
- **`pipeline/`** — Stage execution agents selected by category. Frontmatter includes `categories` and `priority` fields. Validated against `config/schemas/pipeline-agent-v1.json`.

The `model` field is optional; when omitted the CLI uses its default. The config overlay system
supports per-repo model overrides. Output schemas are defined at the pipeline category level
(see Pipelines below), not in agent definitions.

### Pipelines (`config/pipelines.yaml`)
Pipeline definitions are loaded from this standalone file (path configured via `pipelinesFile`
in `supervisor.yaml`). Contains two top-level sections:
- `categories:` — maps category names to `outputSchema` (JSON Schema contracts between stages)
- `pipelines:` — named stages with structured exit-gate conditions (`simple:` expressions,
  `ai:` Claude-evaluated prompts) supporting `yes:`/`no:` stage jumps and `maxRepeats:` guards

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
| `pipeline/conditions.py` | Structured condition evaluation engine (simple expressions, AI conditions, stage jumps) |
| `pipeline/agent_registry.py` | Agent discovery, capacity management, env/tools resolution |
| `pipeline/context.py` | Context accumulation for stages |
| `cli/claude.py` | Claude CLI subprocess wrapper (with `extra_env` support) |
| `config_store.py` | Agent/pipeline CRUD in database |
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

## GitHub Wiki Structure

The wiki is stored at `aquarco/aquarco.wiki.git` (separate from the main repo).
Clone with: `git clone https://github.com/aquarco/aquarco.wiki.git wiki`

### Page Index

| Page file | URL slug | Topic |
|-----------|----------|-------|
| `Home.md` | `/wiki/Home` | Project intro, quick start, page index |
| `Quick-Start.md` | `/wiki/Quick-Start` | Step-by-step first-time setup |
| `CLI-Reference.md` | `/wiki/CLI-Reference` | All CLI commands and flags |
| `Architecture.md` | `/wiki/Architecture` | VM, Docker services, networking |
| `File-Layout.md` | `/wiki/File-Layout` | Repository directory structure |
| `Components.md` | `/wiki/Components` | Individual Docker services |
| `Agent-System.md` | `/wiki/Agent-System` | Agent definitions, discovery, selection |
| `Pipeline-System.md` | `/wiki/Pipeline-System` | Stages, lifecycle, context, spending |
| `Conditions-Engine.md` | `/wiki/Conditions-Engine` | Exit gate conditions syntax |
| `Git-Flow.md` | `/wiki/Git-Flow` | Branch modes, naming, back-merges |
| `Auth-Flows.md` | `/wiki/Auth-Flows` | Claude PKCE + GitHub device flows |
| `Database.md` | `/wiki/Database` | Schema, all 11 tables, migrations |
| `Dev-Setup.md` | `/wiki/Dev-Setup` | Contributing, dev mode, testing |
| `Operations.md` | `/wiki/Operations` | Backup, restore, update, monitoring |
| `_Sidebar.md` | (navigation) | Persistent wiki sidebar |
| `_Footer.md` | (footer) | Footer shown on every page |

### Conventions
- Page filenames use Title-Case with hyphens (no underscores, no spaces).
- Every page starts with an H1 matching its filename (e.g., `# CLI Reference`).
- Cross-links use wiki double-bracket syntax: `[[Page-Name]]` or
  `[[Page-Name|Link Text]]`.
- When adding a new page: add entry to this table, add link to `_Sidebar.md`,
  add entry to the `Home.md` page index.

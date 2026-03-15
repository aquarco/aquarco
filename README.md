# Multi-Agent Development System

A Claude Code agent system that coordinates specialized AI agents for every aspect of development.

## Architecture

```
solution-architect  ←─── coordinates everything
     │
     ├── ralph           ─── writes decisions to prd.json (on request only)
     ├── docs            ─── keeps CLAUDE.md, README.md, CHANGELOG.md current
     ├── e2e             ─── Playwright tests for registration, portfolio, public pages
     ├── database        ─── PostgreSQL schema, migrations, queries
     ├── qa              ─── code quality, standards
     ├── testing         ─── unit / integration / e2e tests
     ├── security        ─── OWASP, auth, secrets, audits
     ├── scripting       ─── shell scripts, Makefile, CI/CD
     ├── dev-infra       ─── Docker Compose, dev containers
     ├── graphql         ─── GraphQL API development
     └── frontend        ─── React + MUI + Next.js
```

## How It Works

### Automatic Trigger (on every change)
Every time Claude writes or edits a file, the `PostToolUse` hook fires:
1. Detects which file changed and categorizes it
2. Injects context into the conversation
3. The `solution-architect` reviews the change and delegates to specialists

### Manual Commands
| Command | Description |
|---------|-------------|
| `/architect-init` | Initialize a new project — sets up `prd.json` |
| `/new-feature <description>` | Implement a feature with full agent pipeline |
| `/review` | Multi-agent review: QA + Security + Testing |
| `/deploy-check` | Pre-deployment validation checklist |
| `/prd` | Show human-readable PRD summary |

### PRD (Product Requirements Document)
All architectural decisions are recorded in `prd.json` by the **Ralph agent**.
- Ralph writes **only when explicitly asked** — not automatically
- Invoke manually: *"Ralph — record this decision: [decision]"*

## Setup

```bash
# 1. Install dependencies
make setup

# 2. Copy env vars
cp .env.example .env

# 3. Start dev stack
make dev

# 4. Initialize project with Claude Code
claude
> /architect-init
```

## File Structure

```
.claude/
  agents/           # Subagent system prompts
    solution-architect.md
    ralph.md
    docs.md
    e2e.md
    database.md
    qa.md
    testing.md
    security.md
    scripting.md
    dev-infra.md
    graphql.md
    frontend.md
  commands/         # Slash commands
    architect-init.md
    new-feature.md
    review.md
    deploy-check.md
    prd.md
  hooks/            # Automatic triggers
    orchestrate-on-change.sh   # Fires on every Write/Edit
    session-start.sh           # Fires at session start
  skills/           # Shared domain knowledge
    architecture/SKILL.md
  logs/             # Hook execution logs (gitignored)
  settings.json     # Hook config + permissions

CLAUDE.md           # Project memory loaded every session
prd.json            # Living architecture document (managed by ralph)
Makefile            # All common tasks
```

## Decision Log

Architecture decisions are stored in `prd.json` as ADRs (Architecture Decision Records).
To view: `make prd-show` or `/prd` in Claude Code.

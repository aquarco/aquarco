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

## Mission-Critical Flows (E2E)
The `e2e` agent owns Playwright tests for three non-negotiable areas:
- **User registration** — full signup flow, validation, duplicate handling
- **User portfolio management** — create, view, edit, delete portfolios; auth guards
- **Public pages smoke tests** — every public route renders without JS errors or failed requests

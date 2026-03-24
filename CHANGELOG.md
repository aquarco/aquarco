# Changelog

## [2026-03-24] — Redesign agents page (github-issue-aquarco-1)

### Added
- **Agents page redesigned** with two distinct sections: **Global Agents** and **Repository Agents**
- Agents are now categorised by source: `DEFAULT` (built-in), `GLOBAL` (from config repositories), and `REPOSITORY` (repo-specific)
- Per-agent **disable/enable** toggle — works for both global and repository-scoped agents
- Per-agent **spec modification** — edits persist to the database via the `agent_overrides` table; a separate action can create a PR to push changes back to the config repository
- **Reset override** action to revert an agent to its original definition
- New GraphQL queries: `agentDefinitions(source)`, `repositoriesWithAgents`
- New GraphQL mutations: `setAgentDisabled`, `updateAgentSpec`, `resetAgentOverride`
- New GraphQL types: `AgentDefinition`, `AgentOverride`, `RepositoryAgents`, `AgentSource` enum
- New frontend components: `GlobalAgentsSection`, `RepositoryAgentsSection`, `AgentEditDialog`, `AgentCard`
- Database migration `019_agent_overrides.sql` — adds `source`/`source_repository` columns to `agent_definitions` and creates `agent_overrides` table

### Changed
- `web/src/app/agents/page.tsx` — complete rewrite from flat agent-instance table to two-section layout
- `api/src/schema.graphql` — extended with agent definition types, queries, and mutations
- `api/src/resolvers/queries.ts` and `api/src/resolvers/mutations.ts` — new resolvers for agent management

## [2026-03-20] — Rebrand: ai-fishtank → aquarco

### Breaking Changes

> **Upgrade path required for existing deployments.** Apply `018_rename_schema.sql`
> before restarting any service — the application now issues `SET search_path TO aquarco`
> and will fail immediately if the database schema is still named `aifishtank`.

- **PostgreSQL schema renamed** from `aifishtank` to `aquarco`. Run migration
  `db/migrations/018_rename_schema.sql` on every deployed database before upgrading.
  Fresh installs are unaffected (the migration is a no-op when `aifishtank` does not exist).
- **Python package renamed** from `aifishtank_supervisor` to `aquarco_supervisor`.
  Re-install the package: `pip install -e supervisor/python/` (or `pip install -e ".[dev]"`).
- **CLI binary renamed** from `aifishtank-supervisor` to `aquarco-supervisor`.
  Update any scripts, cron jobs, or process supervisors that invoke the old binary.
- **systemd service units renamed** — the Python supervisor service is now
  `aquarco-supervisor-python`. Update `systemctl` calls and any monitoring checks.
- **Config `apiVersion` changed** — all agent definition YAML files must use the
  new `apiVersion` value. The supervisor will fast-fail on stale configs at startup.

### Changed (non-breaking)

- All 140 source files updated: directory names, import paths, log prefixes,
  environment variable prefixes, Docker image tags, and inline comments.
- Sudoers entry in `provision.sh` corrected to reference `aquarco-supervisor-python`
  (the actual systemd service name), restoring passwordless restart capability for
  the agent user.
- Branch prefixes in test assertions updated to match executor implementation.

## [2026-03-06] — E2E agent for mission-critical flows

### Added
- `e2e` agent (`bright_magenta`) — owns Playwright end-to-end tests for three mission-critical areas:
  - **User registration**: full signup flow, validation errors, duplicate email handling
  - **User portfolio management**: create/view/edit/delete, auth guard redirect, empty state
  - **Public pages smoke tests**: all public routes render without JS console errors or failed requests
- `e2e/` directory structure convention: `fixtures/`, `pages/` (Page Objects), `tests/`
- Hook routing: `e2e` category triggered by changes to `e2e/`, `playwright.config`, `register`, `portfolio`, or `middleware.ts`
- Mission-critical section added to CLAUDE.md

### Changed
- `solution-architect` delegation list updated — auth/portfolio/layout/middleware changes now route to `e2e` agent
- README architecture diagram and file structure updated

## [2026-03-06] — Docs agent + agent system refinements

### Added
- `docs` agent (`bright_cyan`) — keeps CLAUDE.md, README.md, and CHANGELOG.md up to date after significant changes
- Colour assigned to every agent in frontmatter for visual identification in Claude Code
- `tasks/` folder convention — solution-architect now writes a detailed `TASK-NNN-<slug>.md` file before delegating work
- Hook skip rule for CLAUDE.md, README.md, CHANGELOG.md to prevent re-triggering when docs agent writes

### Changed
- `ralph` agent now writes to `prd.json` **only on explicit request** — no longer invoked automatically after every change
- `solution-architect` delegation list updated to include `docs` agent
- `orchestrate-on-change.sh` — added `docs` category routing for `.claude/` config file changes; added ralph suppression note in context message
- CLAUDE.md and README.md updated to reflect new agent roster and revised ralph behaviour

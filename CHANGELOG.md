# Changelog

## [2026-03-26] — Conditional loops in pipeline (#6)

### Added
- **Conditional loop support** for pipeline stages — stages can repeat until an exit condition is met or a maximum repeat count is reached, enabling iterative review-fix cycles
- `loop` configuration on pipeline stage definitions with fields: `condition` (exit condition), `max_repeats` (1–10, default 3), `eval_mode` (`simple` field comparison or `ai` natural-language evaluation via Claude CLI), and `loopStages` (categories to repeat)
- `LoopConfig` Pydantic model for loop configuration validation
- **Pipeline visualization** (`supervisor/python/.../pipeline/visualize.py`) — renders pipeline stages as text diagrams showing linear flow, conditional branches, and loop back-edges
- Pipeline Stages section in PR body showing all possible branches and loop paths
- **Web UI pipeline viewer** on the task detail page — displays pipeline definitions with loop connections, back-edges, and tooltips
- `GET_PIPELINE_DEFINITIONS` GraphQL query with `PipelineDefinition` and `PipelineLoopConfig` types
- `LoopConfig` added to JSON schema (`config/schemas/pipeline-definition-v1.json`) for pipeline definition validation
- `quality-pipeline` example pipeline using AI-evaluated loop conditions
- Loop configuration added to `feature-pipeline` (review stage) and `pr-review-pipeline` (review stage)

### Changed
- `config/pipelines.yaml` — `feature-pipeline` bumped to v2.0.0 with review loop; `pr-review-pipeline` bumped to v2.0.0 with review loop; new `quality-pipeline` added
- GraphQL schema extended with pipeline definition and loop config types

## [2026-03-25] — Convert to yoyo migrations (#22)

### Added
- **yoyo-migrations** integration — database migrations now use the [yoyo-migrations](https://ollama.com/library/yoyo) framework instead of one-shot `docker-entrypoint-initdb.d` scripts
- `migrations` Docker Compose service — lightweight Python 3.12 Alpine container that runs `yoyo apply` on every `docker compose up`, ensuring the database schema is always current
- `db/Dockerfile` — builds the migrations container with `yoyo-migrations[postgres]`
- `db/yoyo.ini` — yoyo configuration (sources directory, database URL from `DATABASE_URL` env var)
- `db/migrate.sh` — helper script supporting `apply`, `rollback`, `reapply`, and `list` operations
- 26 `.rollback.sql` companion files — one for each migration, enabling safe rollback of any migration step
- `-- depends:` dependency headers in all migration SQL files establishing a linear migration chain

### Changed
- All 26 existing migration SQL files converted to yoyo format (added dependency headers, removed legacy `-- up`/`-- down` markers)
- `docker/compose.yml` — removed `initdb.d` volume mount from postgres; added `migrations` service; `api` now depends on `migrations` completing successfully
- `supervisor/templates/docker-compose.repo.yml.tmpl` — updated to match new migrations pattern

## [2026-03-25] — Redesign agents page (#1)

### Added
- **Agents page redesign** with tabbed layout: "Global Agents" and "Repository Agents" tabs
- New GraphQL queries: `globalAgents`, `repoAgentGroups` — return agent definitions grouped by source with override state
- New GraphQL mutations: `setAgentDisabled`, `modifyAgent`, `resetAgentModification`, `createAgentPR` — manage agent overrides and create PRs with agent changes
- New GraphQL types: `AgentDefinition`, `AgentSource` enum (`DEFAULT`, `GLOBAL_CONFIG`, `REPOSITORY`), `RepoAgentGroup`, `AgentDefinitionPayload`, `CreatePRPayload`
- `agent_overrides` database table — stores per-agent disable/enable state and modified spec (migration `019_agent_overrides_and_source.sql`)
- `source` column on `agent_definitions` table — tracks agent origin (`default`, `global:<repo>`, `repo:<repo>`)
- Frontend components: `GlobalAgentsTab`, `RepoAgentsTab`, `AgentTable`, `AgentEditDialog`
- `api/src/github-api.ts` — GitHub REST API helper for creating branches, commits, and PRs with agent changes
- 46 tests for agent queries and mutations

### Changed
- Agents page (`web/src/app/agents/page.tsx`) rewritten from runtime-metrics-only view to full agent management with disable/enable, edit, reset, and PR creation
- GraphQL schema extended with agent definition types and management operations

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

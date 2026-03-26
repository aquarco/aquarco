# Changelog

## [2026-03-26] — Powerful conditions in pipeline (#6)

### Added
- **Structured exit-gate conditions** — pipeline stages now support `simple:` (expression-based) and `ai:` (Claude CLI-evaluated) conditions with `yes:`/`no:` named-stage jumps and `maxRepeats:` loop guards
- **Pipeline categories** — `outputSchema` definitions moved from agent definitions into a top-level `categories:` section in `pipelines.yaml`, enabling schema resolution by stage category rather than agent name
- **Condition evaluation engine** (`supervisor/python/src/aquarco_supervisor/pipeline/conditions.py`) — recursive-descent expression parser supporting `==`, `!=`, `>=`, `>`, `<=`, `<`, `&&`, `||`, parentheses, `true`/`false` literals, and dotted field paths (e.g., `analysis.risks`)
- **AI condition evaluation** — `ai:` conditions are evaluated via Claude CLI with accumulated pipeline context; returns boolean yes/no
- **Named-stage execution flow** — stage execution loop replaced from linear `enumerate` to name-indexed while-loop with condition-driven jumps and repeat tracking
- **Database migration** `029_add_pipeline_categories.sql` — adds `categories JSONB DEFAULT '{}'` column to `pipeline_definitions` table
- **JSON schema update** (`config/schemas/pipeline-definition-v1.json`) — `categories` array, `name` on stages, structured `ConditionObject` (oneOf `simple`/`ai` with `yes`/`no`/`maxRepeats`)
- 97 new tests across `test_conditions_extended.py`, `test_executor_conditions.py`, `test_config_store_categories.py`, and `test_config_categories.py`

### Changed
- `StageConfig` model — added `name: str` field; `conditions` type changed from `list[str]` to `list[dict[str, Any]]`
- `PipelineConfig` model — added `categories: dict[str, dict[str, Any]]` for category-to-outputSchema mapping
- `load_pipelines()` — now parses `categories:` from YAML, builds name→outputSchema dict
- Output schema resolution — primary lookup is now `pipeline.categories[stage.category].outputSchema`; agent-level `outputSchema` is a fallback for backward compatibility
- `config_store.py` — `store_pipeline_definitions()` now persists `categories` JSONB alongside stages and trigger config
- `api/src/resolvers/mutations.ts` — removed `'output'` from `REQUIRED_SPEC_KEYS`
- `cli/agents.py` — removed `output.format` validation and `VALID_OUTPUT_FORMATS` check
- Pipeline definitions (`config/pipelines.yaml`) — all pipelines now use named stages with structured conditions instead of string-based conditions

## [2026-03-25] — Autoload .claude agents (#14)

### Added
- **Agent autoloader** (`supervisor/python/src/aquarco_supervisor/agent_autoloader.py`) — scans a repository's `.claude/agents/` directory for `.md` prompt files, analyzes them via Claude CLI to infer metadata (categories, tools, description), generates aquarco agent YAML definitions, writes them to `aquarco-config/agents/` in the repo, and stores them in the database with `source='autoload:<repo_name>'`
- **Database migration** `028_repo_agent_scans.sql` — new `repo_agent_scans` table tracking scan status (`pending`, `scanning`, `analyzing`, `writing`, `completed`, `failed`), agents found/created counts, and timestamps per repository
- **GraphQL query** `repoAgentScan(repoName)` — returns the latest agent scan status for a repository
- **GraphQL mutation** `reloadRepoAgents(repoName)` — triggers a rescan of `.claude/agents/` for on-demand agent reload
- **GraphQL types** `RepoAgentScan`, `RepoAgentScanStatus` enum, `RepoAgentScanPayload`
- **`AUTOLOADED` agent source** — new value in the `AgentSource` GraphQL enum distinguishing autoloaded agents from `DEFAULT`, `GLOBAL_CONFIG`, and `REPOSITORY` agents
- **Repository fields** `hasClaudeAgents: Boolean!` and `lastAgentScan: RepoAgentScan` on the `Repository` GraphQL type
- **Reload Agents button** on the Repositories page — triggers `reloadRepoAgents` mutation with scan progress polling and Snackbar result display
- **Autoloaded agents in RepoAgentsTab** — autoloaded agents displayed with "(autoloaded)" chip in the Repository Agents tab
- **Config overlay integration** — autoloaded agents merged as a 4th layer: `default → global_overlay → repo_overlay → autoloaded`
- 75 new tests across `test_agent_autoload.py`, `test_config_store_autoload.py`, and `test_config_overlay_autoload.py`

### Changed
- `config_store.py` — added `store_agent_definitions()` support for `autoload:` source prefix, `read_autoloaded_agents()`, and `deactivate_autoloaded_agents()` helpers
- `config_overlay.py` — added `merge_autoloaded_agents()` and updated `resolve_config()` to accept optional autoloaded agents parameter
- `web/src/app/repos/page.tsx` — added Reload Agents icon button per repository row
- `web/src/components/agents/RepoAgentsTab.tsx` — updated to display `AUTOLOADED` source agents

### Security
- Path traversal protection: only scans `.claude/agents/*.md` (no recursive traversal), filenames validated against `^[a-zA-Z0-9_-]+\.md$`
- Autoloaded agents inherit conservative default tools (`Read`, `Grep`, `Glob` only)
- Rate limited to 1 scan per repository per 5 minutes, max 20 agent prompts per scan, 50KB max prompt file size

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

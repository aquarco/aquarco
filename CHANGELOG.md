# Changelog

## [2026-04-12]

### Added
- **`aquarco backup`** — back up the PostgreSQL database and credentials directly to the host at `~/.aquarco/backups/<timestamp>/`. The pg_dump and credential files are streamed from the VM over SSH, so backups survive VM rebuilds. Flags: `--no-db`, `--no-creds`, `--output/-o`.
- **`aquarco init --dev`** — convenience flag to start the VM in development mode (mounts the source tree, uses editable installs).

### Changed
- **Vagrantfile** — synced folders are disabled by default. Use `aquarco init --dev` (or the `aquarco-dev` machine) to enable the source tree and log-export mounts.
- **`provision.sh`** — branches on `DEV_MODE` for all source-dependent steps: editable pip install vs bundled package, mounted vs inline systemd service files, `vboxsf` group membership, and log-export cron job.

## [2026-04-09] — Implement Git Workflow Specification (#118)

### Added
- **Git Workflow system** — automated branching and merging strategy supporting two modes:
  - **Simple Branch mode** — single-branch workflow (all work targets `main` or `master`)
  - **Git Flow mode** — structured multi-branch strategy with `develop`, `release/*`, `feature/*`, `bugfix/*`, and `hotfix/*` branches
- **Automatic back-merge logic** — when a PR merges into `release/*` or `main`, Aquarco automatically back-merges to prevent regressions (PR merged to release → `develop`; PR merged to main → active release or `develop`)
- **Task label support** — branch type controlled via `feature`, `bugfix`, `hotfix` labels and optional `target:` label to override default base branch
- **Conflict handling for back-merges** — conflicts during automatic back-merge are surfaced as new PRs labeled with `back-merge` and `conflict` for manual resolution
- **Active release branch detection** — automatically identifies newest `release/*` branch (by semantic version) that hasn't been fully merged to `main`
- **Comprehensive test coverage** — 61 new tests covering Git Flow scenarios: feature/bugfix/hotfix branch creation, automatic back-merging, conflict handling, empty release branches, and multiple active releases

### Fixed
- **CRITICAL** — Race condition in `perform_back_merge()` (git_workflow.py): Added HEAD-before/after comparison and dirty-state detection. If merge leaves dirty working tree without conflict markers (indicating merge failure), function now aborts and returns False instead of pushing incomplete merge
- **CRITICAL** — SQL NULL handling in `executor.py`: Changed `checkpoint_data || %(data)s::jsonb` to `COALESCE(checkpoint_data, '{}'::jsonb) || %(data)s::jsonb` to safely handle NULL checkpoint_data in UPSERT operations
- **CRITICAL** — Unsanitized CLI input in `git_workflow.py`: Added truncation of task_description to 500 characters before passing to `gh` CLI commands to prevent argument injection
- **WARNING** — Fetch failures in `find_active_release_branch()` now log warnings with context instead of silently passing
- **WARNING** — `rev-list` failures now log warnings with branch information for debugging
- **WARNING** — Merged PR timestamp comparison now uses parsed datetime objects instead of string comparison
- **PERFORMANCE** — Hoisted `find_active_release_branch()` call outside the merged-PR loop (reduced from O(N*M) to O(N) complexity)
- **PERFORMANCE** — UPSERT uses targeted jsonb_set with COALESCE to prevent concurrent write clobbering

### Test Coverage
- 61 new tests across two test files:
  - `test_pipeline/test_git_workflow_extended.py` — Git Flow scenarios (feature/bugfix/hotfix branch creation, back-merge routing, conflict handling)
  - `test_pollers/test_github_source_backmerge.py` — GitHub webhook integration and back-merge triggers
- 9 async integration tests covering `perform_back_merge()` success, conflict, dirty-state, already-up-to-date, and description truncation cases
- 8 tests for `find_active_release_branch()` including active branch selection, all-merged releases, no releases, and fetch failure scenarios
- All 103 tests passing with 89% code coverage

## [2026-04-08] — Consolidate migrations (#110)

### Added
- **Consolidated migration `000_consolidated_init.sql`** — single canonical database init script replacing 44 incremental migrations (000-043), creating the full as-built schema in one atomic operation with all 11 tables, indexes, triggers, functions, and seed data
- **Archive directory `db/migrations/archive/`** — all 44 previous migration files (and their rollbacks) archived for reference and audit trail, with clear documentation of consolidation rationale
- **Pre-flight guard in `db/migrate.sh`** — (3 bug fixes):
  - Changed hashing from MD5 (32 chars) to SHA-256 (64 chars) to match yoyo-migrations' internal algorithm, preventing re-application of consolidated migration on existing databases
  - Added existence check for `_yoyo_migration` table before querying, preventing UndefinedTable crash when bootstrapping from a dump
  - Added `ON CONFLICT (migration_hash) DO NOTHING` clause to INSERT statement to prevent unique constraint violations from concurrent migrate.sh processes
- **Test suite `db/tests/test_consolidation_details.py`** — 121 new tests covering:
  - Schema structure validation (tables, columns, types, constraints, indexes, triggers)
  - Foreign key relationships and referential integrity
  - Archive migration metadata validation
  - Migration ordering and dependencies
  - All tests pass with 95% coverage

### Changed
- **Migration strategy** — all future migrations now apply against the consolidated base in a single atomic operation, eliminating the pre-release incremental migration pattern
- **Migration test discovery** — test patterns updated to handle archive directory structure and validate both canonical and historical migrations

### Fixed
- **Migration idempotency** — consolidated init now applies safely to databases at any migration level (from scratch through 043)
- **Concurrent migration safety** — pre-flight guard now prevents race condition errors from parallel migrate.sh invocations
- **Database bootstrap reliability** — migrations now handle both fresh databases and database dumps without errors

### Test Coverage
- 121 new tests in `test_consolidation_details.py` covering schema validation, constraints, and migration integrity
- 57 existing migration tests updated and passing
- All 178 migration-related tests passing with 95% coverage

## [2026-04-08] — Simplify codebase and resolve code review findings (#109)

### Changed
- **Module decomposition** — refactored large monolithic files into smaller, focused modules following low-coupling, high-cohesion principles:
  - `supervisor/pipeline/executor.py` — split multi-stage executor logic into focused concerns; updated `StageRunner` and `PipelinePlanner` constructor calls
  - `supervisor/pipeline/stage_runner.py` — fixed NameError in worktree cleanup (line 658) from bare `_run_git()` to qualified `self._exec._run_git()`
  - `api/resolvers/` — consolidated duplicate `toDbEnum()` function with TypeScript overloads in `helpers.ts`, updated 5 sibling resolvers to import directly from `./mappers.js`
  - `web/components/tasks/TaskActions.tsx` — moved `'use client'` directive to line 1 (before JSDoc), matching project convention
- **GraphQL Stage type** — added missing optional fields `model` and `rawOutput` to TypeScript interface in `web/src/app/tasks/[id]/types.ts` to match backend schema

### Fixed
- **CRITICAL** — Fixed NameError in `stage_runner.py:658` that would crash during worktree cleanup; `_run_git()` now properly qualified as `self._exec._run_git()`
- **ERROR** — Fixed latent AttributeError in `executor.py:72,76` where unresolved `stage_manager` parameter could be None; now passes correctly resolved `self._sm` to constructors
- **ERROR** — Eliminated duplicate `toDbEnum()` function in `task-queries.ts` by extending shared version in `helpers.ts` with overloads, reducing code duplication
- **WARNING** — Updated resolver imports across `repo-mutations.ts`, `agent-mutations.ts`, `repo-queries.ts`, `task-queries.ts`, and `types.ts` to import directly from `./mappers.js` instead of through barrel file

### Test Coverage
- 395 new tests added across Python and TypeScript: executor wiring, stage runner parallel execution, backward compatibility, refactoring regression, resolver assembly, and task query logic
- All test suites passing: Python 2402/2402 (100%), API 276/276 (100%), Web 197/197 (100%) with 87% overall coverage
- No pre-existing test failures introduced

## [2026-04-08] — Add execution_order to stages (#102)

### Added
- **`execution_order` column on `stages` table** — new nullable integer column recording the actual sequence in which stages were invoked per task, enabling correct sort order when the conditions engine performs stage jumps (forward or backward)
- **Partial unique index** on `(task_id, execution_order)` WHERE `execution_order IS NOT NULL` ensuring no duplicate values can be assigned
- **Supervisor execution order tracking** — per-task counter in `pipeline/executor.py` tracks sequential execution, recovered from DB on task resume via `MAX(execution_order)` query
- **Parallel stage pre-allocation** — execution_order values pre-allocated before `asyncio.gather()` to avoid race conditions in concurrent stage execution
- **`executionOrder` field on GraphQL `Stage` type** — new nullable integer field exposing the execution_order value to API clients
- **Database migration 042** — adds nullable `execution_order` column, creates unique partial index, updates `get_task_context()` SQL function to include execution_order and sort by it

### Changed
- **Stage sort order in task context** — `get_task_context()` now returns stages sorted by `execution_order ASC NULLS LAST` instead of `(stage_number, iteration)`, preserving execution sequence for all queries
- **Frontend task detail page** — stage history list now sorts by `executionOrder NULLS LAST` with legacy fallback to `(stageNumber, iteration)` for backward compatibility
- **Stage queries across API** — all stage queries updated to include `execution_order` in result sets and mappings

### Fixed
- **Stage order display with stage jumps** — stages now display in actual execution order even when conditions engine jumps backward or forward (e.g., retry loops that run stages 0→1→0→2 now show in correct sequence, not deduplicated by stage_number)

### Test Coverage
- 32 new tests: `test_executor_execution_order.py` covers counter initialization, assignment, parallel pre-allocation, and resume recovery; `test_task_queue_execution_order.py` covers DB updates and NULL handling
- All 1433 tests passing with 83% coverage

## [2026-04-07] — Show token usage chart on Dashboard (#83)

### Added
- **Token Usage Chart** — new dashboard card displaying daily token consumption broken down by model (Opus, Sonnet, Haiku) with a stacked bar chart showing Input, Output, Cache Read, and Cache Write tokens
- **Model extraction from stages** — new `model` column on `stages` table populated from NDJSON `raw_output`; supervisor extracts model when completing stages via `parse_ndjson_spending()`
- **GraphQL `tokenUsageByModel` query** — new query accepting optional `days` parameter (clamped to [1, 365]) returning daily aggregated token data grouped by model; NULL models coalesced to `'unknown'` for backward compatibility
- **Model field on Stage type** — GraphQL `Stage` type now includes `model: String` field for accessing the per-stage model value
- **TokenUsageChart React component** (`web/src/components/dashboard/TokenUsageChart.tsx`) — recharts-powered stacked bar chart with token type toggle (Input/Output/Cache Read/Cache Write/Total), model color mapping (Opus=purple, Sonnet=blue, Haiku=green), and responsive skeleton loading state
- **Backfill script** (`db/scripts/backfill_stage_model.py`) — one-time operator-run script that parses existing `raw_output` NDJSON to retroactively populate `model` for completed stages
- **Database migration 040** — adds `model VARCHAR(100)` column to `stages` table with rollback support

### Changed
- **Dashboard layout** — added Token Usage Chart section showing last 30 days of model-based token consumption
- **`mapStage()` resolver** — now includes `model` field in Stage object mapping

### Test Coverage
- 58 new tests across Python (model extraction from NDJSON), GraphQL (tokenUsageByModel resolver with days parameter clamping), and React (TokenUsageChart component color mapping, token filtering, data transformation)
- All Python tests passing (16/16); API and Web tests written following existing patterns

## [2026-04-07] — Improve stage output rendering (#95)

### Added
- **Live output JSON parser** — `parseLiveOutput()` function extracts relevant fields from NDJSON stream-json output instead of displaying raw output
- **Supported live output fields**:
  - Top-level: `stdout`, `output`
  - Message content: `message.content[].thinking`, `message.content[].text`, `message.content[].content`
  - Message input: `message.content[].input.description`, `message.content[].input.file_path`
  - Tool results: `tool_use_result.stdout`, `tool_use_result.stderr`, `tool_use_result.content`, `tool_use_result.file.filePath`
- **Structured output display** — `StructuredOutputDisplay` React component renders agent structured output as Markdown-like format:
  - Field names (snake_case, camelCase) converted to Title Case section headings
  - String values rendered as heading + body text
  - Arrays rendered as ordered lists
  - "Findings" arrays (objects with `severity` and `message`) get special handling with numbered items, severity chips (color-coded: error/critical=red, warning=yellow), and file:line references
  - Numbers and booleans rendered as plain text
  - Other objects rendered as JSON code blocks
  - Fields prefixed with `_` are hidden from display
- **45 new tests** (`web/src/app/tasks/[id]/__tests__/stage-output-display.test.ts`) with 95% coverage for output parsing and display logic

### Changed
- **`Stage` GraphQL type** — removed `rawOutput` field; clients now use filtered and parsed `liveOutput` only
- **Task detail page** (`web/src/app/tasks/[id]/page.tsx`) — stage output now displays parsed live output and rendered structured output instead of raw output
- **`GET_TASK` GraphQL query** — removed `rawOutput` from stage selection

### Security
- Raw output (containing potentially sensitive tokens, logs, file paths) is no longer exposed to the UI; only structured and parsed output is displayed

## [2026-04-04] — Show token counts alongside costs (#82)

### Added
- **Total tokens display in Dashboard** — new "Tokens Today" stat card showing sum of all input, output, cache read, and cache write tokens from today's tasks
- **Token count in task list** — each row now displays per-task total token count alongside the cost
- **Per-stage token totals** — stage accordion headers now show the sum of all token columns for that stage next to the stage cost
- **`Task.totalTokens` GraphQL field** — new resolver that sums `tokens_input + tokens_output + cache_read_tokens + cache_write_tokens` for a given task

### Changed
- **`dashboardStats` SQL query** — now includes `cache_read_tokens + cache_write_tokens` in `totalTokensToday` sum (previously only counted input + output)
- **`formatTokens()` utility** (`web/src/lib/spending.ts`) — enhanced to handle null/undefined/zero values (returns "—" dash for empty)
- **GraphQL schema** — `Task` type now includes `totalTokens: Int` field

## [2026-04-04] — Production build: fix image versioning and backup serialization (#4)

### Fixed
- **`docker/compose.prod.yml`** — added explicit `AQUARCO_POSTGRES_VERSION` and `AQUARCO_CADDY_VERSION` variable overrides to `postgres` and `caddy` services, ensuring `versions.env` is the single source of truth for all pinned Docker image versions in production deployments
- **`vagrant/scripts/backup-credentials.sh`** — fixed manifest.json serialization: empty credential arrays (`found` and `missing`) now correctly emit `[]` instead of `[""]` via new `_json_array()` helper function
- **`cli/tests/test_commands/test_update.py`** — added `TestBackupRollbackIntegration` test class with 3 integration tests verifying rollback behavior: (1) rollback invoked when backup exists and update step fails, (2) rollback NOT invoked when no backup, (3) rollback invoked on provision failure with backup present

### Test Coverage
- All 217 CLI tests pass including 26 new rollback-related tests

## [2026-04-04] — Remove repository specific agents (#79)

### Removed
- **`agent_autoloader.py`** — entire module removed; autoloading subsystem no longer scans repos for `.claude/agents/` files
- **Autoloaded config layer** — removed from `config_overlay.py`; config merge now uses 3 layers: `default → global → repo`
- **`_load_autoloaded_agents()`** from `pipeline/agent_registry.py` — no longer loads autoloaded agents at startup
- **`_auto_scan_new_repos()` and `_process_agent_scan_commands()`** from `main.py` — autoload polling and IPC command handling removed
- **`deactivate_autoloaded_agents()` and `read_autoloaded_agents_from_db()`** from `config_store.py` — autoload-specific DB functions removed
- **`repo_agent_scans` table** — dropped via new migration `036_drop_repo_agent_scans.sql`
- **`repo-descriptor-agent`** — system agent removed (was used for heuristic-based agent analysis, never formally wired in)
- **Autoloaded agent fetching** from `pipeline/executor.py`'s `_resolve_layered_config()`

### Migration Path
The `agent_definitions` table columns `source` and `agent_group` are **preserved** — they are still used for tracking built-in, global, and repository-specific agents. Only the `autoload:` source prefix and the `repo_agent_scans` table are removed. Existing agent definitions with `source != 'autoload:*'` continue to work unchanged.

## [2026-04-03] — Merge agent definition and prompt file (#69)

### Changed
- **Agent file format** — agent definitions and system prompts now merged into single hybrid `.md` files with YAML frontmatter instead of separate YAML and markdown files
  - Each agent is a single file in `config/agents/definitions/{system,pipeline}/agent-name.md`
  - Frontmatter contains agent metadata (name, version, model, categories, tools, resources, environment)
  - Markdown content contains the full system prompt
- **Agent registry parsing** (`supervisor/python/src/aquarco_supervisor/pipeline/agent_registry.py`) — updated `_discover_agents_from_dir()` to glob `.md` files and extract YAML frontmatter instead of reading separate `.yaml` files
- **Schema validation** (`config/schemas/`) — agent-definition-v1.json, system-agent-v1.json, and pipeline-agent-v1.json updated to validate flat frontmatter format instead of Kubernetes-style envelope
- **Removed** — `config/agents/prompts/` directory (prompts now inline in `.md` files)

### Migration
- All 9 existing agents (3 system + 6 pipeline) migrated to new hybrid format
- Agent discovery logic enhanced to handle both `role` (system agents) and `categories` (pipeline agents) in frontmatter

## [2026-04-03] — Aquarco CLI enhancements (#71)

### Added
- **`-h` alias for `--help`** on all CLI commands — added `context_settings={"help_option_names": ["-h", "--help"]}` to main app and all sub-apps (Typer/Click integration)
- **`--port` option on `aquarco init`** — allows custom port configuration (default 8080) with propagation to Vagrant port forwarding and Caddyfile template
- **Docker healthchecks** for `web` (Next.js on 3000) and `api` (GraphQL on 4000) services in `compose.yml` with `depends_on: condition: service_healthy` chains to ensure services are up before dependent services start
- **Smart `aquarco auth` command** — bare invocation (`aquarco auth` without subcommand) now auto-detects unauthenticated services, runs Claude OAuth and GitHub device flows as needed, and shows status at the end
- **`aquarco repos` subcommands with `--json` support** — `aquarco repos list` now supports `--json` output for machine-readable repository listing
- **`aquarco auth status` with `--json` support** — output authentication status as JSON
- **`aquarco ui` subcommands** — new command structure with `aquarco ui web` (default), `aquarco ui db` (Adminer), `aquarco ui api` (GraphQL playground), and `aquarco ui stop` (stop all services except API)
- **`--no-open` flag for `aquarco ui`** — inverted flag; browser opens by default, use `--no-open` to suppress it
- **GraphQL drain mode API** — new `setDrainMode(enabled: Boolean!)` mutation and `drainStatus` query for graceful supervisor restart: supervisor stops picking up new work when drain is enabled, waits for all stages to reach WAITING state, then auto-restarts
- **Graceful supervisor restart in `aquarco update`** — three-way prompt when active agents are present: "Yes" (restart immediately), "No" (abort), "Plan update when idle" (enable drain mode)
- **Graceful error messages for API connectivity issues** — `aquarco auth status`, `aquarco status`, `aquarco repos list` and other API-dependent commands now catch connection errors and show friendly message instead of raw exception
- **Improved `aquarco status --help` text** — clarified that `TASK_ID` is optional and documented all options and example usage

### Changed
- **Renamed `aquarco install` → `aquarco init`** — aligns with standard initialization terminology; all help text, tests, and error messages updated
- **Renamed `aquarco watch` → `aquarco repos`** — better reflects the command's purpose; all subcommands (`add`, `list`, `remove`) and tests renamed accordingly
- **Removed `git pull` step from `aquarco update`** — users manage host-side git themselves; VirtualBox shared folders sync code without git pull
- **Supervisor drain mode integration** — supervisor main loop checks drain flag and halts new task pickup; auto-restarts when all stages reach WAITING; CLI `update.py` queries drain status and presents multi-phase flow
- **Port persistence in `aquarco init`** — uses Click's `ParameterSource` detection to distinguish explicit `--port` flags from defaults; only saves config when explicitly passed

### Fixed
- **Authentication guard on `setDrainMode` mutation** — requires `AQUARCO_INTERNAL_API_KEY` environment variable for security
- **Exception handling in `update.py` drain mode** — specific exception types (KeyError, TypeError) and descriptive logging instead of bare Exception catches
- **Auth callback robustness** — catches all exceptions to ensure both Claude and GitHub login flows are always attempted
- **Combined drain status query** — single atomic SQL query instead of 3 sequential queries for consistent reads

## [2026-03-31] — Per-agent model selection (#60)

### Added
- **`model` field** in agent definition schemas (`system-agent-v1.json`, `pipeline-agent-v1.json`) — optional string specifying which Claude model the agent should use (e.g., `claude-sonnet-4-6`, `claude-haiku-4-5`). When omitted, the CLI uses its default model.
- **`get_agent_model()`** on `AgentRegistry` — retrieves the configured model for a given agent name, returning `None` when not set
- **`get_agent_model()`** on `ScopedAgentView` (`config_overlay.py`) — resolves model through the multi-layer config overlay (default → global → repo), enabling per-repo model overrides
- **`--model` flag** passed to Claude CLI (`cli/claude.py`) — `execute_claude()` accepts an optional `model` parameter and appends `--model <value>` to the CLI args when set
- **Model resolution in executor** (`pipeline/executor.py`) — both `_execute_agent()` and condition-evaluator invocations now resolve and pass the agent's model to the CLI
- 39 new tests in `supervisor/python/tests/test_model_per_agent.py`

### Changed
- **All agent definitions** updated with explicit `model` values:
  - Pipeline agents (`analyze`, `design`, `implementation`, `review`, `test`, `docs`): `claude-sonnet-4-6`
  - System agents (`planner`): `claude-sonnet-4-6`
  - System agents (`condition-evaluator`, `repo-descriptor`): `claude-haiku-4-5`
- **Legacy flat-directory agent definitions** (`config/agents/definitions/*.yaml`) also updated with `model` field for backward compatibility

## [2026-03-31] — Aquarco CLI (#5)

### Added
- **`aquarco` CLI** (`cli/`) — host-side Python CLI (Typer + Rich + httpx) for managing the Aquarco VM from macOS without SSHing manually
- **`aquarco init`** — one-command bootstrap: checks prerequisites (VirtualBox, Vagrant), runs `vagrant up`, and verifies stack health. Supports `--port` option (default 8080)
- **`aquarco update`** — pulls latest source, Docker images, runs migrations, restarts services, and re-provisions the VM. Supports `--dry-run`, `--skip-migrations`, and `--skip-provision` flags
- **`aquarco auth claude`** — initiates Claude OAuth PKCE flow via the GraphQL API, opens browser for authorization
- **`aquarco auth github`** — initiates GitHub device flow login via the GraphQL API
- **`aquarco auth status`** — checks authentication status for both Claude and GitHub
- **`aquarco repos add <url>`** — registers a repository for autonomous watching via `registerRepository` GraphQL mutation. Options: `--name`, `--branch`, `--poller`
- **`aquarco repos list`** — lists all watched repositories with clone status and poller info
- **`aquarco repos remove <name>`** — removes a watched repository
- **`aquarco run <title> --repo <name>`** — creates a task for agent execution via `createTask` mutation. Options: `--pipeline`, `--priority`, `--context` (JSON string or `@filepath`), `--follow`
- **`aquarco status`** — dashboard overview with task counts, active agents, and cost. Options: `--json`, `--limit`
- **`aquarco status <task-id>`** — detailed task view with stage history. Options: `--follow`, `--json`
- **`aquarco ui`** — starts web UI services (web + API + Postgres + Caddy). Option: `--open` to launch browser
- **`aquarco ui stop`** — stops the web UI services
- **Shared modules**: `VagrantHelper` (SSH/provision), `GraphQLClient` (httpx-based), `AquarcoConfig` (Vagrantfile discovery), `console` (Rich formatting), `health` (endpoint checks)
- Installable via `pip install -e cli/` — registers `aquarco` console script

## [2026-03-31] — Unified reverse proxy routing via Caddy (#2)

### Added
- **Caddy reverse proxy** (`docker/caddy/Caddyfile`) — single-port entry point on `:8080` with path-based routing to all services
- **Caddy Docker service** in `docker/compose.yml` — `caddy:2-alpine` container with Caddyfile mount, admin API on `127.0.0.1:2019`, and named volumes (`caddy_data`, `caddy_config`)
- **Monitoring network bridge** — `docker/compose.monitoring.yml` now joins the `aquarco` network so Caddy can reach Grafana and Prometheus

### Changed
- **`docker/compose.yml`** — `web` service no longer exposes external ports (Caddy handles routing); `api` restricted to `127.0.0.1:4000` for debug access only; added `NEXT_PUBLIC_API_URL: /api/graphql` to `web` environment
- **`docker/compose.dev.yml`** — Adminer no longer exposes a direct port; accessed via `/adminer/*` through Caddy
- **`docker/compose.monitoring.yml`** — Grafana and Prometheus direct ports removed; Grafana configured with `GF_SERVER_SERVE_FROM_SUB_PATH` at `/grafana/`; Prometheus configured with `--web.external-url` and `--web.route-prefix` at `/prometheus`
- **`web/src/lib/apollo.tsx`** — browser-side API URL changed from absolute to relative (`/api/graphql`); SSR continues to use `http://api:4000/graphql`
- **`vagrant/Vagrantfile`** — reduced to two system port forwards: `8080` (Caddy proxy) and `15432` (PostgreSQL); removed individual forwards for API, Grafana, Prometheus, and Adminer

### Routing Map
```
:8080 (single port)
  /              → web:3000        (Next.js)
  /api/*         → api:4000        (GraphQL, path stripped)
  /adminer/*     → adminer:8080    (path stripped)
  /grafana/*     → grafana:3000    (subpath-aware)
  /prometheus/*  → prometheus:9090 (subpath-aware)
  /repo/*        → 503 placeholder (Phase 2)
```

## [2026-03-30] — Expand retryable error handling to cover HTTP 500 and 529 (#41)

### Added
- **`RetryableError`** base exception class (`exceptions.py`) — sits between `AgentExecutionError` and the concrete postponable errors; catching it covers all transient Claude API errors in a single clause
- **`ServerError(RetryableError)`** — raised when the Claude CLI exits non-zero and stdout or the debug log contains `"api_error"` / `"status code 500"` signals; postponed with a 30-minute cooldown (max 12 retries)
- **`OverloadedError(RetryableError)`** — raised when the same signals indicate `"overloaded_error"` / `"status code 529"`; postponed with a 15-minute cooldown (max 24 retries)
- **`_cooldown_for_error(e)`** helper (`exceptions.py`) — returns `(cooldown_minutes, max_retries)` for any `RetryableError` subtype; used by both executor and the main-loop defensive handler
- **`_is_server_error_in_lines()` / `_is_server_error()`** (`cli/claude.py`) — detection helpers for HTTP 500 signals in NDJSON stdout lines and debug log file respectively
- **`_is_overloaded_in_lines()` / `_is_overloaded()`** (`cli/claude.py`) — detection helpers for HTTP 529 signals
- **`postpone_task()`** method on `TaskQueue` (`task_queue.py`) — generalised postpone with configurable `cooldown_minutes` and `max_retries`; persists the cooldown value to `tasks.postpone_cooldown_minutes` so the resume poller uses per-task wait times
- **`get_postponed_tasks()`** method on `TaskQueue` — replaces `get_rate_limited_tasks()`; queries `status='rate_limited'` using the per-row `postpone_cooldown_minutes` column instead of a fixed 60-minute constant
- **Database migration `031_add_postpone_cooldown.sql`** — adds `postpone_cooldown_minutes INTEGER NOT NULL DEFAULT 60` column to `tasks` table; rollback file included

### Changed
- **`RateLimitError`** now inherits from `RetryableError` instead of directly from `AgentExecutionError` — all existing `isinstance(e, RateLimitError)` checks continue to pass; existing 429 behaviour is unchanged (60-minute cooldown, 24 max retries)
- **`execute_claude()`** (`cli/claude.py`) — raises `ServerError` or `OverloadedError` before the generic `AgentExecutionError` fallthrough when the process exits non-zero
- **`_execute_running_phase()`** (`pipeline/executor.py`) — `except RateLimitError` replaced with `except RetryableError`; calls `postpone_task()` with per-type cooldown values from `_cooldown_for_error()`
- **`rate_limit_task()`** (`task_queue.py`) — delegates to `postpone_task(cooldown_minutes=60, max_retries=...)`; behaviour unchanged, kept for backward compatibility
- **`_resume_rate_limited_tasks()`** (`main.py`) — calls `get_postponed_tasks()` instead of `get_rate_limited_tasks(cooldown_minutes=60)`

## [2026-03-27] — Show full pipeline execution history on task detail page (#39)

### Added
- **`iteration: Int!` and `run: Int!` fields** on the GraphQL `Stage` type — expose per-stage loop counters so clients can distinguish repeated runs of the same stage
- **Flat chronological history list** on the task detail page — every execution of every stage is now visible; repeated runs are labeled "(next run)", "(3rd run)", etc.
- **Evaluation info blocks** between stage runs — show `_condition_message` from the condition evaluator so users can see why a stage was retried or why the pipeline advanced

### Changed
- **`stagesByTaskLoader`** (`api/src/loaders.ts`) — removed `DISTINCT ON` so all stage rows are returned ordered by `(stage_number, iteration, run)` instead of only the latest run per stage
- **`pipelineStatus` query** (`api/src/resolvers/queries.ts`) — removed `DISTINCT ON`; `totalStages` now counts distinct `stage_number` values rather than total rows
- **`mapStage` mapper** (`api/src/resolvers/mappers.ts`) — maps the new `iteration` and `run` fields from `StageRow` to the GraphQL type
- **`GET_TASK` fragment** (`web/src/lib/graphql/queries.ts`) — includes `iteration` and `run` in the stages selection
- **`PipelineStagesFlow` SVG diagram** (`web/src/app/tasks/[id]/page.tsx`) — deduplicates stage nodes (one node per `stageNumber`, latest run wins for status colouring) so the diagram still shows the pipeline definition shape
- **Stage Output accordion headers** — standardized to 1-based display (`stageNumber + 1`) to match the SVG flow diagram

## [2026-03-26] — Separate system agents from pipeline agents (#30)

### Added
- **`config/agents/definitions/system/`** — new subdirectory for system agents that orchestrate pipeline execution (`planner-agent.yaml`, `condition-evaluator-agent.yaml`, `repo-descriptor-agent.yaml`)
- **`config/agents/definitions/pipeline/`** — new subdirectory for pipeline stage agents (`analyze-agent.yaml`, `design-agent.yaml`, `implementation-agent.yaml`, `review-agent.yaml`, `test-agent.yaml`, `docs-agent.yaml`)
- **`config/schemas/system-agent-v1.json`** — JSON schema for system agents; uses `spec.role` instead of `spec.categories`, capped at 20 turns and 0.5 USD default cost
- **`config/schemas/pipeline-agent-v1.json`** — JSON schema for pipeline agents; structurally identical to `agent-definition-v1.json` with updated `$id` and title
- **`condition-evaluator-agent`** (system) — formal agent definition + prompt file (`config/agents/prompts/condition-evaluator-agent.md`) for AI pipeline condition evaluation; previously an inline Claude CLI call with a hardcoded system prompt
- **`repo-descriptor-agent`** (system) — formal agent definition + prompt file for future repo `.claude/agents/` analysis; autoloader continues to use heuristics for now
- **`sync_all_agent_definitions_to_db()`** in `config_store.py` — loads from `system/` and `pipeline/` subdirectories, validates each against the correct schema, and sets `agent_group` column; falls back to flat scan for backward compatibility
- **`agent_group` column** on `agent_definitions` table (migration `030_add_agent_group.sql`) — values `'system'` or `'pipeline'`; known system agents back-filled by name on migration
- **`get_agent_group(agent_name)`** and **`get_system_agent_by_role(role)`** on `AgentRegistry` — look up agent classification at runtime
- **`AgentGroup` enum** (`SYSTEM` / `PIPELINE`) in GraphQL schema; `group: AgentGroup!` field added to `AgentDefinition` type
- **`SYSTEM_AGENT_NAMES`** constant in `constants.py` — canonical list of system agent names used for backward-compat group inference
- **Pipeline Agents / System Infrastructure sections** in `GlobalAgentsTab` — agents are split into two visually distinct groups in the web UI
- 11 new tests across `test_agent_registry.py`, `test_conditions.py`, and `test_main.py`

### Changed
- **`planner-agent.yaml`** — migrated to `system/` directory and fixed: removed invalid `categories: [planning]` and `priority: 0` fields; added `role: planner`; now validates cleanly against `system-agent-v1.json`
- **`agent_registry.py`** — `get_agents_for_category()` now skips system agents; `_discover_agents()` scans subdirectories with group tagging; autoloaded agents always tagged as `pipeline`
- **`conditions.py`** — `evaluate_ai_condition()` loads system prompt from `condition-evaluator-agent.md` when `prompts_dir` is provided; falls back to inline `_INLINE_SYSTEM_PROMPT` constant when file is absent
- **`main.py`** — `_sync_definitions_to_db` updated to call `sync_all_agent_definitions_to_db()` and reference `system-agent-v1.json` / `pipeline-agent-v1.json` schema paths

## [2026-03-26] — Stream-json CLI output for real-time agent events (#17)

### Added
- **`--output-format stream-json`** — `execute_claude()` now runs the Claude CLI with `--output-format stream-json`, reading live NDJSON events from stdout line by line instead of waiting for full output
- **`ClaudeOutput` dataclass** — separates structured output (parsed from the `result` event) and raw output (all NDJSON lines joined) for clean downstream consumption
- **`_read_stream_json()`** — async coroutine that reads NDJSON lines from the subprocess stdout pipe, updates an inactivity timestamp on each event, signals when the `result` event arrives, and invokes an optional `on_live_output` callback per event
- **`_monitor_for_inactivity_stream()`** — async coroutine that polls for inactivity after the `result` event is seen and kills the subprocess if no events arrive within `inactivity_timeout` seconds (default 90s)
- **`_parse_ndjson_output()`** — parses a list of NDJSON lines, locates the `{type: "result"}` event, and delegates to `_extract_from_result_message()`; falls back to extracting JSON from assistant text blocks
- **`_is_rate_limited_in_lines()`** — checks NDJSON stdout lines for rate-limit indicators (`rate_limit_error`, `status code 429`)
- **`on_live_output` parameter** on `execute_claude()` — optional async callback `Callable[[str], Awaitable[None]]` invoked immediately per NDJSON event for real-time streaming to callers
- 68 new tests in `supervisor/python/tests/test_stream_json.py` and `test_stream_json_coverage.py`

### Changed
- `execute_claude()` — replaced single `await proc.communicate()` call with concurrent `stream_task` + `monitor_task` managed via `asyncio.wait()`; inactivity detection is now event-driven (tied to `result` event) rather than based on overall wall-clock timeout
- Return type of `execute_claude()` changed from `dict[str, Any]` to `ClaudeOutput`
- `_parse_output()` retained as a backward-compatible function for existing tests; new code uses `_parse_ndjson_output()`
- Stderr is now written to a separate `.stderr` log file alongside the existing debug log; rate-limit detection reads from both files

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

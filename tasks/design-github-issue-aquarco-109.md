# Design: Codebase Simplification (Issue #109)

**Date**: 2026-04-08  
**Issue**: https://github.com/aquarco/aquarco/issues/109  
**Goal**: Break large multi-responsibility files into smaller, focused modules following low-coupling/high-cohesion principles.

---

## Overview

Eight large files span 7,118 lines across three layers (supervisor Python, GraphQL API, Next.js web). Each file mixes multiple unrelated responsibilities. This document describes a sequenced refactoring into 22 focused modules with no behavior changes.

The work is split into **10 steps**, ordered smallest-to-largest change and from least-to-most risky. Steps 1–4 are purely additive (new files + import updates). Steps 5–10 involve larger module splits. The implementation agent must run the full test suite after each step before proceeding.

---

## Assumptions

1. **No behavior changes** — all logic is moved verbatim. No algorithmic changes.
2. **Backward-compat re-exports** — for the web GraphQL `queries.ts` split, the original file re-exports from new sub-files so that existing consumers (10+ files) need no immediate update. Consumers can migrate gradually.
3. **API test file imports** — `api/src/__tests__/*.test.ts` files that import from `queries.js`/`mutations.js` will be updated to import from the new domain files.
4. **`TaskQueue` keeps poll_state methods** — pollers call `tq.update_poll_cursor()` / `tq.get_poll_cursor()`. These stay on `TaskQueue` to avoid updating 4 poller files. `StageManager` is a new class introduced alongside.
5. **`check_conditions` / `_resolve_field` / `_compare_complexity`** stay in `executor.py` (legacy sync bridge functions). They are not moved in this refactor.

---

## File-Level Design

### Layer A: `supervisor/python` — CLI parsing

#### Step 1: `cli/output_parser.py` (new — ~480 lines)

Extract all NDJSON parsing and error-detection pure functions from `cli/claude.py`:

| Function | Currently in |
|---|---|
| `_parse_ndjson_output` | `claude.py:566` |
| `_parse_output` (legacy) | `claude.py:683` |
| `_find_result_message` | `claude.py:721` |
| `_extract_structured_output_tool_use` | `claude.py:729` |
| `_extract_result_metadata` | `claude.py:759` |
| `_extract_from_result_message` | `claude.py:782` |
| `_extract_json` | `claude.py:838` |
| `_extract_session_id_from_lines` | `claude.py:627` |
| `_is_rate_limited_in_lines` | `claude.py:644` |
| `_is_server_error_in_lines` | `claude.py:653` |
| `_is_overloaded_in_lines` | `claude.py:674` |
| `_is_rate_limited` (debug log file) | `claude.py:862` |
| `_is_server_error` (debug log file) | `claude.py:879` |
| `_is_overloaded` (debug log file) | `claude.py:899` |
| `_format_schema_prompt` | `claude.py:229` |

After extraction, `claude.py` shrinks from 914 to ~330 lines (retains `ClaudeOutput`, constants, `_tail_file`, `_read_file_tail`, `_scan_file_for_rate_limit_event`, `execute_claude`).

`claude.py` adds at the top:
```python
from .output_parser import (
    _parse_ndjson_output, _extract_session_id_from_lines,
    _is_rate_limited_in_lines, _is_server_error_in_lines, _is_overloaded_in_lines,
    _is_rate_limited, _is_server_error, _is_overloaded,
    _format_schema_prompt,
)
```

---

### Layer B: `supervisor/python` — Stage management

#### Step 2: `stage_manager.py` (new — ~780 lines)

Extract all stage DB operations from `task_queue.py`. Create `StageManager` class:

| Method | Currently in |
|---|---|
| `_resolve_stage_status` (free fn) | `task_queue.py:10` |
| `create_system_stage` | `task_queue.py:629` |
| `record_stage_executing` | `task_queue.py:670` |
| `record_stage_failed` | `task_queue.py:752` |
| `record_stage_skipped` | `task_queue.py:813` |
| `create_pending_stages` | `task_queue.py:867` |
| `create_planned_pending_stages` | `task_queue.py:895` |
| `create_iteration_stage` | `task_queue.py:~960` |
| `create_rerun_stage` | `task_queue.py:1147` |
| `store_stage_output` | `task_queue.py:337` |
| `update_stage_live_output` | `task_queue.py:~1040` |
| `get_latest_stage_run` | `task_queue.py:1128` |
| `update_checkpoint` | `task_queue.py:~480` |
| `get_stage_number_for_id` | `task_queue.py:1298` |
| `get_max_execution_order` | `task_queue.py:1306` |
| `get_max_iteration` | `task_queue.py:1286` |
| `get_task_context` | `task_queue.py:~550` |

After extraction, `task_queue.py` shrinks from 1,316 to ~450 lines (retains all task lifecycle methods: `create_task`, `get_next_task`, `get_task`, `update_task_status`, `task_exists`, `fail_task`, `postpone_task`, `rate_limit_task`, `get_postponed_tasks`, `get_rate_limited_tasks`, `resume_rate_limited_task`, `complete_task`, `close_task`, `retry_task`, `rerun_task`, `store_pr_info`, `get_tasks_pending_close`, `update_poll_cursor`, `get_poll_cursor`).

`StageManager.__init__` signature:
```python
def __init__(self, db: Database) -> None:
```

`stage_manager.py` imports `parse_ndjson_spending` from `spending.py` (same as task_queue currently does).

`executor.py` updated to:
1. Import `StageManager` from `..stage_manager`
2. Accept `stage_manager: StageManager` as `__init__` parameter (or create it internally from `db`)
3. Replace all `self._tq.<stage_method>(...)` calls with `self._sm.<stage_method>(...)`

`main.py` updated to instantiate `StageManager(db)` and pass to `PipelineExecutor`.

---

### Layer C: `supervisor/python` — Pipeline git operations

#### Step 3: `pipeline/git_ops.py` (new — ~80 lines)

Move the module-level git functions from the bottom of `executor.py` (lines 1502–1539):

```python
# pipeline/git_ops.py
async def _git_checkout(clone_dir: str, branch: str) -> None: ...
async def _auto_commit(clone_dir: str, task_id: str, stage_num: int, category: str) -> None: ...
async def _push_if_ahead(clone_dir: str, branch: str) -> None: ...
async def _get_ahead_count(clone_dir: str, branch: str, base: str = "main") -> int: ...
```

`executor.py` imports these from `.git_ops` instead of defining them locally. `executor.py` loses ~40 lines.

---

### Layer D: `supervisor/python` — config_store split

#### Step 4a: `agent_store.py` (new — ~280 lines)

Extract agent-definition CRUD from `config_store.py`:
- `load_agent_definitions_from_files`
- `upsert_agent_definitions_to_db`
- `sync_all_agent_definitions_to_db`
- `export_agent_definitions_from_db`
- `_parse_md_frontmatter` (shared helper — keep in `config_store.py` and import from there)

#### Step 4b: `pipeline_store.py` (new — ~230 lines)

Extract pipeline-definition CRUD from `config_store.py`:
- `load_pipeline_definitions_from_file`
- `upsert_pipeline_definitions_to_db`
- `sync_pipeline_definitions_to_db`
- `export_pipeline_definitions_from_db`

After extraction, `config_store.py` shrinks from 597 to ~120 lines (retains schema validation helpers: `_load_json_schema`, `validate_agent_definition`, `validate_pipeline_definition`, `_parse_md_frontmatter`, constants `AGENT_API_VERSION`, `AGENT_KIND`, `PIPELINE_KIND`).

`main.py` imports updated:
```python
from .agent_store import sync_all_agent_definitions_to_db
from .pipeline_store import sync_pipeline_definitions_to_db
```

---

### Layer E: `supervisor/python` — executor split into planner + stage_runner

#### Step 5: `pipeline/planner.py` (new — ~160 lines)

Extract planning phase from `PipelineExecutor`:

```python
class PipelinePlanner:
    def __init__(
        self,
        tq: TaskQueue,
        sm: StageManager,
        registry: AgentRegistry,
        next_execution_order: Callable[[str], int],
    ) -> None: ...

    def build_default_plan(self, stages: list[dict]) -> list[dict]: ...

    async def execute_planning_phase(
        self,
        task_id: str,
        pipeline_name: str,
        stages: list[dict],
        context: dict,
    ) -> list[dict]: ...
```

`execute_planning_phase` calls `self._execute_agent(...)` where `_execute_agent` is extracted to `AgentInvoker` (Step 6). The planner receives an `AgentInvoker` reference.

#### Step 6: `pipeline/agent_invoker.py` (new — ~220 lines)

Extract `_execute_agent` from `PipelineExecutor` into a focused class:

```python
class AgentInvoker:
    def __init__(
        self,
        db: Database,
        registry: AgentRegistry,
        pipelines: list[PipelineConfig],
    ) -> None: ...

    def get_output_schema_for_stage(
        self, pipeline_name: str, category: str, agent_name: str
    ) -> dict | None: ...

    async def execute_agent(
        self,
        agent_name: str,
        task_id: str,
        context: dict,
        stage_num: int,
        *,
        work_dir: str | None = None,
        on_live_output: Callable | None = None,
        pipeline_name: str = "",
        category: str = "",
        resume_session_id: str | None = None,
    ) -> dict: ...
```

Contains the max-turns continuation loop (lines 1047–1190 of `executor.py`), rate-limit detection, and raw output handling.

#### Step 7: `pipeline/stage_runner.py` (new — ~450 lines)

Extract stage execution methods from `PipelineExecutor`:

```python
class StageRunner:
    def __init__(
        self,
        db: Database,
        tq: TaskQueue,
        sm: StageManager,
        registry: AgentRegistry,
        invoker: AgentInvoker,
        next_execution_order: Callable[[str], int],
    ) -> None: ...

    async def execute_running_phase(...) -> bool: ...
    async def execute_planned_stage(...) -> tuple[dict, int | None]: ...
    async def execute_parallel_agents(...) -> dict: ...
    async def execute_stage(...) -> dict: ...  # legacy path
```

The `_ai_eval` nested closure (executor.py lines 492–555) is lifted to a private method `_run_condition_eval(...)` on `StageRunner`, eliminating the deeply nested async closure pattern.

After Steps 5–7, `executor.py` shrinks from 1,539 to ~480 lines (retains `PipelineExecutor` with `execute_pipeline`, worktree lifecycle, `_setup_branch`, `_get_repo_branch`, `_create_pipeline_pr`, `_resolve_clone_dir`, `close_task_resources`, `_get_repo_slug`, `_next_execution_order`; delegates planning to `PipelinePlanner` and stage execution to `StageRunner`).

---

### Layer F: API resolvers split

#### Step 8: `api/src/resolvers/helpers.ts` (new — ~55 lines)

Extract shared utilities from `mutations.ts` used across multiple resolver files:
```typescript
function toDbEnum(value: string): string
function taskPayload(task)
function errorPayload(field, message)
function repoErrorPayload(field, message)
function agentErrorPayload(field, message)
function prErrorPayload(message)
function validateScope(scope): string | null
function validateSpec(spec: unknown): string | null
const SCOPE_PATTERN
const VALID_SPEC_KEYS
const REQUIRED_SPEC_KEYS
const MAX_SPEC_SIZE
```

#### Step 8a: `api/src/resolvers/task-mutations.ts` (new — ~220 lines)

Extract task-related mutations from `mutations.ts`:
- `createTask`, `updateTaskStatus`, `retryTask`, `rerunTask`, `closeTask`, `cancelTask`, `unblockTask`

#### Step 8b: `api/src/resolvers/repo-mutations.ts` (new — ~160 lines)

Extract repository mutations:
- `registerRepository`, `retryClone`, `removeRepository`, `setDrainMode`

#### Step 8c: `api/src/resolvers/agent-mutations.ts` (new — ~160 lines)

Extract agent mutations:
- `setAgentDisabled`, `modifyAgent`, `resetAgentModification`, `createAgentPR`

#### Step 8d: `api/src/resolvers/mutations.ts` reduces to ~60 lines

Becomes an assembler that imports and spreads all domain mutation objects:
```typescript
import { taskMutations } from './task-mutations.js'
import { repoMutations } from './repo-mutations.js'
import { agentMutations } from './agent-mutations.js'
// auth mutations stay inline (6 small methods)
export const Mutation = {
  ...authMutations,
  ...taskMutations,
  ...repoMutations,
  ...agentMutations,
}
```

Auth mutations (`githubLoginStart`, `githubLoginPoll`, `githubLogout`, `claudeLoginStart`, `claudeLoginPoll`, `claudeSubmitCode`, `claudeLogout`) stay inline in `mutations.ts` as they are thin wrappers over auth imports.

#### Step 8e: `api/src/resolvers/mappers.ts` addition

`mapRepository`, `mapStage`, `mapAgentDefinition`, `fetchAgentWithOverrides`, `getDrainStatus` currently live in `queries.ts` and are imported by `mutations.ts` and tests. Move these into `mappers.ts` (already exists). Update `queries.ts` and test imports.

#### Step 8f: `api/src/resolvers/task-queries.ts` (new — ~120 lines)

Extract task-related queries:
- `task`, `tasks`, `stage`, `taskStages`, `dashboardStats`, `tokenUsageByModel`

#### Step 8g: `api/src/resolvers/repo-queries.ts` (new — ~80 lines)

Extract repository queries:
- `repository`, `repositories`, `githubRepositories`, `githubBranches`, `githubAuthStatus`, `claudeAuthStatus`, `drainStatus`

`queries.ts` reduces to ~80 lines (retains agent queries + pipeline queries + assembler). Update `queries.ts` to assemble `Query` from sub-objects:
```typescript
import { taskQueries } from './task-queries.js'
import { repoQueries } from './repo-queries.js'
export const Query = { ...taskQueries, ...repoQueries, ...agentQueries }
```

Update test files: `resolvers.test.ts`, `pipeline-history.test.ts`, `token-usage-resolver.test.ts`, `query-resolvers-extended.test.ts`, `agent-queries.test.ts`, `type-resolvers.test.ts` to import from the new domain files instead of `queries.js`.

---

### Layer G: Web — GraphQL queries split

#### Step 9: Split `web/src/lib/graphql/queries.ts`

Create domain-specific files:

**`web/src/lib/graphql/task-queries.ts`** (~200 lines):
`GET_TASKS`, `GET_TASK`, `CREATE_TASK`, `UPDATE_TASK_STATUS`, `RETRY_TASK`, `RERUN_TASK`, `CLOSE_TASK`, `CANCEL_TASK`, `UNBLOCK_TASK`

**`web/src/lib/graphql/repo-queries.ts`** (~160 lines):
`GET_REPOSITORIES`, `REGISTER_REPOSITORY`, `RETRY_CLONE`, `REMOVE_REPOSITORY`, `GITHUB_AUTH_STATUS`, `GITHUB_REPOSITORIES`, `GITHUB_BRANCHES`, `GITHUB_LOGIN_START`, `GITHUB_LOGIN_POLL`, `GITHUB_LOGOUT`, `CLAUDE_AUTH_STATUS`, `CLAUDE_LOGIN_START`, `CLAUDE_LOGIN_POLL`, `CLAUDE_SUBMIT_CODE`, `CLAUDE_LOGOUT`

**`web/src/lib/graphql/agent-queries.ts`** (~120 lines):
`GET_AGENT_INSTANCES`, `GET_GLOBAL_AGENTS`, `SET_AGENT_DISABLED`, `MODIFY_AGENT`, `RESET_AGENT_MODIFICATION`, `CREATE_AGENT_PR`, `GET_PIPELINE_DEFINITIONS`

**`web/src/lib/graphql/queries.ts`** reduces to ~30 lines — re-exports everything from the three new files plus the dashboard constants it keeps directly:
```typescript
export * from './task-queries'
export * from './repo-queries'
export * from './agent-queries'
export const DASHBOARD_STATS = gql`...`
export const TOKEN_USAGE_BY_MODEL = gql`...`
```

This preserves all existing consumer imports (`@/lib/graphql/queries`) with zero changes to 10+ consumer files.

---

### Layer H: Web — task detail page component extraction

#### Step 10: `web/src/app/tasks/[id]/page.tsx` (1,146 → ~350 lines)

**`web/src/app/tasks/[id]/types.ts`** (new — ~60 lines):
Extract interfaces: `Stage`, `ContextEntry`, `Task`

**`web/src/components/tasks/StructuredOutputDisplay.tsx`** (new — ~140 lines):
Extract `StructuredOutputDisplay` component (lines 109–247 of page.tsx). Props: `{ output: Record<string, unknown> }`.

**`web/src/components/tasks/StageDuration.tsx`** (new — ~50 lines):
Extract `StageDuration` component (lines 248–292 of page.tsx). Props: `{ startedAt, completedAt, isExecuting }`.

**`web/src/components/tasks/PipelineStagesFlow.tsx`** (new — ~240 lines):
Extract `PipelineStagesFlow` component (lines 293–539 of page.tsx). Props: `{ stages, pipelineDefinitions }`. Includes the SVG flow diagram constants and rendering logic.

`page.tsx` retains only `TaskDetailPage` default export with state management, queries, mutations, and stage accordion list.

The `utils.ts` file already exists in the `[id]/` directory — no change needed.

---

## Import Dependency Graph (after refactor)

```
cli/output_parser.py   ← (no deps within package)
cli/claude.py          ← output_parser
stage_manager.py       ← database, logging, models, spending
task_queue.py          ← database, logging, models, spending (smaller)
pipeline/git_ops.py    ← utils
pipeline/agent_invoker.py ← cli.claude, registry, config, models, exceptions
pipeline/planner.py    ← task_queue, stage_manager, agent_registry, agent_invoker
pipeline/stage_runner.py ← task_queue, stage_manager, registry, agent_invoker, git_ops, context, conditions
pipeline/executor.py   ← task_queue, stage_manager, planner, stage_runner, agent_invoker, git_ops, database, models
```

No import cycles introduced.

---

## Files Changed Summary

| File | Before | After | Action |
|---|---|---|---|
| `supervisor/python/.../cli/claude.py` | 914 | ~330 | Shrink (extract output_parser) |
| `supervisor/python/.../cli/output_parser.py` | — | ~480 | Create |
| `supervisor/python/.../task_queue.py` | 1,316 | ~450 | Shrink (extract stage_manager) |
| `supervisor/python/.../stage_manager.py` | — | ~780 | Create |
| `supervisor/python/.../pipeline/executor.py` | 1,539 | ~480 | Shrink (extract planner, stage_runner, agent_invoker, git_ops) |
| `supervisor/python/.../pipeline/git_ops.py` | — | ~80 | Create |
| `supervisor/python/.../pipeline/agent_invoker.py` | — | ~220 | Create |
| `supervisor/python/.../pipeline/planner.py` | — | ~160 | Create |
| `supervisor/python/.../pipeline/stage_runner.py` | — | ~450 | Create |
| `supervisor/python/.../config_store.py` | 597 | ~120 | Shrink (extract agent_store, pipeline_store) |
| `supervisor/python/.../agent_store.py` | — | ~280 | Create |
| `supervisor/python/.../pipeline_store.py` | — | ~230 | Create |
| `supervisor/python/.../main.py` | existing | small updates | Update imports |
| `api/src/resolvers/mutations.ts` | 636 | ~60 | Shrink (assembler) |
| `api/src/resolvers/helpers.ts` | — | ~55 | Create |
| `api/src/resolvers/task-mutations.ts` | — | ~220 | Create |
| `api/src/resolvers/repo-mutations.ts` | — | ~160 | Create |
| `api/src/resolvers/agent-mutations.ts` | — | ~160 | Create |
| `api/src/resolvers/queries.ts` | 427 | ~80 | Shrink (assembler) |
| `api/src/resolvers/task-queries.ts` | — | ~120 | Create |
| `api/src/resolvers/repo-queries.ts` | — | ~80 | Create |
| `api/src/resolvers/mappers.ts` | existing | +functions | Expand (move from queries.ts) |
| `web/src/lib/graphql/queries.ts` | 543 | ~30 | Shrink (re-export wrapper) |
| `web/src/lib/graphql/task-queries.ts` | — | ~200 | Create |
| `web/src/lib/graphql/repo-queries.ts` | — | ~160 | Create |
| `web/src/lib/graphql/agent-queries.ts` | — | ~120 | Create |
| `web/src/app/tasks/[id]/page.tsx` | 1,146 | ~350 | Shrink (extract components) |
| `web/src/app/tasks/[id]/types.ts` | — | ~60 | Create |
| `web/src/components/tasks/StructuredOutputDisplay.tsx` | — | ~140 | Create |
| `web/src/components/tasks/StageDuration.tsx` | — | ~50 | Create |
| `web/src/components/tasks/PipelineStagesFlow.tsx` | — | ~240 | Create |

---

## No Database Migrations Required

This is a pure code refactoring with no schema changes.

---

## Test Strategy

- After each step: run `cd supervisor/python && python -m pytest tests/ -v` for Python steps
- After each step: run `cd api && npm test` for API steps  
- After each step: run `cd web && npm test` for web steps
- No new test files required (existing tests cover the extracted code)
- If a test import breaks (e.g. `from ..task_queue import TaskQueue` for a method now on `StageManager`), update the import path in the test file

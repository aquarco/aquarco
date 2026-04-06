# Design: Autoload .claude agents from repositories

**Task:** github-issue-aquarco-14
**Issue:** https://github.com/aquarco/aquarco/issues/14
**Date:** 2026-03-25

## 1. Overview

When a repository contains a `.claude/agents/` directory, the system should:
1. **Scan** the directory for `.md` prompt files
2. **Analyze** each prompt using Claude CLI to infer agent metadata (categories, tools, description)
3. **Generate** aquarco agent YAML definitions and categorize into pipeline categories
4. **Write** the generated definitions to `aquarco-config/agents/` and optionally `aquarco-config/pipelines.yaml` in the repo
5. **Store** the autoloaded agents in the database with source `autoload:<repo_name>`
6. **Provide** a "Reload Agents" button in the repos UI to rescan on demand

## 2. Architecture

### 2.1 New Module: `agent_autoloader.py`

A new module at `supervisor/python/src/aquarco_supervisor/agent_autoloader.py` handles the core logic:

```
scan_repo_agents(repo_path: Path) → list[DiscoveredPrompt]
    Finds all .md files in <repo_path>/.claude/agents/

analyze_agent_prompt(prompt_path: Path, claude_runner) → AgentAnalysis
    Calls Claude CLI with a specialized prompt to analyze the .md file
    and infer: name, description, categories, tools, priority, output format

generate_agent_definition(analysis: AgentAnalysis, repo_name: str) → dict
    Converts the analysis result into a full aquarco agent YAML definition
    (apiVersion, kind, metadata, spec)

write_aquarco_config(repo_path: Path, definitions: list[dict], pipelines: list[dict] | None)
    Writes definitions to <repo_path>/aquarco-config/agents/<name>.yaml
    Optionally writes pipelines to <repo_path>/aquarco-config/pipelines.yaml

autoload_repo_agents(repo_path: Path, repo_name: str, db: Database, claude_runner) → AutoloadResult
    Orchestrates the full flow: scan → analyze → generate → write → store
```

### 2.2 Data Model

#### New `source` value

Autoloaded agents use `source = 'autoload:<repo_name>'` to distinguish them from:
- `default` (built-in)
- `global:<repo_name>` (from config repo overlay)
- `repo:<repo_name>` (from `.aquarco.yaml` overlay)

#### New database table: `repo_agent_scans`

Tracks scan history per repository for status/progress:

```sql
CREATE TABLE repo_agent_scans (
    id              SERIAL PRIMARY KEY,
    repo_name       TEXT NOT NULL REFERENCES repositories(name) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','scanning','analyzing','writing','completed','failed')),
    agents_found    INT DEFAULT 0,
    agents_created  INT DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

#### New `AgentSource` enum value

Add `AUTOLOADED` to the GraphQL `AgentSource` enum.

### 2.3 Agent Analysis (Heuristic)

The implementation uses fast heuristic-based analysis rather than Claude CLI invocation (avoiding per-scan cost and latency). The `analyze_agent_prompt()` function:

1. Extracts the description from the first non-empty line of the markdown
2. Infers the category from keyword matching against agent name and content (e.g., "test", "review", "security")
3. Infers allowed tools from specific multi-word phrases in content (e.g., "bash", "write file", "edit file")
4. Falls back to conservative defaults: category=`implementation`, tools=`[Read, Grep, Glob]`

This approach is zero-cost, deterministic, and avoids the risks of prompt injection during analysis. Claude CLI analysis can be added as an optional enhancement in the future for ambiguous prompts.

### 2.4 Integration Points

#### 2.4.1 Config Overlay Integration

The existing `resolve_config()` function already supports 3 config layers. Autoloaded agents add a **4th layer** between the repo overlay and the runtime:

```
default → global_overlay → repo_overlay → autoloaded agents
```

Implementation approach: After `resolve_config()`, the executor calls `load_autoloaded_agents()` to read autoloaded agent definitions from the DB and merge them into the resolved config using the existing `merge_agents()` function with EXTEND strategy.

#### 2.4.2 Agent Registry Integration

`AgentRegistry.load()` already syncs agent definitions from YAML files to DB. Add a call to also sync autoloaded agents from `aquarco-config/agents/` if present.

#### 2.4.3 Pipeline Executor Integration

No changes to the executor itself. Autoloaded agents are resolved through the existing config overlay + agent registry path. They participate in planning and execution like any other agent.

#### 2.4.4 Supervisor Main Loop

Add an optional startup scan: when a repository reaches `clone_status = 'ready'` and has a `.claude/agents/` directory, queue an autoload scan if no prior scan exists.

### 2.5 GraphQL API Changes

#### New Query
```graphql
repoAgentScan(repoName: String!): RepoAgentScan
```

#### New Mutation
```graphql
reloadRepoAgents(repoName: String!): RepoAgentScanPayload!
```

#### New Types
```graphql
type RepoAgentScan {
  id: ID!
  repoName: String!
  status: RepoAgentScanStatus!
  agentsFound: Int!
  agentsCreated: Int!
  errorMessage: String
  startedAt: DateTime
  completedAt: DateTime
  createdAt: DateTime!
}

enum RepoAgentScanStatus {
  PENDING
  SCANNING
  ANALYZING
  WRITING
  COMPLETED
  FAILED
}

type RepoAgentScanPayload {
  scan: RepoAgentScan
  errors: [Error!]
}
```

#### Updated `AgentSource` Enum
```graphql
enum AgentSource {
  DEFAULT
  GLOBAL_CONFIG
  REPOSITORY
  AUTOLOADED
}
```

#### Updated `Repository` Type
Add field:
```graphql
hasClaudeAgents: Boolean!
lastAgentScan: RepoAgentScan
```

### 2.6 Frontend Changes

#### Repository Page (`web/src/app/repos/page.tsx`)

Add a "Reload Agents" icon button per repository row (only visible when `hasClaudeAgents` is true or scan exists). When clicked:
1. Calls `reloadRepoAgents` mutation
2. Shows a Snackbar "Agent scan started"
3. Polls `repoAgentScan` query until status is `COMPLETED` or `FAILED`
4. Shows result in Snackbar: "Found N agents, created M definitions" or error

#### Repository Agents Tab (`web/src/components/agents/RepoAgentsTab.tsx`)

Update to include `AUTOLOADED` source agents grouped under their repository. Show an "(autoloaded)" chip next to the source chip.

### 2.7 Security Considerations

1. **Path Traversal**: Only scan `.claude/agents/*.md` — no recursive directory traversal. Validate filenames match `^[a-zA-Z0-9_-]+\.md$`.
2. **Prompt Injection**: The `.md` files are analyzed by Claude but never executed as system prompts during the analysis step. The analysis prompt explicitly instructs Claude to treat the content as data to categorize, not instructions to follow.
3. **Tool Permissions**: Autoloaded agents inherit conservative defaults (Read, Grep, Glob only). Admin can override via the existing `modifyAgent` mutation.
4. **Rate Limiting**: Maximum 20 agent prompts per scan. Scans are rate-limited to 1 per repository per 5 minutes.
5. **Size Limits**: Agent prompt files larger than 50KB are skipped with a warning.

### 2.8 Assumptions

1. **Claude CLI is available**: The autoload feature requires an authenticated Claude CLI. If not available, scans fail gracefully with an error message.
2. **Repository is cloned**: Autoload only works on repos with `clone_status = 'ready'`.
3. **No automatic pipeline creation**: While Claude may suggest custom pipelines in rare cases, the default behavior is to categorize agents into existing global pipelines. Custom pipeline creation requires explicit admin approval (future enhancement).
4. **aquarco-config/ is gitignored**: The generated `aquarco-config/` directory should be added to `.gitignore` in target repos to avoid committing generated definitions.

## 3. Implementation Steps

### Step 1: Database Migration
Create `028_repo_agent_scans.sql` with the `repo_agent_scans` table and add `AUTOLOADED` to any check constraints that reference source values.

### Step 2: Pydantic Models
Add `RepoAgentScan` model and `RepoAgentScanStatus` enum to `models.py`.

### Step 3: Core Autoloader Module
Create `agent_autoloader.py` with:
- `scan_repo_agents()` — file discovery
- `analyze_agent_prompt()` — Claude CLI analysis
- `generate_agent_definition()` — YAML generation
- `write_aquarco_config()` — file output
- `autoload_repo_agents()` — orchestrator
- `load_autoloaded_agents_from_db()` — DB read helper

### Step 4: Config Store Integration
Update `config_store.py`:
- Add `store_agent_definitions()` support for `autoload:<repo>` source
- Add `read_autoloaded_agents()` to filter by source prefix `autoload:`
- Add `deactivate_autoloaded_agents()` for cleanup before re-scan

### Step 5: Config Overlay Integration
Update `config_overlay.py`:
- Add `merge_autoloaded_agents()` function
- Update `resolve_config()` to accept optional autoloaded agents parameter

### Step 6: GraphQL Schema & Resolvers
- Add new types/queries/mutations to `schema.graphql`
- Add `reloadRepoAgents` mutation resolver
- Add `repoAgentScan` query resolver
- Update `Repository` type resolver to include `hasClaudeAgents` and `lastAgentScan`
- Update agent source mapping in `mappers.ts`

### Step 7: Frontend - Repository Page
- Add "Reload Agents" button to repos table
- Add scan status polling
- Show results in Snackbar

### Step 8: Frontend - Agents Tab
- Update `RepoAgentsTab.tsx` to show autoloaded agents
- Add AUTOLOADED source chip variant

### Step 9: Supervisor Integration
- Update `main.py` to trigger initial scan on new repos with `.claude/agents/`
- Add autoloaded agents to agent registry loading path

### Step 10: Tests
- Unit tests for `agent_autoloader.py` (scan, analyze mock, generate, write)
- Integration test for the full autoload flow
- API test for `reloadRepoAgents` mutation

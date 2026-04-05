# Design: Redesign Agents Page

**Task:** [GitHub Issue #1](https://github.com/aquarco/aquarco/issues/1)
**Date:** 2026-03-25
**Status:** Design

## Overview

Redesign the agents page to distinguish agents from 3 sources (default, global config repo, repository-specific) and provide per-agent disable/modify/PR-creation capabilities.

The current agents page (`web/src/app/agents/page.tsx`) shows only runtime metrics from the `agent_instances` table. The redesigned page will show **agent definitions** grouped by source, with management capabilities.

## Architecture

### Data Flow

```
YAML files (default agents)
    ↓ sync_agent_definitions_to_db()
agent_definitions table (versioned specs)
    ↓ + agent_overrides table (user customizations)
    ↓ + repositories table (is_config_repo, source tracking)
GraphQL API (new queries/mutations)
    ↓
Next.js Agents Page (Global + Repository sections)
```

### Key Design Decisions

1. **New `agent_overrides` table** — stores per-agent user customizations (disabled state, modified spec). This is separate from `agent_definitions` which handles versioning. Overrides reference agent definitions by name and track which source they apply to.

2. **Source tracking via new `source` column on `agent_definitions`** — adds `source TEXT NOT NULL DEFAULT 'default'` to distinguish where an agent came from: `'default'`, `'global:<repo_name>'`, or `'repo:<repo_name>'`.

3. **PR creation via GitHub REST API** in the Node.js API service — reuses the existing GitHub token from `/agent-ssh/github-token` (already stored by `github-auth.ts`). Creates a branch, commits modified YAML, and opens a PR.

4. **No changes to the Python supervisor** — the supervisor's `config_overlay.py` and `agent_registry.py` remain unchanged. The API reads `agent_definitions` and `agent_overrides` directly from the DB. The supervisor already syncs definitions to DB via `config_store.py`.

### Assumption

The supervisor already calls `sync_agent_definitions_to_db()` at startup and stores all agent definitions (from default YAML files) into the `agent_definitions` table. We assume that global config repo agents and per-repo agents also get synced to this table (or will be by the time this feature ships). If not, a separate task will be needed to extend `config_store.py` to tag definitions with their source. **For this design, we add a `source` column to `agent_definitions` and update `config_store.py` to populate it.**

---

## Database Schema Changes

### Migration `019_agent_overrides_and_source.sql`

#### 1. Add `source` column to `agent_definitions`

```sql
ALTER TABLE agent_definitions
  ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'default';

COMMENT ON COLUMN agent_definitions.source IS
  'Origin of this agent definition: default, global:<repo_name>, or repo:<repo_name>';

CREATE INDEX IF NOT EXISTS idx_agent_definitions_source
  ON agent_definitions (source) WHERE is_active;
```

#### 2. Create `agent_overrides` table

```sql
CREATE TABLE IF NOT EXISTS agent_overrides (
    agent_name      TEXT        NOT NULL,
    scope           TEXT        NOT NULL DEFAULT 'global',
    is_disabled     BOOLEAN     NOT NULL DEFAULT FALSE,
    modified_spec   JSONB,
    modified_by     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (agent_name, scope)
);

COMMENT ON TABLE agent_overrides IS
  'User overrides for agent definitions: disable/enable and spec modifications';
COMMENT ON COLUMN agent_overrides.scope IS
  'Scope of override: global (applies to all) or repo:<repo_name> (per-repo)';
COMMENT ON COLUMN agent_overrides.modified_spec IS
  'Modified agent spec JSONB, null if only disabled. Merged on top of base spec.';

CREATE TRIGGER trg_agent_overrides_updated_at
  BEFORE UPDATE ON agent_overrides
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

---

## GraphQL API Changes

### New Types

```graphql
type AgentDefinition {
  name: String!
  version: String!
  description: String!
  source: AgentSource!
  sourceRepo: String
  isActive: Boolean!
  isDisabled: Boolean!
  isModified: Boolean!
  categories: [String!]!
  spec: JSON!
  modifiedSpec: JSON
  # Runtime metrics (joined from agent_instances)
  activeCount: Int!
  totalExecutions: Int!
  totalTokensUsed: Int!
  lastExecutionAt: DateTime
}

enum AgentSource {
  DEFAULT
  GLOBAL_CONFIG
  REPOSITORY
}

type AgentDefinitionPayload {
  agent: AgentDefinition
  errors: [Error!]
}

type CreatePRPayload {
  prUrl: String
  errors: [Error!]
}

type RepoAgentGroup {
  repository: Repository!
  agents: [AgentDefinition!]!
}
```

### New Queries

```graphql
type Query {
  # ... existing queries ...

  # Returns all global agents (default + global config repo agents)
  # with their override state (disabled/modified)
  globalAgents: [AgentDefinition!]!

  # Returns repositories that have custom agents, with their agents
  repoAgentGroups: [RepoAgentGroup!]!
}
```

### New Mutations

```graphql
type Mutation {
  # ... existing mutations ...

  # Toggle agent disabled state
  setAgentDisabled(name: String!, scope: String!, disabled: Boolean!): AgentDefinitionPayload!

  # Save modified agent spec (persists to agent_overrides)
  modifyAgent(name: String!, scope: String!, spec: JSON!): AgentDefinitionPayload!

  # Reset agent modifications (remove override)
  resetAgentModification(name: String!, scope: String!): AgentDefinitionPayload!

  # Create PR with agent changes to config repo or specific repo
  createAgentPR(repoName: String!): CreatePRPayload!
}
```

### Query Resolution Logic

**`globalAgents` resolver:**
1. Query `agent_definitions WHERE is_active = true AND (source = 'default' OR source LIKE 'global:%')`
2. LEFT JOIN `agent_overrides` on `agent_name` where `scope = 'global'`
3. LEFT JOIN `agent_instances` on `agent_name` for runtime metrics
4. Map `source` to `AgentSource` enum
5. Return combined list sorted by source (default first) then name

**`repoAgentGroups` resolver:**
1. Query `agent_definitions WHERE is_active = true AND source LIKE 'repo:%'`
2. LEFT JOIN `agent_overrides` on `agent_name` and matching scope
3. LEFT JOIN `agent_instances` for metrics
4. Group by repository, join with `repositories` table
5. Return as `RepoAgentGroup[]`

---

## Frontend Design

### Page Layout

The agents page will have two sections separated by tabs:

```
[Agents] ← page title
[Claude: user@example.com] ← existing auth button (keep as-is)

┌──────────────────────────────────────────┐
│  [Global Agents]  [Repository Agents]    │ ← Tab bar
├──────────────────────────────────────────┤
│                                          │
│  Tab content (see below)                 │
│                                          │
└──────────────────────────────────────────┘
```

### Tab 1: Global Agents

Table with columns:
| Enabled | Agent Name | Source | Description | Active | Executions | Tokens | Actions |
|---------|-----------|--------|-------------|--------|------------|--------|---------|
| Switch  | name      | Chip   | text        | dot+N  | N          | N      | buttons |

- **Enabled**: MUI `Switch` toggle (calls `setAgentDisabled` mutation)
- **Source**: `Chip` showing "Default" (gray) or repo name (blue)
- **Actions**:
  - "Edit" `IconButton` (opens `AgentEditDialog`) — disabled for default agents
  - "Reset" `IconButton` (visible only if modified, calls `resetAgentModification`)
- **Header area**: "Create PR" `Button` (calls `createAgentPR` for the global config repo)

### Tab 2: Repository Agents

Accordion-based layout, one per repository:

```
▼ my-app (3 agents)
  ┌─────────────────────────────────────────┐
  │ Enabled │ Agent Name │ Description │ ... │
  │ Switch  │ custom-agent │ ...       │ ... │
  └─────────────────────────────────────────┘
  [Create PR to my-app]
```

- Each repository is a collapsible `Accordion`
- Inside: same table structure as Global tab
- "Create PR" button per repository

### AgentEditDialog Component

Dialog for modifying an agent's spec:

```
┌─────────────────────────────────────┐
│ Edit Agent: analyze-agent           │
├─────────────────────────────────────┤
│                                     │
│ [JSON Editor / Form Fields]         │
│                                     │
│ Resources:                          │
│   Timeout (min): [30]               │
│   Max Turns: [30]                   │
│   Max Cost ($): [5.00]              │
│   Max Concurrent: [2]              │
│                                     │
│ Tools:                              │
│   Allowed: [multi-select]           │
│   Denied: [multi-select]            │
│                                     │
│ Categories: [chip input]            │
│                                     │
│            [Cancel]  [Save]         │
└─────────────────────────────────────┘
```

Uses form fields for the most common spec properties. For advanced editing, a "Raw JSON" toggle shows a `TextField` multiline with the full spec.

---

## File-by-File Changes

### New Files

| File | Purpose |
|------|---------|
| `db/migrations/019_agent_overrides_and_source.sql` | New migration |
| `web/src/components/agents/GlobalAgentsTab.tsx` | Global agents tab content |
| `web/src/components/agents/RepoAgentsTab.tsx` | Repository agents tab content |
| `web/src/components/agents/AgentEditDialog.tsx` | Agent spec editor dialog |
| `web/src/components/agents/AgentTable.tsx` | Shared agent table component |
| `api/src/github-api.ts` | GitHub API helper for creating branches, commits, PRs |

### Modified Files

| File | Changes |
|------|---------|
| `api/src/schema.graphql` | Add new types, queries, mutations |
| `api/src/resolvers/queries.ts` | Add `globalAgents` and `repoAgentGroups` resolvers |
| `api/src/resolvers/mutations.ts` | Add agent override and PR mutations |
| `api/src/generated/types.ts` | Regenerated from schema |
| `web/src/lib/graphql/queries.ts` | Add new GQL query and mutation definitions |
| `web/src/app/agents/page.tsx` | Complete rewrite with tabs |
| `supervisor/python/src/aquarco_supervisor/config_store.py` | Add `source` parameter to `store_agent_definitions()` |

---

## PR Creation Flow

When the user clicks "Create PR":

1. **API mutation `createAgentPR(repoName)`** is called
2. Read GitHub token from `/agent-ssh/github-token`
3. Query all `agent_overrides` where scope matches the repo
4. For each modified agent, read base spec from `agent_definitions`, merge with `modified_spec`
5. Determine the repo's `url` and default branch from `repositories` table
6. Use GitHub REST API:
   a. Create a new branch `aquarco/agent-changes-<timestamp>` from default branch
   b. For each modified agent, create/update the agent YAML file via Contents API
   c. Create a PR with title "Update agent definitions" and body listing changes
7. Return the PR URL

This approach uses the GitHub Contents API (file create/update) rather than the Git Data API (trees/blobs), which is simpler for small file changes.

---

## Supervisor Changes (Minimal)

Only `config_store.py` needs a small change:

```python
# In store_agent_definitions(), add source parameter:
async def store_agent_definitions(
    db: Database,
    definitions: list[dict[str, Any]],
    source: str = "default",  # NEW parameter
) -> None:
    # ... existing upsert logic ...
    # Add source to the INSERT/UPDATE query
```

The existing `sync_agent_definitions_to_db()` passes `source="default"`.
When global config repo agents are synced, pass `source=f"global:{repo_name}"`.
When per-repo agents are synced, pass `source=f"repo:{repo_name}"`.

---

## Testing Strategy

- **Unit tests**: Resolver logic for `globalAgents`, `repoAgentGroups`, override mutations
- **Integration tests**: Migration applies cleanly, overrides persist correctly
- **E2E tests**: Tab navigation, disable/enable toggle, edit dialog open/save
- **Manual verification**: PR creation (requires GitHub auth)

# Fix PR Review Issues: Agents Page

## Context
PR #18 (redesigned agents page) was merged with 9 unresolved review findings — 3 errors, 6 warnings. None have been addressed. This plan fixes all 9.

## Files to modify
- `api/src/resolvers/mutations.ts` — issues 1, 2, 8
- `api/src/resolvers/queries.ts` — issues 7, 8
- `api/src/github-api.ts` — issues 4, 5
- `web/src/components/agents/AgentEditDialog.tsx` — issue 6
- `db/migrations/022_fix_agent_overrides_schema.sql` (new) — issues 3, 9

## Existing resources to reuse
- `config/schemas/agent-definition-v1.json` — spec schema with allowed keys: `categories, priority, promptFile, tools, resources, environment, output, outputSchema, healthCheck, conditions`. Required: `categories, promptFile, output`.
- `mapAgentDefinition()` in `queries.ts:~284` — already shared mapper
- `agentErrorPayload()` in `mutations.ts:~55` — existing error helper

---

## Step 1: Scope validation (ERROR — Issue 1)

**File**: `api/src/resolvers/mutations.ts`

Add near top (after `agentErrorPayload`):
```ts
const SCOPE_PATTERN = /^(global|repo:[a-zA-Z0-9._-]+)$/
function validateScope(scope: string): string | null {
  if (!SCOPE_PATTERN.test(scope)) return `Invalid scope "${scope}". Must be "global" or "repo:<name>".`
  return null
}
```

Add at the start of `try` in `setAgentDisabled`, `modifyAgent`, `resetAgentModification`:
```ts
const scopeErr = validateScope(args.scope)
if (scopeErr) return agentErrorPayload('scope', scopeErr)
```

## Step 2: Spec validation (ERROR — Issue 2)

**File**: `api/src/resolvers/mutations.ts`

Add helper (no new dependency — keys derived from existing JSON schema):
```ts
const VALID_SPEC_KEYS = new Set([
  'categories','priority','promptFile','tools','resources',
  'environment','output','outputSchema','healthCheck','conditions',
])
const REQUIRED_SPEC_KEYS = ['categories', 'promptFile', 'output']
const MAX_SPEC_SIZE = 100 * 1024

function validateSpec(spec: unknown): string | null {
  if (typeof spec !== 'object' || spec === null || Array.isArray(spec)) return 'Spec must be a JSON object'
  if (JSON.stringify(spec).length > MAX_SPEC_SIZE) return 'Spec exceeds 100KB size limit'
  const keys = Object.keys(spec)
  for (const k of REQUIRED_SPEC_KEYS) { if (!keys.includes(k)) return `Spec missing required key "${k}"` }
  for (const k of keys) { if (!VALID_SPEC_KEYS.has(k)) return `Spec contains unknown key "${k}"` }
  return null
}
```

Call in `modifyAgent` after scope validation, before upsert:
```ts
const specErr = validateSpec(args.spec)
if (specErr) return agentErrorPayload('spec', specErr)
```

## Step 3: Extract shared agent query helper (WARNING — Issue 8)

**File**: `api/src/resolvers/queries.ts`

Export a new function (alongside `mapAgentDefinition`):
```ts
import { Pool } from 'pg'

export async function fetchAgentWithOverrides(
  pool: Pool, name: string, scope: string
): Promise<Record<string, unknown> | null> {
  const result = await pool.query<Record<string, unknown>>(
    `SELECT ad.name, ad.version, ad.description, ad.spec, ad.source,
       COALESCE(ao.is_disabled, false) AS is_disabled, ao.modified_spec,
       COALESCE(ai.active_count, 0) AS active_count,
       COALESCE(ai.total_executions, 0) AS total_executions,
       COALESCE(ai.total_tokens_used, 0) AS total_tokens_used,
       ai.last_execution_at
     FROM agent_definitions ad
     LEFT JOIN agent_overrides ao ON ao.agent_name = ad.name AND ao.scope = $2
     LEFT JOIN agent_instances ai ON ai.agent_name = ad.name
     WHERE ad.name = $1 AND ad.is_active = true LIMIT 1`,
    [name, scope]
  )
  return result.rows[0] ?? null
}
```

**File**: `api/src/resolvers/mutations.ts`

Import `fetchAgentWithOverrides` and replace inline queries in all 3 mutations:
```ts
const row = await fetchAgentWithOverrides(ctx.pool, args.name, args.scope)
if (!row) return agentErrorPayload('name', `Agent "${args.name}" not found`)
return { agent: mapAgentDefinition(row), errors: [] }
```

For `resetAgentModification`: after DELETE, call `fetchAgentWithOverrides` — the LEFT JOIN naturally returns nulls for the deleted override, producing identical results to the current hardcoded `false`/`NULL`.

## Step 4: Migration search_path fix + orphan cleanup (ERROR — Issue 3, WARNING — Issue 9)

**New file**: `db/migrations/022_fix_agent_overrides_schema.sql`

```sql
-- Fix: migration 019 omitted SET search_path. Re-run idempotently in correct schema.
-- Also: add orphan cleanup and documentation for agent_overrides FK design.

SET search_path TO aquarco, public;

-- Re-create table idempotently (already exists if 019 ran in correct schema)
CREATE TABLE IF NOT EXISTS agent_overrides (
    agent_name   TEXT NOT NULL,
    scope        TEXT NOT NULL DEFAULT 'global',
    is_disabled  BOOLEAN NOT NULL DEFAULT FALSE,
    modified_spec JSONB,
    modified_by  TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at   TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (agent_name, scope)
);

-- Ensure source column exists on agent_definitions
ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS idx_agent_definitions_source ON agent_definitions (source) WHERE is_active;

-- Clean up orphan overrides
DELETE FROM agent_overrides
WHERE agent_name NOT IN (SELECT name FROM agent_definitions WHERE is_active = true);

-- Document FK design decision
COMMENT ON TABLE agent_overrides IS
  'Overrides reference agent_definitions by name (not version). '
  'FK not possible: PK is (name, version) and partial unique index cannot serve as FK target. '
  'Orphans are cleaned up by periodic application-level queries.';
```

## Step 5: Fetch default_branch from GitHub API (WARNING — Issue 5)

**File**: `api/src/github-api.ts`

Replace lines 113-135 (the main/master guessing) with:
```ts
const repoMetaRes = await githubFetch(
  `https://api.github.com/repos/${owner}/${repoSlug}`, token
)
if (!repoMetaRes.ok) throw new Error(`Cannot access repository ${owner}/${repoSlug}`)
const repoMeta = (await repoMetaRes.json()) as { default_branch: string }
const baseBranch = repoMeta.default_branch

const refRes = await githubFetch(
  `https://api.github.com/repos/${owner}/${repoSlug}/git/ref/heads/${baseBranch}`, token
)
if (!refRes.ok) throw new Error(`Cannot find branch "${baseBranch}"`)
const refData = (await refRes.json()) as { object: { sha: string } }
const baseSha = refData.object.sha
```

## Step 6: Branch cleanup on partial failure (WARNING — Issue 4)

**File**: `api/src/github-api.ts`

After branch creation succeeds (~line 153), wrap the file-update loop + PR creation in try/catch:
```ts
try {
  // ... file updates + PR creation (existing code) ...
} catch (err) {
  // Best-effort cleanup: delete the orphaned branch
  try {
    await githubFetch(
      `https://api.github.com/repos/${owner}/${repoSlug}/git/refs/heads/${branchName}`,
      token, { method: 'DELETE' }
    )
  } catch { /* ignore cleanup failure */ }
  throw err
}
```

## Step 7: Simplify CONCAT/SUBSTRING (WARNING — Issue 7)

**File**: `api/src/resolvers/queries.ts`, line 153

Replace:
```sql
ao.scope = CONCAT('repo:', SUBSTRING(ad.source FROM 6))
```
With:
```sql
ao.scope = ad.source
```

The WHERE clause already filters `ad.source LIKE 'repo:%'` — same format as override scopes.

## Step 8: Fix AgentEditDialog defaultValue (WARNING — Issue 6)

**File**: `web/src/components/agents/AgentEditDialog.tsx`

Add helper inside the component:
```ts
function getResource(key: string): string | number {
  try {
    const parsed = JSON.parse(specText)
    return parsed?.resources?.[key] ?? ''
  } catch { return '' }
}
```

Replace all 4 `defaultValue` with controlled `value`:
```tsx
value={getResource('timeoutMinutes')}   // was defaultValue={...}
value={getResource('maxTurns')}
value={getResource('maxCost')}
value={getResource('maxConcurrent')}
```

## Verification

1. **Tests**: Run `cd api && npm test` — existing agent mutation tests should pass
2. **Type check**: `cd api && npx tsc --noEmit` — verify no TS errors
3. **Migration**: Manually verify `022_fix_agent_overrides_schema.sql` is idempotent by reading it
4. **Frontend**: `cd web && npm run build` — verify no build errors
5. **Manual check**: Confirm all 9 issues from the review are addressed

# Design: Show Token Count Alongside Cost (Issue #82)

## Summary

Display the total token count (sum of `tokens_input + tokens_output + cache_read_tokens + cache_write_tokens`) wherever `cost_usd` is shown in the UI. Three locations require changes. One SQL bug in `dashboardStats` must also be fixed (cache tokens were not included in `totalTokensToday`).

---

## Locations Changed

### 1. Dashboard — "Cost Today" stat card (`web/src/app/page.tsx`)
A new "Tokens Today" stat card will be added to the `statCards` array, using the existing `StatCard` component and `formatTokens()`. The `DASHBOARD_STATS` query already fetches `totalTokensToday` — no query change needed. The SQL bug (missing cache tokens) is fixed in the API resolver.

### 2. Task list — "Cost" column (`web/src/app/tasks/page.tsx`)
A `totalTokens: Int` field is added to the `Task` GraphQL type and resolved by a new field resolver. `GET_TASKS` query gains `totalTokens`. The Cost cell renders `formatTokens(task.totalTokens)` as a muted caption below the cost string.

### 3. Stage accordion summary bar (`web/src/app/tasks/[id]/page.tsx`, lines 730–755)
Per-stage token total is computed inline from the already-fetched `tokensInput`, `tokensOutput`, `cacheReadTokens`, `cacheWriteTokens` stage fields (no new query needed). It is shown as a caption next to `stageCost` in the `AccordionSummary` right-side stack.

---

## Backend Changes

### `api/src/schema.graphql`
Add `totalTokens: Int` to the `Task` type (nullable, returns `null` when 0 — consistent with `totalCostUsd`):

```graphql
type Task {
  ...existing fields...
  totalCostUsd: Float
  totalTokens: Int        # sum of all four token columns across the task's stages; null if no stages recorded
  stages: [Stage!]!
  ...
}
```

No change to `DashboardStats` — `totalTokensToday: Int!` already exists in the schema.

### `api/src/resolvers/queries.ts` — Fix `dashboardStats` SQL
The `tokens` query currently uses:
```sql
SELECT COALESCE(SUM(tokens_input + tokens_output), 0) AS total FROM stages WHERE started_at >= CURRENT_DATE
```
Fix to:
```sql
SELECT COALESCE(SUM(
  COALESCE(tokens_input, 0) + COALESCE(tokens_output, 0) +
  COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0)
), 0) AS total FROM stages WHERE started_at >= CURRENT_DATE
```
Per-column `COALESCE(..., 0)` guards against `NULL` in any individual column (all four are nullable in the DB as seen in schema.graphql).

### `api/src/resolvers/types.ts` — Add `Task.totalTokens` field resolver
Follow the exact pattern of `Task.totalCostUsd`:

```typescript
async totalTokens(
  parent: { id: string },
  _: unknown,
  ctx: Context
): Promise<number | null> {
  const result = await ctx.pool.query<{ total: string }>(
    `SELECT COALESCE(SUM(
       COALESCE(tokens_input, 0) + COALESCE(tokens_output, 0) +
       COALESCE(cache_read_tokens, 0) + COALESCE(cache_write_tokens, 0)
     ), 0) AS total FROM stages WHERE task_id = $1`,
    [parent.id]
  )
  const val = parseInt(result.rows[0].total, 10)
  return val > 0 ? val : null
},
```

> **Known risk (N+1)**: This issues one query per task on the task list, exactly as `totalCostUsd` does. This is the existing pattern; fixing it with a DataLoader is out of scope for this issue.

### `api/src/generated/types.ts` — Regenerate
Run `cd api && npm run codegen` after schema change to regenerate types. The `Task` interface gains `totalTokens?: Maybe<Scalars['Int']>`.

---

## Frontend Changes

### `web/src/lib/spending.ts` — Guard `formatTokens` against null/zero
Update signature to handle `number | null | undefined`:

```typescript
export function formatTokens(count: number | null | undefined): string {
  if (count == null || count === 0) return '—'
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}k`
  return String(count)
}
```

This makes `formatTokens` consistent with `formatCost` — both return `'—'` for absent/zero values.

### `web/src/lib/graphql/queries.ts` — Add `totalTokens` to `GET_TASKS`
```graphql
nodes {
  ...existing fields...
  totalCostUsd
  totalTokens    # add this line
}
```
`DASHBOARD_STATS` already has `totalTokensToday` — no change needed.

### `web/src/app/page.tsx` — Add "Tokens Today" stat card
Add a new entry in `statCards` after "Cost Today":
```typescript
{
  label: 'Tokens Today',
  value: stats?.totalTokensToday != null ? formatTokens(stats.totalTokensToday) : '—',
  color: '#757575',
},
```
Import `formatTokens` at the top alongside `formatCost`.

Update `TaskRow` interface to include `totalTokens?: number | null`.

### `web/src/app/tasks/page.tsx` — Token count in Cost column
Update `Task` interface:
```typescript
interface Task {
  ...
  totalCostUsd?: number | null
  totalTokens?: number | null
}
```
Import `formatTokens` alongside `formatCost`. Update the Cost cell:
```tsx
<TableCell>
  <Typography variant="body2" color="warning.main" sx={{ ...monoStyle, fontSize: '0.8rem' }}>
    {formatCost(task.totalCostUsd)}
  </Typography>
  {task.totalTokens != null && task.totalTokens > 0 && (
    <Typography variant="caption" color="text.secondary" sx={monoStyle}>
      {formatTokens(task.totalTokens)}
    </Typography>
  )}
</TableCell>
```
Update skeleton `[...Array(7)]` — column count stays at 7, no header change needed since we're adding data within the existing Cost cell.

### `web/src/app/tasks/[id]/page.tsx` — Per-stage token total in AccordionSummary
In the stage accordion loop (~line 715), compute the stage token total inline:
```typescript
const stageCost = stage.costUsd
const stageTotalTokens =
  (stage.tokensInput ?? 0) +
  (stage.tokensOutput ?? 0) +
  (stage.cacheReadTokens ?? 0) +
  (stage.cacheWriteTokens ?? 0)
```
In the right-side `Stack` (lines 743–753), add the token caption after the cost:
```tsx
{stageTotalTokens > 0 && (
  <Typography variant="caption" color="text.secondary" sx={monoStyle}>
    {formatTokens(stageTotalTokens)}
  </Typography>
)}
```
`formatTokens` is already imported in this file (used in the token stats bar at ~line 770).

---

## Assumptions

1. `monoStyle` is already imported in `tasks/[id]/page.tsx` — confirm before using it in the stage row.
2. The codegen command is `npm run codegen` inside the `api/` directory (as used in other design docs).
3. No database migration is required — all four token columns already exist in the `stages` table.
4. The `formatTokens` null-guard change is backward-compatible: existing callers in `tasks/[id]/page.tsx` guard with `!= null && > 0` before calling, so they are unaffected.

---

## Out of Scope

- Task detail spending row (lines 583–634) — already shows full token breakdown.
- Stage accordion token stats bar (lines 759–791) — already shows per-token-type breakdown.
- DataLoader optimization for `Task.totalTokens` N+1.

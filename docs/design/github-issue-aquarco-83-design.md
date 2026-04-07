# Design: Token Usage Chart on Dashboard (Issue #83)

## Summary

Add a stacked bar chart to the Dashboard page that shows daily token consumption
broken down by model (e.g. `claude-sonnet-4-6`, `claude-opus-4-6`). The chart
displays four token buckets — Input, Output, Cache Read, Cache Write — per model
per day. Requires a DB migration adding a `model` column to `stages`, a Python
backfill script, supervisor write-path changes, a new GraphQL query, and a
recharts-powered frontend component.

---

## Architecture

### Data Flow

```
stages.raw_output (NDJSON)
        │
        ▼
spending.py::parse_ndjson_spending()   ← already extracts summary.model
        │
        ▼
task_queue.py::complete_stage()        ← add model to all 3 SQL UPDATE/INSERT paths
        │
        ▼
stages.model (new VARCHAR column)
        │
        ▼
GraphQL tokenUsageByModel(days: Int)   ← GROUP BY date_trunc('day'), model
        │
        ▼
TokenUsageChart.tsx (recharts)         ← stacked bar, series per model
```

---

## Step-by-Step Design

### Step 1 — Database Migration (`040_add_stage_model`)

File: `db/migrations/040_add_stage_model.sql`

```sql
-- depends: 039_add_stage_msg_spending_state
-- Migration 040: Add model column to stages
SET search_path TO aquarco, public;

ALTER TABLE stages
  ADD COLUMN IF NOT EXISTS model VARCHAR(100);

COMMENT ON COLUMN stages.model IS 'Claude model used for this stage (e.g. claude-sonnet-4-6). Populated by supervisor from raw_output NDJSON.';
```

Rollback: `db/migrations/040_add_stage_model.rollback.sql`

```sql
SET search_path TO aquarco, public;
ALTER TABLE stages DROP COLUMN IF EXISTS model;
```

No index needed at this time — the chart query filters by `started_at` range
and groups/sorts by date; model cardinality is low (< 10 values).

### Step 2 — Backfill Script (`db/scripts/backfill_stage_model.py`)

A standalone Python script (not part of the migration itself) that reads
`raw_output` for existing completed stages where `model IS NULL` and populates
the column using the existing `parse_ndjson_spending()` function.

```python
#!/usr/bin/env python3
"""Backfill stages.model from raw_output NDJSON.

Run once after migration 040 is applied:
  python db/scripts/backfill_stage_model.py postgresql://...
"""
import sys
import asyncio
import psycopg
from aquarco_supervisor.spending import parse_ndjson_spending

async def main(dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        rows = await conn.execute(
            "SELECT id, raw_output FROM aquarco.stages "
            "WHERE model IS NULL AND raw_output IS NOT NULL "
            "ORDER BY id"
        )
        updated = 0
        async for row_id, raw_output in rows:
            summary = parse_ndjson_spending(raw_output)
            if summary.model:
                await conn.execute(
                    "UPDATE aquarco.stages SET model = %s WHERE id = %s",
                    (summary.model, row_id)
                )
                updated += 1
        await conn.commit()
        print(f"Backfilled {updated} rows.")

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
```

This script is safe to re-run (`WHERE model IS NULL` guard). Expected runtime:
linear in the number of stages with raw_output; will be slow on large datasets
but is a one-time operation.

### Step 3 — Supervisor: Persist Model in task_queue.py

File: `supervisor/python/src/aquarco_supervisor/task_queue.py`

The `complete_stage()` method already extracts spending fields from the output
dict. Model is available on `SpendingSummary.model` — but the spending parser
is called elsewhere. The simplest approach: extract `model` from the
`_agent_name` prefix in structured output, or better, parse it directly from
the raw_output using the existing `parse_ndjson_spending()`.

**Exact change (near line ~373 in `complete_stage()`):**

After the existing `_pop_cumulative(...)` block that extracts cost/token fields,
add:

```python
# Extract model from raw_output NDJSON.
# parse_ndjson_spending is already imported for live output parsing.
from .spending import parse_ndjson_spending  # move to module top-level if not already

model: str | None = None
if raw_output:
    spending = parse_ndjson_spending(raw_output)
    model = spending.model or None
```

Then add `model` to `spending_params`:

```python
spending_params = {
    "cost_usd": cost_usd,
    "tokens_in": tokens_input,
    "tokens_out": tokens_output,
    "cache_read": cache_read,
    "cache_write": cache_write,
    "model": model,
}
```

All three SQL paths (UPDATE by stage_id, UPDATE by stage_key, INSERT/UPSERT
by stage_number) must include `model = %(model)s` in their SET clause:

```sql
-- add to all three paths:
model = %(model)s,
```

For the INSERT path, add `model` to both the column list and VALUES:
```sql
INSERT INTO stages (task_id, stage_number, category, agent, status,
                   ..., model, ...)
VALUES (..., %(model)s, ...)
ON CONFLICT ... DO UPDATE
SET ..., model = %(model)s, ...
```

> **Note:** `parse_ndjson_spending` is already used in `update_stage_live_output`.
> Move the import to the module top level if it is currently inside a function.

### Step 4 — GraphQL Schema Changes (`api/src/schema.graphql`)

#### 4a. Add `model` to `Stage` type

```graphql
type Stage {
  # ... existing fields ...
  cacheWriteTokens: Int
  errorMessage: String
  retryCount: Int!
  liveOutput: String
  model: String          # ← ADD THIS
}
```

#### 4b. Add new query to `Query` type

```graphql
type Query {
  # ... existing queries ...
  tokenUsageByModel(days: Int): [TokenUsageByDay!]!
}
```

#### 4c. Add new output types

```graphql
type TokenUsageByDay {
  day: DateTime!
  model: String!
  tokensInput: Int!
  tokensOutput: Int!
  cacheReadTokens: Int!
  cacheWriteTokens: Int!
}
```

### Step 5 — GraphQL Resolver (`api/src/resolvers/queries.ts`)

#### 5a. Update `mapStage()` to include model

In the existing `mapStage()` function (currently ends at line 376):

```typescript
export function mapStage(row: Record<string, unknown>) {
  return {
    // ... existing fields ...
    cacheWriteTokens: row.cache_write_tokens ?? null,
    errorMessage: row.error_message ?? null,
    retryCount: row.retry_count,
    liveOutput: row.live_output ?? null,
    model: row.model ?? null,   // ← ADD THIS
  }
}
```

#### 5b. Add `tokenUsageByModel` resolver to `Query` object

```typescript
async tokenUsageByModel(
  _: unknown,
  args: { days?: number | null },
  ctx: Context
) {
  const days = args.days ?? 30
  const result = await ctx.pool.query<{
    day: Date
    model: string
    tokens_input: string
    tokens_output: string
    cache_read_tokens: string
    cache_write_tokens: string
  }>(`
    SELECT
      DATE_TRUNC('day', started_at AT TIME ZONE 'UTC') AS day,
      COALESCE(model, 'unknown') AS model,
      COALESCE(SUM(tokens_input), 0)       AS tokens_input,
      COALESCE(SUM(tokens_output), 0)      AS tokens_output,
      COALESCE(SUM(cache_read_tokens), 0)  AS cache_read_tokens,
      COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens
    FROM stages
    WHERE started_at >= NOW() - ($1 || ' days')::INTERVAL
    GROUP BY 1, 2
    ORDER BY 1 ASC, 2 ASC
  `, [days])
  return result.rows.map((r) => ({
    day: r.day,
    model: r.model,
    tokensInput: parseInt(r.tokens_input, 10),
    tokensOutput: parseInt(r.tokens_output, 10),
    cacheReadTokens: parseInt(r.cache_read_tokens, 10),
    cacheWriteTokens: parseInt(r.cache_write_tokens, 10),
  }))
},
```

### Step 6 — Update Generated Types (`api/src/generated/types.ts`)

After schema changes, run codegen:

```bash
cd api && npm run codegen
```

This regenerates `types.ts` automatically. If `codegen` script does not exist,
manually add the `TokenUsageByDay` type and the new `tokenUsageByModel` query
resolver type to the generated file following the existing pattern (interface
sorted alphabetically, resolver signature matching the schema).

**Manual additions if codegen is absent:**

```typescript
export type TokenUsageByDay = {
  __typename?: 'TokenUsageByDay';
  cacheReadTokens: Scalars['Int']['output'];
  cacheWriteTokens: Scalars['Int']['output'];
  day: Scalars['DateTime']['output'];
  model: Scalars['String']['output'];
  tokensInput: Scalars['Int']['output'];
  tokensOutput: Scalars['Int']['output'];
};
```

And add `model?: Maybe<Scalars['String']['output']>` to the `Stage` type.

### Step 7 — Frontend: Add recharts Dependency

File: `web/package.json`

Add to `"dependencies"`:
```json
"recharts": "^2.13.0"
```

Install with `npm install recharts` inside the `web/` directory.

### Step 8 — Frontend: GraphQL Query (`web/src/lib/graphql/queries.ts`)

Add after the `DASHBOARD_STATS` query:

```typescript
export const TOKEN_USAGE_BY_MODEL = gql`
  query TokenUsageByModel($days: Int) {
    tokenUsageByModel(days: $days) {
      day
      model
      tokensInput
      tokensOutput
      cacheReadTokens
      cacheWriteTokens
    }
  }
`
```

### Step 9 — Frontend: TokenUsageChart Component

New file: `web/src/components/dashboard/TokenUsageChart.tsx`

The component receives raw `TokenUsageByDay[]` data, pivots it into recharts
`BarChart` format, and renders a stacked bar chart.

**Data transformation:**
- Group rows by `day`, producing one entry per day with sub-keys per model.
- Each model gets a `Bar` with a distinct color.

**Color palette** (consistent, 6 colors for 6 possible model variants):
```
claude-opus-*    → #7c3aed  (purple)
claude-sonnet-*  → #1976d2  (blue)
claude-haiku-*   → #2e7d32  (green)
unknown          → #757575  (grey)
```

**Props:**

```typescript
interface TokenUsageByDay {
  day: string
  model: string
  tokensInput: number
  tokensOutput: number
  cacheReadTokens: number
  cacheWriteTokens: number
}

interface TokenUsageChartProps {
  data: TokenUsageByDay[]
  loading: boolean
  tokenType?: 'input' | 'output' | 'cacheRead' | 'cacheWrite' | 'total'
}
```

**Default `tokenType`:** `'total'` (sum of all four buckets per model per day).

The component:
1. Accepts a `tokenType` selector (MUI `ToggleButtonGroup` or `Select`) to let
   the user switch between token buckets.
2. Renders a `ResponsiveContainer` wrapping a `BarChart`.
3. One `Bar` per unique model, each stacked (`stackId="tokens"`).
4. X axis formatted as `MMM D` (e.g. "Apr 7").
5. Y axis uses `formatTokens()` from `web/src/lib/spending.ts`.
6. Tooltip shows model name + raw token count.
7. Legend at bottom.
8. If `loading`, renders MUI `Skeleton` matching the chart height (200px).

**Skeleton approach:**
```tsx
{loading ? (
  <Skeleton variant="rectangular" height={200} />
) : (
  <ResponsiveContainer width="100%" height={200}>
    <BarChart data={chartData}>...
```

### Step 10 — Dashboard Page (`web/src/app/page.tsx`)

Import and integrate `TokenUsageChart`:

```typescript
import { TokenUsageChart } from '@/components/dashboard/TokenUsageChart'
import { TOKEN_USAGE_BY_MODEL } from '@/lib/graphql/queries'
```

Add query in component body:

```typescript
const {
  data: tokenData,
  loading: tokenLoading,
} = useQuery(TOKEN_USAGE_BY_MODEL, { variables: { days: 30 } })
```

Add chart section after the existing "Tasks by Pipeline / Tasks by Repository"
grid, before "Recent Tasks":

```tsx
{/* Token Usage Chart */}
<Card variant="outlined" sx={{ mb: 3 }}>
  <CardContent>
    <Typography variant="subtitle1" fontWeight={700} gutterBottom>
      Token Usage by Model (Last 30 Days)
    </Typography>
    <Divider sx={{ mb: 2 }} />
    <TokenUsageChart
      data={tokenData?.tokenUsageByModel ?? []}
      loading={tokenLoading}
    />
  </CardContent>
</Card>
```

---

## Key Assumptions

1. **recharts v2** is used (current stable with React 18 support). If the team
   prefers a different chart library, the component API changes but the data
   model and query remain identical.

2. **No codegen script** — `api/src/generated/types.ts` is manually updated
   following the existing alphabetical pattern, because there is no `codegen`
   npm script in `api/package.json` at time of writing. The implementation
   agent should verify and run codegen if available.

3. **Backfill is a one-time manual step**, not run automatically during
   migration. The `db/scripts/backfill_stage_model.py` script must be run by
   an operator after deploying migration 040.

4. **NULL model → `'unknown'`** coalesced in SQL so existing data renders
   correctly without requiring a complete backfill before launch.

5. **No database index** on `stages.model` for now. The chart query groups by
   `date_trunc('day', started_at)` and `model`; the existing index on
   `started_at` (if present) is sufficient. If query performance is an issue,
   a composite index `(started_at, model)` can be added later.

---

## Files Changed

| File | Change |
|------|--------|
| `db/migrations/040_add_stage_model.sql` | New — add `model` column |
| `db/migrations/040_add_stage_model.rollback.sql` | New — drop `model` column |
| `db/scripts/backfill_stage_model.py` | New — backfill model from raw_output |
| `supervisor/python/src/aquarco_supervisor/task_queue.py` | Add model extraction + persist in all 3 SQL paths |
| `api/src/schema.graphql` | Add `model` to Stage, add `tokenUsageByModel` query + `TokenUsageByDay` type |
| `api/src/resolvers/queries.ts` | Add `tokenUsageByModel` resolver + update `mapStage()` |
| `api/src/generated/types.ts` | Add `TokenUsageByDay` type, update `Stage` type |
| `web/package.json` | Add `recharts` dependency |
| `web/src/lib/graphql/queries.ts` | Add `TOKEN_USAGE_BY_MODEL` query |
| `web/src/components/dashboard/TokenUsageChart.tsx` | New — recharts stacked bar chart |
| `web/src/app/page.tsx` | Import + render `TokenUsageChart` |

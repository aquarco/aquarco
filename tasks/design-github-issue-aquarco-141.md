# Design: Cost Spending Visualisation (Issue #141)

## Summary

Two dashboard enhancements:
1. **Token Usage Chart** — overlay a second Y-axis (right) showing daily cost in USD as a `Line` series on the existing stacked `BarChart`.
2. **Recent Tasks table** — replace the simplified 5-column table with the full Tasks-page table, minus the ID column (columns: Title, Status, Repository, Pipeline, Cost, Updated).

No database migrations are required. The only API change is adding `costUsd: Float!` to the `TokenUsageByDay` GraphQL type.

---

## Detailed Design

### 1. GraphQL Schema (`api/src/schema.graphql`)

Add `costUsd` to `TokenUsageByDay`:

```graphql
type TokenUsageByDay {
  day: DateTime!
  model: String!
  tokensInput: Int!
  tokensOutput: Int!
  cacheReadTokens: Int!
  cacheWriteTokens: Int!
  costUsd: Float!   # ← new field
}
```

### 2. Resolver (`api/src/resolvers/task-queries.ts`)

In `tokenUsageByModel`, extend the SELECT to also aggregate `cost_usd` and add it to the mapped result:

```sql
SELECT
  DATE_TRUNC('day', started_at AT TIME ZONE 'UTC') AS day,
  COALESCE(model, 'unknown') AS model,
  COALESCE(SUM(tokens_input), 0)::int AS tokens_input,
  COALESCE(SUM(tokens_output), 0)::int AS tokens_output,
  COALESCE(SUM(cache_read_tokens), 0)::int AS cache_read_tokens,
  COALESCE(SUM(cache_write_tokens), 0)::int AS cache_write_tokens,
  COALESCE(SUM(cost_usd), 0)::float AS cost_usd    -- ← new column
FROM stages
WHERE started_at >= NOW() - ($1 || ' days')::INTERVAL
GROUP BY 1, 2
ORDER BY 1 ASC, 2 ASC
```

Map the new column in the return object:

```ts
return result.rows.map((row) => ({
  day: (row.day as Date).toISOString(),
  model: row.model as string,
  tokensInput: row.tokens_input as number,
  tokensOutput: row.tokens_output as number,
  cacheReadTokens: row.cache_read_tokens as number,
  cacheWriteTokens: row.cache_write_tokens as number,
  costUsd: row.cost_usd as number,   // ← new
}))
```

### 3. GraphQL Client Query (`web/src/lib/graphql/queries.ts`)

Add `costUsd` to `TOKEN_USAGE_BY_MODEL`:

```ts
export const TOKEN_USAGE_BY_MODEL = gql`
  query TokenUsageByModel($days: Int) {
    tokenUsageByModel(days: $days) {
      day model tokensInput tokensOutput cacheReadTokens cacheWriteTokens costUsd
    }
  }
`
```

### 4. TokenUsageChart Component (`web/src/components/dashboard/TokenUsageChart.tsx`)

#### 4a. Interface extension

Add `costUsd` to `TokenUsageByDay`:

```ts
interface TokenUsageByDay {
  day: string
  model: string
  tokensInput: number
  tokensOutput: number
  cacheReadTokens: number
  cacheWriteTokens: number
  costUsd: number  // ← new
}
```

#### 4b. Recharts imports

Replace `BarChart` with `ComposedChart` and add `Line`:

```ts
import {
  ComposedChart,   // replaces BarChart
  Bar,
  Line,            // new
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
  CartesianGrid,
  PieChart,
  Pie,
  Cell,
} from 'recharts'
```

`ComposedChart` is already available in the recharts version used by this project (confirmed by existing imports from `recharts`).

#### 4c. useMemo — aggregate costUsd per day

In the existing `dayMap` loop, also accumulate `costUsd` per day key. Extend the map value type:

```ts
const dayMap = new Map<string, {
  input: number; output: number; cacheRead: number; cacheWrite: number; costUsd: number
}>()

// inside loop:
if (!dayMap.has(key)) {
  dayMap.set(key, { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0 })
}
const entry = dayMap.get(key)!
entry.input += row.tokensInput
entry.output += row.tokensOutput
entry.cacheRead += row.cacheReadTokens
entry.cacheWrite += row.cacheWriteTokens
entry.costUsd += row.costUsd          // ← new
```

In `buildFullDayRange` mapping, include `costUsd: 0` as the empty-slot default:

```ts
const chartData = buildFullDayRange(days, startDate).map((isoDate) => ({
  day: formatDay(isoDate),
  ...(dayMap.get(isoDate) ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, costUsd: 0 }),
}))
```

#### 4d. Chart JSX — switch to ComposedChart, dual Y-axis, Line

```tsx
<ComposedChart data={chartData} barSize={8}>
  <CartesianGrid strokeDasharray="3 3" />
  <XAxis dataKey="day" tick={{ fontSize: 12 }} />

  {/* Left axis — tokens (unchanged) */}
  <YAxis
    yAxisId="tokens"
    orientation="left"
    tickFormatter={(v: number) => formatTokens(v)}
    width={55}
  />

  {/* Right axis — cost USD */}
  <YAxis
    yAxisId="cost"
    orientation="right"
    tickFormatter={(v: number) => `$${v.toFixed(2)}`}
    width={60}
  />

  <Tooltip
    formatter={(value: number, name: string) => {
      if (name === 'costUsd') return [`$${value.toFixed(4)}`, 'Cost (USD)']
      return [formatTokens(value), TOKEN_LABELS[name] ?? name]
    }}
  />
  <Legend
    formatter={(name: string) =>
      name === 'costUsd' ? 'Cost (USD)' : (TOKEN_LABELS[name] ?? name)
    }
  />

  {/* Existing token bars — add yAxisId */}
  <Bar yAxisId="tokens" dataKey="input" stackId="t" fill={TOKEN_COLORS.input} name="input" />
  <Bar yAxisId="tokens" dataKey="output" stackId="t" fill={TOKEN_COLORS.output} name="output" />
  <Bar yAxisId="tokens" dataKey="cacheRead" stackId="t" fill={TOKEN_COLORS.cacheRead} name="cacheRead" />
  <Bar yAxisId="tokens" dataKey="cacheWrite" stackId="t" fill={TOKEN_COLORS.cacheWrite} name="cacheWrite" />

  {/* New cost line */}
  <Line
    yAxisId="cost"
    type="monotone"
    dataKey="costUsd"
    stroke="#f57c00"
    strokeWidth={2}
    dot={false}
    name="costUsd"
  />
</ComposedChart>
```

**Note on dual-axis scale**: Tokens are large integers (thousands–millions); costs are small floats ($0.001–$10). By using separate `yAxisId` props, each axis uses its own auto-scaled domain. No explicit `domain` override is needed unless the data is extremely sparse — the implementation agent should NOT add hard-coded domains.

### 5. Dashboard Page (`web/src/app/page.tsx`)

#### 5a. Extend `TaskRow` interface

Replace:
```ts
interface TaskRow {
  id: string
  title: string
  status: string
  pipeline: string
  repository: { name: string }
  createdAt: string
}
```

With (matching `tasks/page.tsx`):
```ts
interface TaskRow {
  id: string
  title: string
  status: string
  pipeline?: string | null
  repository: { name: string }
  createdAt: string
  updatedAt: string
  completedAt?: string | null
  totalCostUsd?: number | null
  totalTokens?: number | null
}
```

#### 5b. Additional imports

Add the following imports (already used by `tasks/page.tsx`):
```ts
import { formatCost } from '@/lib/spending'
import { formatElapsed } from '@/lib/format'
import { monoStyle } from '@/lib/theme'
```

(`formatTokens` and `formatDate` are already imported.)

#### 5c. Replace the table

Replace the existing 5-column Recent Tasks table (Title | Pipeline | Status | Repository | Created) with the 6-column version matching `tasks/page.tsx` minus the ID column:

**Headers**: Title | Status | Repository | Pipeline | Cost | Updated

**Skeleton rows**: change from `[...Array(5)]` with 5 cells to `[...Array(5)]` with 6 cells.

**Data rows** (mirror `tasks/page.tsx` exactly, minus ID cell):
```tsx
<TableRow
  key={task.id}
  hover
  sx={{ cursor: 'pointer' }}
  onClick={() => router.push(`/tasks/${task.id}`)}
  data-testid={`task-row-${task.id}`}
>
  <TableCell>{task.title}</TableCell>
  <TableCell>
    <StatusChip status={task.status} />
  </TableCell>
  <TableCell>{task.repository.name}</TableCell>
  <TableCell>{task.pipeline ?? '—'}</TableCell>
  <TableCell>
    <Typography variant="body2" color="warning.main" sx={{ ...monoStyle, fontSize: '0.8rem' }}>
      {formatCost(task.totalCostUsd)}
    </Typography>
    {task.totalTokens != null && task.totalTokens > 0 && (
      <Typography variant="caption" color="text.secondary" sx={{ ...monoStyle, fontSize: '0.7rem', display: 'block' }}>
        {formatTokens(task.totalTokens)}
      </Typography>
    )}
  </TableCell>
  <TableCell title={formatDate(task.updatedAt)}>
    {['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'CLOSED'].includes(task.status?.toUpperCase())
      ? formatDate(task.completedAt || task.updatedAt)
      : formatElapsed(task.updatedAt)}
  </TableCell>
</TableRow>
```

The `GET_TASKS` query (from `task-queries.ts`) already fetches all needed fields: `id`, `title`, `status`, `repository.name`, `createdAt`, `updatedAt`, `pipeline`, `totalCostUsd`, `totalTokens`. The dashboard uses `{ variables: { limit: 10, offset: 0 } }` which is correct — no query changes needed.

**Section heading**: Change "Recent Tasks" heading to remain as-is (it's cosmetically accurate).

### 6. Tests

#### 6a. `api/src/__tests__/token-usage-resolver.test.ts`

- Add `cost_usd` to each mock row in `Query.tokenUsageByModel` tests.
- Assert that returned objects include `costUsd` field.
- Add a test: `should COALESCE cost_usd in the query` that checks `COALESCE(SUM(cost_usd), 0)` appears in the SQL.

Specific changes:
1. In the "should return mapped token usage rows" test, add `cost_usd: 0.05` to mock rows and assert `result[0].costUsd` equals `0.05`.
2. In the "should return empty array when no data" test — no change needed (empty array still works).
3. Add a new test "should include costUsd in returned rows" that verifies the field is present.
4. Update "should query with parameterized interval" test to also check the SQL contains `cost_usd`.

#### 6b. `web/src/components/dashboard/__tests__/TokenUsageChart.test.ts`

- Add `costUsd: number` to the local `TokenUsageByDay` interface in the test file.
- Add `costUsd` to all `sampleRow` and `testData` entries.
- Add a test in the `chart data transformation` block: "should aggregate costUsd per day" that sums cost values across models for a day.

---

## Assumptions

1. `ComposedChart` is available in the installed version of recharts (it is — recharts 2.x exports it).
2. The `cost_usd` column exists on the `stages` table and is already populated (confirmed: `dashboardStats` resolver already queries `SUM(cost_usd)` from `stages`).
3. The right Y-axis label is not required — the legend entry "Cost (USD)" and the `$`-prefixed tick formatter are sufficient to distinguish it from the token axis.
4. The dashboard Recent Tasks table does not need pagination or filters — it remains a fixed `limit: 10` snapshot, same as before.

---

## Files to Modify

| File | Change |
|------|--------|
| `api/src/schema.graphql` | Add `costUsd: Float!` to `TokenUsageByDay` |
| `api/src/resolvers/task-queries.ts` | Add `SUM(cost_usd)` to `tokenUsageByModel` SQL and map result |
| `web/src/lib/graphql/queries.ts` | Add `costUsd` to `TOKEN_USAGE_BY_MODEL` fragment |
| `web/src/components/dashboard/TokenUsageChart.tsx` | Switch to `ComposedChart`, add dual Y-axis and cost `Line` |
| `web/src/app/page.tsx` | Extend `TaskRow`, add imports, update Recent Tasks table |
| `api/src/__tests__/token-usage-resolver.test.ts` | Add `costUsd` assertions to resolver tests |
| `web/src/components/dashboard/__tests__/TokenUsageChart.test.ts` | Add `costUsd` to test data and add aggregation test |

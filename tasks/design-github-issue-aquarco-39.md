# Design: Show Full Pipeline Execution History on Task Detail Page

**Issue:** #39
**Date:** 2026-03-27
**Status:** Design Complete

---

## Problem Statement

The task detail page only shows the *latest run* of each pipeline stage because the API applies `DISTINCT ON` to suppress superseded runs. For a feature-pipeline that looped three times through Implementation/Review, the UI shows 7 items instead of 19. Condition-evaluation messages (`_condition_message`) stored in `structured_output` are never surfaced, so users cannot understand why additional runs were triggered.

---

## Solution Overview

1. **Remove `DISTINCT ON` from both query paths** so all stage rows are returned in chronological order.
2. **Add `iteration` and `run` fields** to the GraphQL `Stage` type, `StageRow` interface, `mapStage` mapper, and the frontend types/query.
3. **Frontend: build a flat chronological list** of stage runs interleaved with condition evaluation blocks sourced from `structuredOutput._condition_message`.
4. **Deduplicate stages inside `PipelineStagesFlow`** (the SVG diagram still needs one node per unique pipeline step, colored by latest status).
5. **Standardize stage numbering to 1-based** in the Stage Output accordion header.

No database migration is required — `iteration` and `run` columns already exist from migration `025_add_stage_run.sql`.

---

## Layer-by-Layer Design

### 1. `api/src/schema.graphql` — Add fields to `Stage`

Add two new non-nullable Int fields:

```graphql
type Stage {
  id: ID!
  taskId: ID!
  stageNumber: Int!
  iteration: Int!       # NEW — which pipeline iteration pass (default 1)
  run: Int!             # NEW — run number within iteration (default 1)
  category: String!
  agent: String
  agentVersion: String
  status: StageStatus!
  startedAt: DateTime
  completedAt: DateTime
  structuredOutput: JSON
  rawOutput: String
  tokensInput: Int
  tokensOutput: Int
  errorMessage: String
  retryCount: Int!
  liveOutput: String
}
```

### 2. `api/src/loaders.ts` — StageRow + stagesByTaskLoader

**`StageRow` interface**: add two fields:
```ts
iteration: number   // default 1 in DB
run: number         // default 1 in DB
```

**`stagesByTaskLoader`**: remove `DISTINCT ON`, change the query to return all rows ordered chronologically:

```sql
SELECT s.*
FROM stages s
WHERE s.task_id = ANY($1)
ORDER BY s.task_id, s.stage_number ASC, s.iteration ASC, s.run ASC
```

> Assumption: `stage_number`, `iteration`, and `run` together uniquely identify execution order. The `started_at` timestamp could also be used but the triple-column sort is more deterministic.

### 3. `api/src/resolvers/queries.ts`

#### `mapStage` function (defined in `queries.ts`, not `mappers.ts`)

Add two new mapped fields:
```ts
iteration: row.iteration ?? 1,
run: row.run ?? 1,
```

#### `pipelineStatus` resolver (lines 175–180)

Remove `DISTINCT ON`, change ORDER BY to match the loader:

```sql
SELECT s.*
FROM stages s
WHERE s.task_id = $1
ORDER BY s.stage_number ASC, s.iteration ASC, s.run ASC
```

Also update `totalStages` to count distinct `stage_number` values (not total rows), so the progress indicator is not inflated:

```ts
// Count unique stage positions, not total runs
const uniqueStageNumbers = new Set(stagesResult.rows.map((r) => r.stage_number))
const totalStages = uniqueStageNumbers.size
```

### 4. `api/src/generated/types.ts` — Manual sync (no codegen step detected)

Add `iteration` and `run` to the `Stage` type object and `StageResolvers`:

```ts
export type Stage = {
  __typename?: 'Stage';
  // ... existing fields ...
  iteration: Scalars['Int']['output'];   // NEW
  run: Scalars['Int']['output'];         // NEW
};

export type StageResolvers = ResolversObject<{
  // ... existing resolvers ...
  iteration?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;   // NEW
  run?: Resolver<ResolversTypes['Int'], ParentType, ContextType>;         // NEW
  __isTypeOf?: IsTypeOfResolverFn<ParentType, ContextType>;
}>;
```

Also update the `ResolversParentTypes['Stage']` and `ResolversTypes['Stage']` entries (around line 700–800 in the generated file) to include the new fields.

### 5. `web/src/lib/graphql/queries.ts` — GET_TASK fragment

Add `iteration` and `run` to the stages fragment:

```graphql
stages {
  id
  stageNumber
  iteration    # NEW
  run          # NEW
  category
  agent
  agentVersion
  status
  startedAt
  completedAt
  structuredOutput
  rawOutput
  tokensInput
  tokensOutput
  errorMessage
  retryCount
  liveOutput
}
```

### 6. `web/src/app/tasks/[id]/page.tsx` — Multiple changes

#### 6a. `Stage` interface — add new fields

```ts
interface Stage {
  // ... existing ...
  iteration: number   // NEW
  run: number         // NEW
}
```

#### 6b. `PipelineStagesFlow` — deduplicate stages for SVG nodes

The `stages` prop now contains ALL runs. The SVG must show one node per unique pipeline step, colored by the latest-run status.

Inside `PipelineStagesFlow`, before computing `count` and building nodes, deduplicate:

```ts
// Deduplicate: keep the last occurrence per stageNumber (latest run)
const uniqueStageMap = new Map<number, Stage>()
for (const s of stages) {
  uniqueStageMap.set(s.stageNumber, s)  // later entries overwrite earlier ones
}
const uniqueStages = Array.from(uniqueStageMap.values())
  .sort((a, b) => a.stageNumber - b.stageNumber)
```

Then use `uniqueStages` (instead of `stages`) everywhere inside the SVG rendering:
- `const count = Math.max(uniqueStages.length, defnStages.length)`
- `const runtimeStage = uniqueStages[i]` (in the node loop)

The `activeStep` computation in the parent also needs updating — it should be based on the deduplicated view:
```ts
const activeStep = uniqueStages.findIndex(
  (s) => s.status === 'EXECUTING' || (s.status !== 'COMPLETED' && s.status !== 'SKIPPED')
)
```

Pass `uniqueStages` and `activeStep` to `PipelineStagesFlow`:
```tsx
<PipelineStagesFlow
  stages={uniqueStages}
  activeStep={activeStep}
  pipelineName={task.pipeline}
/>
```

Keep `stages` (all runs) available separately for the Stage Output section below.

#### 6c. Sort and label all stage runs

In `TaskDetailPage`, after fetching `task.stages`:

```ts
// All runs in chronological order (API already returns them sorted)
const stages = task.stages.slice().sort(
  (a, b) => a.stageNumber - b.stageNumber || a.iteration - b.iteration || a.run - b.run
)

// For SVG deduplication
const uniqueStageMap = new Map<number, Stage>()
for (const s of stages) { uniqueStageMap.set(s.stageNumber, s) }
const uniqueStages = Array.from(uniqueStageMap.values())
  .sort((a, b) => a.stageNumber - b.stageNumber)

const activeStep = uniqueStages.findIndex(
  (s) => s.status === 'EXECUTING' || (s.status !== 'COMPLETED' && s.status !== 'SKIPPED')
)
```

#### 6d. Build flat chronological list with evaluation blocks

Define a helper for run labels:

```ts
function runLabel(run: number): string {
  if (run === 1) return ''
  if (run === 2) return ' (next run)'
  if (run === 3) return ' (3rd run)'
  return ` (${run}th run)`
}
```

> Note: track global run occurrence per `stageNumber` as we iterate (not just the `run` field), in case `iteration` restarts `run` numbering. Use a `Map<number, number>` counter keyed by `stageNumber`.

Build a flat item list:

```ts
type HistoryItem =
  | { kind: 'stage'; stage: Stage; displayRun: number }
  | { kind: 'evaluation'; message: string; stageId: string; category: string }

const runCountPerStage = new Map<number, number>()
const historyItems: HistoryItem[] = []

for (const stage of stages) {
  const prev = runCountPerStage.get(stage.stageNumber) ?? 0
  const displayRun = prev + 1
  runCountPerStage.set(stage.stageNumber, displayRun)

  historyItems.push({ kind: 'stage', stage, displayRun })

  const condMsg = (stage.structuredOutput as Record<string, unknown> | null)
    ?._condition_message as string | undefined
  if (condMsg) {
    historyItems.push({
      kind: 'evaluation',
      message: condMsg,
      stageId: stage.id,
      category: stage.category,
    })
  }
}
```

#### 6e. Render the flat list

Replace the current `stages.map(...)` accordion block with:

```tsx
{historyItems.map((item, idx) => {
  if (item.kind === 'evaluation') {
    return (
      <Box
        key={`eval-${item.stageId}`}
        sx={{
          mx: 1, my: 0.5, px: 2, py: 1,
          backgroundColor: 'action.hover',
          borderLeft: '3px solid',
          borderColor: 'info.main',
          borderRadius: 1,
        }}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="caption" color="info.main" fontWeight={700}>
            Evaluation
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {item.message}
          </Typography>
        </Stack>
      </Box>
    )
  }

  const { stage, displayRun } = item
  const label = runLabel(displayRun)
  const output = stage.structuredOutput as Record<string, unknown> | null
  // ... same findings/summary/recommendation extraction as before ...

  return (
    <Accordion key={stage.id} variant="outlined" disableGutters>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}>
        <Stack direction="row" spacing={2} alignItems="center">
          <Typography fontWeight={600}>
            Stage {stage.stageNumber + 1}: {stage.category}{label}
          </Typography>
          {stage.agent && (
            <Typography variant="caption" color="text.secondary">
              {stage.agent}
            </Typography>
          )}
          <StatusChip status={stage.status} size="small" />
        </Stack>
      </AccordionSummary>
      <AccordionDetails>
        {/* ... same inner content as current implementation ... */}
      </AccordionDetails>
    </Accordion>
  )
})}
```

**Key changes in the accordion header vs current:**
- `Stage {stage.stageNumber}` → `Stage {stage.stageNumber + 1}` (1-based fix)
- Append `{label}` for subsequent runs (e.g., " (next run)", " (3rd run)")
- Evaluation rows are non-expandable inline blocks with left info-colored border

---

## Data Flow Diagram

```
DB stages table (all rows)
    ↓
stagesByTaskLoader (no DISTINCT ON, ORDER BY stage_number, iteration, run)
    ↓
GraphQL Stage type (+ iteration, run fields)
    ↓
GET_TASK query (stages fragment includes iteration, run)
    ↓
page.tsx task.stages (all runs, sorted)
    ├─→ uniqueStages (deduplicated, for SVG)
    │       ↓
    │   PipelineStagesFlow (status coloring by latest run)
    │
    └─→ historyItems (flat list: stage runs + evaluation blocks)
            ↓
        Stage Output section (accordion per run + info block per evaluation)
```

---

## Risks & Assumptions

| Risk | Mitigation |
|------|-----------|
| Larger API payload for tasks with many retries | Acceptable for now; pagination can be added later if needed |
| `pipelineProgress` subscription payload changes (now returns all runs) | No frontend currently consumes the subscription for stage rendering; no breaking change expected |
| `_condition_message` absent for some stages (e.g., early-exit stages) | Use optional chaining; only render evaluation block if message is truthy |
| `iteration` may reset `run` counter back to 1 (multi-pass pipelines) | Use per-`stageNumber` counter accumulated during list-building, not the raw `run` field |
| `run` field may be NULL for legacy stages (rows predating migration 025) | Default to `1` in both `StageRow` mapping and frontend; `iteration ?? 1`, `run ?? 1` |
| `api/src/generated/types.ts` is manually maintained (no codegen build step detected) | Patch manually; fields added as optional resolvers (`Resolver<...>`) with `??` defaults to avoid breaking existing resolvers |
| `totalStages` in `pipelineStatus` would be inflated if not deduplicated | Count `uniqueStageNumbers.size`, not `stagesResult.rows.length` |

---

## Acceptance Criteria

See structured output below.

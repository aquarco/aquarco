# Design: Improve Stage Output (Issue #139)

## Summary

Three targeted UX improvements to the Stage Output accordion on the task detail page:

1. **Fix run ordinal suffix** — change `"(next run)"` → `"(2nd run)"` for the second execution of a repeated stage.
2. **Show pipeline stage name** — look up the human-readable pipeline stage name (e.g. `FIX REVIEW FINDINGS`) from the pipeline definition instead of displaying the raw category string (e.g. `implement`).
3. **Fixed-width StatusChip** — give `StatusChip` a fixed minimum width and right-align it so all stage name labels start at the same horizontal position across accordion rows.

All changes are contained within two files: `StageOutputSection.tsx` (primary) and `page.tsx` (pass new prop).

---

## Change 1: Fix Run Ordinal Suffix

**File:** `web/src/components/tasks/StageOutputSection.tsx`

**Current code (lines 43–45):**
```tsx
if (runCount === 2) runSuffix = ' (next run)'
else if (runCount === 3) runSuffix = ' (3rd run)'
else if (runCount > 3) runSuffix = ` (${runCount}th run)`
```

**New code:**
```tsx
if (runCount === 2) runSuffix = ' (2nd run)'
else if (runCount === 3) runSuffix = ' (3rd run)'
else if (runCount > 3) runSuffix = ` (${runCount}th run)`
```

Simple string substitution. No logic change.

---

## Change 2: Show Pipeline Stage Name

### Prop Addition

`StageOutputSection` must receive the pipeline name from the task so it can look up stage definitions.

**Interface change:**
```tsx
interface StageOutputSectionProps {
  stages: Stage[]
  effectiveExecutingStages: Set<number>
  pipelineName: string          // NEW — passed from task detail page
}
```

### Query Inside StageOutputSection

`StageOutputSection` will call `useQuery(GET_PIPELINE_DEFINITIONS)` to fetch pipeline definitions. Apollo Client caches results across components — `PipelineStagesFlow` already makes this call on the same page, so this resolves immediately from cache with no extra network request.

```tsx
import { useQuery } from '@apollo/client'
import { GET_PIPELINE_DEFINITIONS } from '@/lib/graphql/queries'
import type { PipelineStageDefn } from '@/app/tasks/[id]/types'

export function StageOutputSection({ stages, effectiveExecutingStages, pipelineName }: StageOutputSectionProps) {
  const { data: pipeData } = useQuery(GET_PIPELINE_DEFINITIONS)
  const pipelineDefs = (pipeData?.pipelineDefinitions ?? []) as Array<{
    name: string
    stages: PipelineStageDefn[]
  }>
  const defnStages: PipelineStageDefn[] =
    pipelineDefs.find((p) => p.name === pipelineName)?.stages ?? []
  // ...
}
```

### Stage Name Resolution

For each stage in the accordion loop, resolve the display name using `stageNumber` as a 0-based index into `defnStages`:

```tsx
const stageName: string =
  defnStages[stage.stageNumber]?.name ?? stage.category.toUpperCase()
```

**Fallback behaviour:**
- If the pipeline definition is not loaded yet → falls back to `stage.category.toUpperCase()` (e.g. `IMPLEMENT`).
- If `stageNumber` is out of bounds (system categories like `planning`, `condition-eval`) → same fallback.
- If the pipeline definition doesn't match the task's pipeline → same fallback.

**Usage in JSX** — replace `{stage.category}` with `{stageName}`:
```tsx
<Typography variant="body2" fontWeight={600}>
  {stageName}{runSuffix}
</Typography>
```

### Prop Threading in page.tsx

**File:** `web/src/app/tasks/[id]/page.tsx`

Add `pipelineName` prop on line 122:
```tsx
<StageOutputSection
  stages={stages}
  effectiveExecutingStages={effectiveExecutingStages}
  pipelineName={task.pipeline}
/>
```

---

## Change 3: Fixed-Width StatusChip Alignment

**File:** `web/src/components/tasks/StageOutputSection.tsx`

The `StatusChip` in each accordion summary must occupy a fixed-width slot, right-aligned within that slot, so that stage name labels align across all rows regardless of status label length.

Longest status values: `EXECUTING`, `COMPLETED`, `CANCELLED` (9 chars), `RATE_LIMITED` (12 chars). A `minWidth` of `120px` safely covers all current and near-future values.

**Current JSX:**
```tsx
<StatusChip status={effectiveStatus} size="small" />
```

**New JSX — wrap in a fixed-width Box:**
```tsx
<Box sx={{ minWidth: 120, display: 'flex', justifyContent: 'flex-end' }}>
  <StatusChip status={effectiveStatus} size="small" />
</Box>
```

This makes the chip right-align within its 120 px column, and the `Typography` stage name that follows in the same `Stack` always starts at the same offset.

---

## Assumptions

1. `stageNumber` on `Stage` is 0-based and corresponds directly to the positional index in `defnStages[]`. This mirrors how `PipelineStagesFlow` resolves names (`defnStages[i]` where `i` is the iteration index over sorted `stages`).
2. System category stages (`planning`, `condition-eval`) have a `stageNumber` that either falls outside the pipeline definition's stage array or has no matching definition — both cases are handled by the fallback.
3. `GET_PIPELINE_DEFINITIONS` is already imported from `@/lib/graphql/queries` (re-exported from `agent-queries.ts`). No new GraphQL queries or schema changes are needed.
4. No `StatusChip` API change is needed — the fixed width is applied via a wrapper `Box`.

---

## Files Modified

| File | Change |
|------|--------|
| `web/src/components/tasks/StageOutputSection.tsx` | Add `pipelineName` prop, add `useQuery`, resolve stage names, fix `(2nd run)`, fixed-width chip |
| `web/src/app/tasks/[id]/page.tsx` | Pass `pipelineName={task.pipeline}` to `StageOutputSection` |

No database migrations, no GraphQL schema changes, no new files.

# Design: Add `execution_order` to Stages

**Task ID**: 102-rerun-1  
**Issue**: https://github.com/aquarco/aquarco/issues/102  
**Author**: design-agent  
**Date**: 2026-04-08

---

## 1. Problem

The `stages` table is currently sorted by `(stage_number, iteration, run)`. This breaks when the conditions engine performs stage jumps, because the same `stage_number` can appear multiple times and the result set order does not reflect actual execution sequence. A pipeline that runs stages 0 → 1 → 0 → 2 (one backward jump) displays all stage-0 rows before stage-1, hiding the real execution order.

---

## 2. Solution Summary

Add a nullable `execution_order INTEGER` column to the `stages` table. The Python supervisor maintains a per-task counter and writes the value when:

- A stage transitions from PENDING → EXECUTING (`record_stage_executing`)
- A stage is SKIPPED (`record_stage_skipped`)

PENDING stages (not yet invoked) retain `NULL`. All DB queries and the frontend sort by `execution_order ASC NULLS LAST`.

---

## 3. Resolved Design Decisions

### 3.1 SVG Diagram Deduplication
The pipeline diagram deduplicates stages to one row per `stage_number`. It uses the **latest run wins** approach (existing behavior). For deduplicated entries, `execution_order` is irrelevant — the diagram sorts by `stageNumber ASC` to preserve pipeline position order. Only the history list (all stage rows) changes its sort key.

### 3.2 Pre-population of PENDING Stages
`create_planned_pending_stages` pre-inserts all stages as PENDING with `execution_order = NULL`. This is correct — NULL signals "not yet invoked."

### 3.3 Parallel Stage Assignment
Pre-allocate `N` execution_order values (one per agent) **before** launching `asyncio.gather`. This avoids races in the synchronous in-memory counter. Values are assigned sequentially to agents in the `agents` list order.

### 3.4 Counter Recovery on Resume
When `start_stage > 0` (task resume path), initialize the counter from the DB:

```sql
SELECT COALESCE(MAX(execution_order), 0) FROM stages
WHERE task_id = %(id)s AND execution_order IS NOT NULL
```

### 3.5 System Stage Coverage
Planning and condition-evaluator stages (`stage_number = -1`) also receive `execution_order`. They are created via `create_system_stage` (pending), then set to executing via `record_stage_executing`. The counter is incremented in the executor before calling `record_stage_executing`, same as for regular stages.

### 3.6 Uniqueness Enforcement
A **partial unique index** on `(task_id, execution_order) WHERE execution_order IS NOT NULL` ensures Python bugs cannot silently assign duplicate values.

---

## 4. Database Migration

### File: `db/migrations/042_add_execution_order.sql`
```
-- depends: 041_backfill_stage_model
```

**Changes:**

1. `ALTER TABLE stages ADD COLUMN IF NOT EXISTS execution_order INTEGER;`  
   - Nullable, no default. NULL = PENDING (never invoked).
   - Comment: "Actual sequence in which this stage was invoked, 1-based, scoped to task_id. Set when transitioning to executing or skipped. NULL for PENDING stages."

2. `CREATE UNIQUE INDEX CONCURRENTLY idx_stages_task_execution_order ON stages(task_id, execution_order) WHERE execution_order IS NOT NULL;`  
   - Partial unique: prevents duplicate assignment. Does NOT block NULL rows.

3. **Update `get_task_context()` function** to include `execution_order` in the stage JSONB object alongside `stage_number`, `category`, etc.:
   ```sql
   'execution_order', s.execution_order,
   ```
   Also update the ORDER BY from `s.stage_number, s.iteration` to `s.execution_order ASC NULLS LAST, s.stage_number ASC` to return stages in execution sequence to agents.

### File: `db/migrations/042_add_execution_order.rollback.sql`
```sql
DROP INDEX IF EXISTS idx_stages_task_execution_order;
ALTER TABLE stages DROP COLUMN IF EXISTS execution_order;
```
Then restore the previous `get_task_context()` body (remove `execution_order` and restore the old ORDER BY).

---

## 5. Supervisor Changes

### 5.1 `task_queue.py` — `record_stage_executing`

**Signature change** (add `execution_order: int | None = None` keyword argument):

```python
async def record_stage_executing(
    self, task_id, stage_num, category, agent, *,
    stage_id=None, stage_key=None, iteration=1, run=1,
    input_context=None,
    execution_order: int | None = None,   # NEW
) -> None:
```

All three UPDATE paths (by `stage_id`, by `stage_key`, legacy) must include:
```sql
execution_order = %(eo)s
```
in their `SET` clause, with `"eo": execution_order` in the params dict.

### 5.2 `task_queue.py` — `record_stage_skipped`

**Signature change** (add `execution_order: int | None = None`):

```python
async def record_stage_skipped(
    self, task_id, stage_num, category, *,
    stage_id=None, stage_key=None,
    execution_order: int | None = None,   # NEW
) -> None:
```

All three UPDATE paths include `execution_order = %(eo)s`.

### 5.3 `pipeline/executor.py` — `PipelineExecutor`

**Add instance state:**
```python
def __init__(self, db, task_queue, registry, pipelines):
    ...
    self._execution_order: dict[str, int] = {}
```

**Add counter helper** (synchronous — counter is in-memory):
```python
def _next_execution_order(self, task_id: str) -> int:
    """Return the next sequential execution_order for this task."""
    val = self._execution_order.get(task_id, 0) + 1
    self._execution_order[task_id] = val
    return val
```

**In `execute_pipeline`**, after computing `start_stage`, initialize the counter:
```python
if start_stage > 0:
    max_eo = await self._db.fetch_val(
        """SELECT COALESCE(MAX(execution_order), 0) FROM stages
           WHERE task_id = %(id)s AND execution_order IS NOT NULL""",
        {"id": task_id},
    )
    self._execution_order[task_id] = int(max_eo or 0)
else:
    self._execution_order[task_id] = 0
```

**In `_execute_planning_phase`**, increment before `record_stage_executing`:
```python
planning_stage_id = await self._tq.create_system_stage(...)
eo = self._next_execution_order(task_id)
await self._tq.record_stage_executing(
    task_id, -1, "planning", "planner-agent",
    stage_id=planning_stage_id,
    stage_key=planning_stage_key, iteration=1,
    execution_order=eo,
)
```

**In `_execute_running_phase`**, the sequential agent path:
```python
eo = self._next_execution_order(task_id)
out, sid = await self._execute_planned_stage(
    task_id, stage_num, category, agent_name,
    accumulated, iteration=base_iteration,
    stage_id=stage_ids.get(sk),
    work_dir=clone_dir,
    pipeline_name=pipeline_name,
    execution_order=eo,           # NEW
)
```

For the skipped stages path (optional stage failed):
```python
eo = self._next_execution_order(task_id)
await self._tq.record_stage_skipped(
    task_id, stage_num, category,
    stage_id=agent_stage_id, stage_key=sk,
    execution_order=eo,           # NEW
)
```

For the **condition-evaluator** inside `_ai_eval` closure:
```python
eo = self._next_execution_order(task_id)
await self._tq.record_stage_executing(
    task_id, stage_num, "condition-eval", "condition-evaluator",
    stage_id=cond_stage_id,
    stage_key=cond_stage_key,
    iteration=_cond_eval_iteration,
    execution_order=eo,           # NEW
)
```

For the **parallel path**, pre-allocate before `asyncio.gather`:
```python
if parallel and len(agents) > 1:
    parallel_eos = {
        agent: self._next_execution_order(task_id)
        for agent in agents
    }
    stage_output = await self._execute_parallel_agents(
        task_id, stage_num, category, agents,
        clone_dir, branch_name,
        pipeline_name=pipeline_name,
        stage_ids=stage_ids,
        execution_orders=parallel_eos,   # NEW
    )
```

**Update `_execute_planned_stage` signature:**
```python
async def _execute_planned_stage(
    self, task_id, stage_num, category, agent_name, context, *,
    iteration=1, stage_id=None, work_dir=None, pipeline_name="",
    execution_order: int | None = None,   # NEW
) -> tuple[dict[str, Any], int | None]:
```

Pass `execution_order` to `record_stage_executing` call inside this method.

**Update `_execute_parallel_agents` signature:**
```python
async def _execute_parallel_agents(
    self, task_id, stage_num, category, agents, clone_dir, branch_name, *,
    pipeline_name="", stage_ids=None,
    execution_orders: dict[str, int] | None = None,   # NEW
) -> dict[str, Any]:
```

Inside `_run_in_worktree`, pass the pre-allocated value:
```python
return await self._execute_planned_stage(
    ...,
    execution_order=(execution_orders or {}).get(agent_name),
)
```

---

## 6. GraphQL API Changes

### 6.1 `api/src/schema.graphql`

Add to `type Stage`:
```graphql
type Stage {
  ...existing fields...
  executionOrder: Int       # NEW — null for PENDING stages
}
```

### 6.2 `api/src/loaders.ts`

Add to `StageRow` interface:
```typescript
execution_order: number | null
```

Update `stagesByTaskLoader` ORDER BY:
```sql
ORDER BY s.task_id,
         s.execution_order ASC NULLS LAST,
         s.stage_number ASC,
         COALESCE(s.iteration, 1) ASC,
         COALESCE(s.run, 1) ASC
```

The `execution_order` primary sort ensures all non-null rows come first in execution sequence; PENDING rows (null) sort to the bottom with the secondary fallback.

### 6.3 `api/src/resolvers/queries.ts` — `mapStage`

Add the field mapping:
```typescript
export function mapStage(row: Record<string, unknown>) {
  return {
    ...existing fields...
    executionOrder: (row.execution_order as number | null) ?? null,   // NEW
  }
}
```

### 6.4 `api/src/generated/types.ts`

Add `executionOrder?: Maybe<Scalars['Int']['output']>` to the `Stage` type (or regenerate via `graphql-codegen`).

---

## 7. Frontend Changes

### 7.1 `web/src/lib/graphql/queries.ts`

Add `executionOrder` to the `GET_TASK` stages selection:
```graphql
stages {
  id
  stageNumber
  iteration
  run
  executionOrder      # NEW
  category
  ...
}
```

### 7.2 `web/src/app/tasks/[id]/page.tsx`

**Add to `Stage` interface:**
```typescript
interface Stage {
  ...
  executionOrder: number | null   // NEW
}
```

**Update sort** (line ~649, `const stages = task.stages.slice().sort(...)`):

Replace the current `stageNumber → iteration → run` sort with:
```typescript
const stages = task.stages.slice().sort((a, b) => {
  // Primary: execution_order ASC NULLS LAST
  if (a.executionOrder != null && b.executionOrder != null) {
    return a.executionOrder - b.executionOrder
  }
  if (a.executionOrder != null) return -1   // a has order, b doesn't → a first
  if (b.executionOrder != null) return 1    // b has order, a doesn't → b first
  // Both null (legacy rows): fall back to stageNumber → iteration → run
  if (a.stageNumber !== b.stageNumber) return a.stageNumber - b.stageNumber
  const iterA = a.iteration ?? 1
  const iterB = b.iteration ?? 1
  if (iterA !== iterB) return iterA - iterB
  return (a.run ?? 1) - (b.run ?? 1)
})
```

**SVG deduplication sort** (`uniqueStages` sort, line ~665) is **unchanged** — it still sorts by `stageNumber ASC` since the diagram represents pipeline position structure, not execution trace.

---

## 8. Acceptance Criteria

1. `SELECT execution_order FROM stages LIMIT 1` succeeds after migration; column exists and is nullable.
2. For a pipeline that completes stages 0 → 1 → 2 linearly, `SELECT execution_order FROM stages WHERE task_id = X ORDER BY stage_number` returns 1, 2, 3.
3. For a pipeline with a backward jump (stage 0 → 1 → 0 → 2), the four stage rows have `execution_order` values 1, 2, 3, 4 ordered by wall-clock sequence (not `stage_number`).
4. PENDING stage rows created by `create_planned_pending_stages` have `execution_order = NULL`.
5. A SKIPPED stage has a non-null `execution_order` less than the `execution_order` of subsequent stages.
6. System stages (planning, condition-eval) have non-null `execution_order` values that fit in the execution sequence.
7. The partial unique index rejects an INSERT with a duplicate `(task_id, execution_order)` when both are non-null.
8. When a task resumes from a checkpoint, the new stages' `execution_order` values continue from `MAX(execution_order)` — no collisions or restarts.
9. The GraphQL `Stage` type exposes `executionOrder: Int` (null for PENDING; integer for all other statuses).
10. `GET_TASK` query includes `executionOrder`; the field is present in the response.
11. The task detail page history list renders stages in `executionOrder ASC NULLS LAST` order: for a pipeline with a backward jump, the second visit to stage 0 appears after the first visit to stage 1.
12. The SVG pipeline diagram is unaffected: stages still appear in `stageNumber ASC` order corresponding to pipeline definition position.
13. `get_task_context()` returns `execution_order` in each stage record within the JSONB output consumed by agents.
14. Parallel agents each receive distinct, sequential `execution_order` values.

---

## 9. Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Resume path misses counter recovery → duplicate values | DB unique index rejects duplicates; unit test covers resume |
| Parallel asyncio.gather assigns same EO | Counter incremented synchronously before gather; pre-allocated dict passed in |
| `_execute_parallel_agents` default `execution_orders=None` breaks parallel stages | Default falls back to None → NULL in DB (allowed); will fail CI test if coverage checked |
| Existing historical stages all have `execution_order = NULL` | Sort handles NULLS LAST; no backfill needed |
| `get_task_context()` recompile required after migration | Migration updates the function; supervisor restart picks up new DB function |

# Resume iteration tracking: preserve per-iteration data in _execute_agent

## Problem

When a stage hits `error_max_turns`, the executor resumes the same Claude CLI
session via `--resume`. The resume loop in `_execute_agent()` (executor.py:1055)
runs multiple `execute_claude()` calls but only stores the **last** iteration's
data. All intermediate iterations are silently discarded.

### What is lost

| Data | Where overwritten | Impact |
|------|-------------------|--------|
| `raw_output` (full NDJSON) | Line 1163: `output["_raw_output"] = claude_output.raw` — only last iteration | Cannot debug intermediate iterations; claude-spend sees full session JSONL but DB has partial |
| `structured_output` | Line 1074: `output = claude_output.structured` — replaced each loop pass | Partial results from intermediate iterations lost |
| Per-iteration token/cost | Accumulated into `_cumulative_*` fields but per-iteration breakdown not stored | Cannot attribute cost to specific resume iterations |

### Evidence

Comparing session `a5820338` (review stage of task `github-push-aquarco-107f95c3a0dd`):

- **DB result message** (`num_turns=23`): cost $0.486, input=25, cache_write=44429, cache_read=644333, output=8417
- **claude-spend** (full session JSONL, `queryCount=29`): cost $0.607, input=33, cache_write=66012, cache_read=815868, output=7618

The 6 extra queries in claude-spend correspond to resume iterations whose data
is not captured in the DB. (In this specific case `_cumulative_cost_usd == _cost_usd`,
meaning no resume actually occurred — but the architecture would lose data if it did.)

### Current workaround

Cumulative accumulators were added (2026-03-30) for cost and all four token
buckets (`_cumulative_input_tokens`, `_cumulative_cache_read_tokens`,
`_cumulative_cache_write_tokens`, `_cumulative_output_tokens`).
`store_stage_output()` prefers cumulative values over per-iteration values.

This gives correct **totals** but no per-iteration breakdown.

## Proposed fix

Each `execute_claude()` call within the resume loop should create its own
stage row, not overwrite the previous one. The DB schema already supports this:

- **`run` column** — currently used for retry-after-failure, could be extended
  or a new `resume_seq` column added
- **Unique constraint** `(task_id, stage_key, iteration, run)` — would need to
  accommodate resume sequence

### Approach options

**Option A: Use `run` column for resume iterations**
- Increment `run` for each resume within `_execute_agent`
- Each iteration gets its own row with full `raw_output`, `structured_output`, tokens, cost
- Final row is marked `completed`, intermediate rows marked `resumed` (new status)
- Pro: no schema change needed (just a new status value)
- Con: overloads `run` semantics (currently means "retry after failure")

**Option B: Add `resume_seq` column**
- New integer column, default 1
- Each resume iteration increments `resume_seq`
- Update unique constraint to `(task_id, stage_key, iteration, run, resume_seq)`
- Pro: clean separation of concerns
- Con: schema change, migration

**Option C: Store intermediate outputs in a separate table**
- New `stage_executions` table linked to `stages`
- Each `execute_claude()` call = one `stage_executions` row
- `stages` row stores final/aggregate data
- Pro: cleanest separation, stages table stays simple
- Con: more complex queries for spending aggregation

### Recommended: Option A

Lowest friction. The `run` column already represents "attempt N for this stage".
A resume is conceptually another attempt. Add status `resumed` to mark
intermediate rows. The final row keeps status `completed`.

## Files involved

- `supervisor/python/src/aquarco_supervisor/pipeline/executor.py` — `_execute_agent()` resume loop
- `supervisor/python/src/aquarco_supervisor/task_queue.py` — `store_stage_output()`, `create_rerun_stage()`
- `db/migrations/003_create_stages.sql` — status CHECK constraint (add `resumed`)

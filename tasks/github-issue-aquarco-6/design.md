# Design: Powerful Conditions in Pipeline

**Task:** github-issue-aquarco-6
**Date:** 2026-03-26
**Status:** Draft

---

## 1. Overview

This design covers two interrelated changes:

- **Part A — Config Refactor:** Move `outputSchema` from agent definitions into pipeline-level `categories` in `pipelines.yaml`. Update all code that reads output schemas to use the new location.
- **Part B — Condition Engine:** Replace the simple string-based `check_conditions()` with a structured exit-gate evaluator supporting `simple:` expressions, `ai:` Claude-evaluated conditions, `yes:`/`no:` named-stage jumps, and `maxRepeats:` loop guards.

The new `pipelines.yaml` format is already committed (categories section, named stages, structured conditions). The Python supervisor and API must be updated to match.

---

## 2. Current State

### Models (`models.py`)
```python
class StageConfig(BaseModel):
    category: str
    required: bool = True
    conditions: list[str] = Field(default_factory=list)  # ← string-based

class PipelineConfig(BaseModel):
    name: str
    version: str = "0.0.0"
    trigger: PipelineTrigger
    stages: list[StageConfig]  # ← no categories field
```

### Condition Evaluator (`executor.py:1126-1160`)
- Parses `"field operator value"` strings (space-split into 3 parts).
- Returns `bool` — used as a gate: if `False`, stage is skipped entirely.
- No support for compound expressions (`&&`, `||`), stage jumps, or AI evaluation.

### Stage Execution Loop (`executor.py:358-479`)
- Linear `for stage_num, plan in enumerate(planned_stages)`.
- `check_conditions()` called as skip gate; no jump semantics.
- Iteration loop driven by validation items, not conditions.

### Output Schema Resolution
- `cfg.get_agent_output_schema(agent_name)` reads `spec.outputSchema` from agent definition.
- Used in `_execute_agent()` at line 857 to pass to Claude CLI.

### DB Schema (`pipeline_definitions` table)
- Columns: `name, version, trigger_config (JSONB), stages (JSONB), is_active, created_at, updated_at`.
- No `categories` column.

---

## 3. Design Decisions

### D1: Categories stored as dict keyed by name
The YAML `categories:` is a list of `{name, outputSchema}`. In Python models we store it as `dict[str, dict[str, Any]]` (name → outputSchema) for O(1) lookup. The list→dict conversion happens in `load_pipelines()`.

### D2: Conditions are list of typed dicts
Each condition object has exactly one of `simple:` or `ai:` (string), plus optional `yes:` (str), `no:` (str), `maxRepeats:` (int). Modeled as `list[dict[str, Any]]` rather than a union Pydantic model — keeps it simple and matches the YAML shape.

### D3: Stage execution becomes name-indexed
The linear `enumerate` loop is replaced with a while-loop over `current_stage_name`. Stages are pre-indexed by name. After each stage, conditions are evaluated as exit gates to determine the next stage.

### D4: `check_conditions()` returns a `ConditionResult` rather than `bool`
New return type: `ConditionResult(matched: bool, jump_to: str | None)`. When `matched=False` and `jump_to=None`, the default next stage is used (preserving linear flow as fallback).

### D5: Simple expression evaluator uses safe tokenization
Compound expressions like `tests_added == 0 || (coverage_percent >= 80 && tests_failed == 0)` are parsed with a small recursive-descent parser. No `eval()`. Supports: `==`, `!=`, `>=`, `>`, `<=`, `<`, `&&`, `||`, parentheses, `true`, `false`, numeric literals, and string literals.

### D6: AI condition evaluation uses Claude CLI
`evaluate_ai_condition()` calls `execute_claude()` with a yes/no prompt, accumulated pipeline context, a short timeout (120s), and `--max-turns 1`. Returns `bool`.

### D7: maxRepeats tracked per stage name
A `repeat_counts: dict[str, int]` is maintained in the execution loop. Incremented each time a stage is visited via a condition jump. When `maxRepeats` is exceeded, that condition is treated as non-matching (fall through to next condition or default next stage).

### D8: Backward compatibility for agent-level outputSchema
`get_agent_output_schema()` on `AgentRegistry` and `ScopedAgentView` is kept but becomes a fallback. Primary lookup: pipeline category → outputSchema. Fallback: agent spec (for autoloaded agents that may still define it).

### D9: DB migration adds `categories` JSONB column
A new migration adds `categories JSONB DEFAULT '{}'` to `pipeline_definitions`. The `store_pipeline_definitions()` function is updated to include it.

---

## 4. Detailed Changes

### 4.1 Models (`models.py`)

**StageConfig** — add `name`, change `conditions` type:
```python
class StageConfig(BaseModel):
    name: str = ""
    category: str
    required: bool = True
    conditions: list[dict[str, Any]] = Field(default_factory=list)
```

**PipelineConfig** — add `categories`:
```python
class PipelineConfig(BaseModel):
    name: str
    version: str = "0.0.0"
    trigger: PipelineTrigger
    stages: list[StageConfig]
    categories: dict[str, dict[str, Any]] = Field(default_factory=dict)
```

### 4.2 Config Loading (`config.py`)

Update `load_pipelines()`:
```python
# Parse categories list → dict
raw_categories = entry.get("categories", [])
# Already on the pipeline-definition level, not per-pipeline
# Actually: categories is at the top-level of the YAML, shared across pipelines
categories_dict = {c["name"]: c.get("outputSchema", {}) for c in raw_categories}

# Parse stages with new fields
stages = [StageConfig(**s) for s in entry.get("stages", [])]
```

**Important:** In the current `pipelines.yaml`, `categories:` is at the top level (sibling of `pipelines:`), not inside each pipeline. `load_pipelines()` must extract it from the top-level raw dict and pass it to each `PipelineConfig`.

### 4.3 Output Schema Resolution (`executor.py`)

New method on `PipelineExecutor`:
```python
def _get_output_schema_for_stage(
    self, pipeline_name: str, category: str, agent_name: str,
    scoped_view: ScopedAgentView | None,
) -> dict[str, Any] | None:
    """Resolve outputSchema: pipeline category first, agent spec fallback."""
    # 1. Look up pipeline categories
    for p in self._pipelines:
        if p.name == pipeline_name:
            schema = p.categories.get(category)
            if schema:
                return schema
    # 2. Fallback to agent-level schema
    cfg = scoped_view or self._registry
    return cfg.get_agent_output_schema(agent_name)
```

Update `_execute_agent()` line 857 to call this instead of `cfg.get_agent_output_schema(agent_name)`. This requires passing `pipeline_name` and `category` to `_execute_agent()`.

### 4.4 Condition Engine (`executor.py`)

#### 4.4.1 New types and function signature

```python
@dataclass
class ConditionResult:
    """Result of evaluating a condition list."""
    jump_to: str | None = None  # stage name to jump to, or None for default next

def evaluate_conditions(
    conditions: list[dict[str, Any]],
    stage_outputs: dict[str, dict[str, Any]],
    current_stage_output: dict[str, Any],
    repeat_counts: dict[str, int],
    *,
    ai_evaluator: Callable[..., Awaitable[bool]] | None = None,
) -> Awaitable[ConditionResult]:
```

`stage_outputs` is a dict keyed by stage name (e.g., `{"analysis": {...}, "design": {...}}`), allowing cross-stage field references like `analysis.risks`.

`current_stage_output` is the output of the just-completed stage, used for unqualified field references like `severity`.

#### 4.4.2 Evaluation logic

For each condition in the list:
1. Check `maxRepeats` — if the target jump stage has reached its repeat count, skip this condition.
2. If `simple:` — evaluate the expression against merged context (current stage output + named stage outputs).
3. If `ai:` — call `ai_evaluator` with the prompt and context.
4. If result is `True` and `yes:` exists → return `ConditionResult(jump_to=yes_stage)`.
5. If result is `False` and `no:` exists → return `ConditionResult(jump_to=no_stage)`.
6. If no matching yes/no key, continue to next condition.
7. If all conditions exhausted → return `ConditionResult(jump_to=None)` (default next stage).

#### 4.4.3 Simple Expression Parser

A small recursive-descent parser for expressions like:
```
tests_added == 0 || (coverage_percent >= 80 && tests_failed == 0)
severity == major_issues || severity == blocking
true
```

Grammar:
```
expr     → or_expr
or_expr  → and_expr ("||" and_expr)*
and_expr → compare  ("&&" compare)*
compare  → atom (("==" | "!=" | ">=" | ">" | "<=" | "<") atom)?
atom     → "true" | "false" | "(" expr ")" | NUMBER | IDENTIFIER ("." IDENTIFIER)*
```

Field resolution: unqualified names (e.g., `severity`) resolve from current stage output. Qualified names (e.g., `analysis.risks`) resolve from `stage_outputs` dict first, then fall back to current output's nested path.

Values are compared as: numbers if both parseable as float, complexity enums if both valid complexity values, strings otherwise.

#### 4.4.4 AI Condition Evaluator

```python
async def evaluate_ai_condition(
    prompt: str,
    context: dict[str, Any],
    work_dir: str,
    task_id: str,
) -> bool:
    """Evaluate an AI condition by calling Claude CLI."""
    system_prompt = (
        "You are evaluating a pipeline condition. "
        "Answer ONLY 'YES' or 'NO' based on the context provided."
    )
    full_prompt = f"{prompt}\n\nContext:\n{json.dumps(context, indent=2, default=str)}"
    # Use execute_claude with max_turns=1, short timeout
    result = await execute_claude(
        prompt=full_prompt,
        append_system_prompt=system_prompt,
        work_dir=work_dir,
        timeout_seconds=120,
        task_id=task_id,
        max_turns=1,
    )
    answer = result.text.strip().upper()
    return answer.startswith("YES")
```

### 4.5 Stage Execution Loop Rewrite (`executor.py`)

Replace the linear `for stage_num, plan in enumerate(planned_stages)` with:

```python
async def _execute_running_phase(self, task_id, planned_stages, stage_defs, ...):
    # Build name-indexed lookup
    stages_by_name: dict[str, tuple[int, dict, dict]] = {}
    stage_order: list[str] = []
    for idx, (plan, sdef) in enumerate(zip(planned_stages, stage_defs)):
        name = sdef.get("name") or plan.get("name") or f"stage-{idx}"
        stages_by_name[name] = (idx, plan, sdef)
        stage_order.append(name)

    # Track outputs per stage name and repeat counts
    stage_outputs: dict[str, dict[str, Any]] = {}
    repeat_counts: dict[str, int] = {}

    current_idx = max(start_stage, 0)

    while current_idx < len(stage_order):
        stage_name = stage_order[current_idx]
        idx, plan, stage_def = stages_by_name[stage_name]

        # ... execute stage (same logic as today) ...

        stage_outputs[stage_name] = stage_output

        # Evaluate exit-gate conditions
        conditions = stage_def.get("conditions", [])
        if conditions:
            result = await evaluate_conditions(
                conditions, stage_outputs, stage_output,
                repeat_counts, ai_evaluator=...,
            )
            if result.jump_to and result.jump_to in stages_by_name:
                repeat_counts[result.jump_to] = repeat_counts.get(result.jump_to, 0) + 1
                current_idx = stage_order.index(result.jump_to)
                continue

        current_idx += 1
```

**Key difference from current:** Conditions are evaluated AFTER stage execution (exit gates), not before. The old behavior used conditions as entry gates to skip stages. The new behavior runs the stage first, then decides where to go next.

### 4.6 Cleanup: Agent Spec Validation

**`cli/agents.py`** — Remove `spec.output.format` validation (step 8, lines 176-185) and `outputFormat` from the normalised record (line 212). The `VALID_OUTPUT_FORMATS` constant can be removed.

**`api/src/resolvers/mutations.ts`** — Remove `'output'` from `REQUIRED_SPEC_KEYS` array. Keep `'outputSchema'` in `VALID_SPEC_KEYS` for backward compat.

### 4.7 Config Store (`config_store.py`)

Update `store_pipeline_definitions()` to include `categories` in the upsert:
```python
await db.execute(
    """INSERT INTO pipeline_definitions
           (name, version, trigger_config, stages, categories, is_active)
       VALUES (%(name)s, %(version)s, %(trigger_config)s, %(stages)s, %(categories)s, true)
       ON CONFLICT (name, version) DO UPDATE SET
           trigger_config = EXCLUDED.trigger_config,
           stages         = EXCLUDED.stages,
           categories     = EXCLUDED.categories,
           is_active      = true""",
    {
        "name": name,
        "version": version,
        "trigger_config": json.dumps(p.get("trigger", {})),
        "stages": json.dumps(p.get("stages", [])),
        "categories": json.dumps(p.get("categories", {})),
    },
)
```

### 4.8 DB Migration

New file: `db/migrations/029_add_pipeline_categories.sql`
```sql
-- depends: 028_repo_agent_scans
ALTER TABLE pipeline_definitions
    ADD COLUMN IF NOT EXISTS categories JSONB NOT NULL DEFAULT '{}';
```

Rollback: `db/migrations/029_add_pipeline_categories.rollback.sql`
```sql
ALTER TABLE pipeline_definitions DROP COLUMN IF EXISTS categories;
```

### 4.9 JSON Schema Update (`config/schemas/pipeline-definition-v1.json`)

- Add `categories` to top-level properties (array of `{name, outputSchema}`).
- Add `name` (string) to Stage definition.
- Change Stage `conditions` from `list[string]` to `list[ConditionObject]`.
- Define `ConditionObject` with properties: `simple` (string), `ai` (string), `yes` (string), `no` (string), `maxRepeats` (integer, minimum 1).
- Remove `enum` constraint on Stage `category` (allow custom categories from autoloaded agents).

### 4.10 Config Overlay Compatibility (`config_overlay.py`)

`_resolve_layered_config()` in `executor.py:68-70` serializes stages via `s.model_dump()`. The new `name` and `conditions` (dict) fields will be included automatically. The `merge_pipelines()` function in `config_overlay.py` does a dict-level merge that should handle new keys without changes, but must be verified.

The `get_pipeline_categories()` method should be added to `ScopedAgentView`:
```python
def get_pipeline_categories(self, pipeline_name: str) -> dict[str, dict[str, Any]]:
    for p in self._resolved.pipelines:
        if p.get("name") == pipeline_name:
            return p.get("categories", {})
    return {}
```

---

## 5. Assumptions

1. **Categories are shared across all pipelines in a file.** The YAML has `categories:` at the top level, not per-pipeline. All pipelines in the same file share the same category definitions.
2. **AI condition evaluation costs are acceptable.** Each `ai:` condition invokes a Claude CLI call. With `max_turns=1` and a simple yes/no prompt, cost should be minimal (~$0.01-0.05 per evaluation).
3. **`true` as a simple condition always evaluates to true.** Used for unconditional jumps (e.g., `fix-review-findings` always jumps back to `review`).
4. **maxRepeats is per condition, not per stage.** Each condition tracks its own repeat count for the target stage it would jump to.
5. **Backward compatibility:** Pipelines without `categories:` or with string-based conditions continue to work. String conditions are treated as legacy and wrapped internally.

---

## 6. Testing Strategy

### Existing tests to update:
- `test_pipeline/test_conditions.py` — All tests use string-based `check_conditions()`. Must be rewritten for new `evaluate_conditions()` with structured dicts.
- `test_config.py` — Pipeline loading tests must verify `categories` parsing and `StageConfig.name` field.
- `test_pipeline/test_executor.py` — Condition-related tests use old format.
- `test_models.py` — StageConfig/PipelineConfig field tests.

### New tests needed:
1. **Simple expression parser:** `==`, `!=`, `>=`, `<`, `&&`, `||`, parentheses, `true`/`false`, numeric comparison, string comparison, cross-stage field references.
2. **Condition evaluation with yes/no jumps:** Condition matches → jump_to populated. No match → jump_to None.
3. **maxRepeats enforcement:** Repeat count exceeds maxRepeats → condition skipped.
4. **AI condition evaluation:** Mock `execute_claude` to test yes/no parsing.
5. **Stage execution loop with jumps:** Full integration test with named stages and condition-driven routing.
6. **Categories loading:** `load_pipelines()` parses top-level categories and assigns to each pipeline.
7. **Output schema resolution:** Pipeline category lookup takes priority over agent spec.
8. **DB migration:** categories column present and functional.

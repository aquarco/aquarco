# Design: Claude API Error Handling (Issue #41)

**Task:** `github-issue-aquarco-41`
**Date:** 2026-03-29
**Stage:** Design (stage 1)
**Implements:** Expand retryable-error handling to cover HTTP 500 and 529 in addition to the existing 429 path.

---

## 1. Problem Summary

The supervisor only treats **429 (rate_limit_error)** as a retryable condition. HTTP **500 (api_error)** and **529 (overloaded_error)** from the Claude API currently fall through to `AgentExecutionError` → permanent task failure. Both are transient Anthropic-side errors that should be postponed and retried.

---

## 2. Design Decisions

### 2.1 Exception Hierarchy

Introduce `RetryableError` as a new base class between `AgentExecutionError` and the concrete postponable exceptions. Existing `RateLimitError` is moved under it; two new leaf classes are added.

```
AgentExecutionError  (existing, unchanged)
  └── RetryableError         ← NEW base: "task must be postponed, not failed"
        ├── RateLimitError   ← MOVED here (was direct child of AgentExecutionError)
        ├── ServerError      ← NEW: HTTP 500 / api_error
        └── OverloadedError  ← NEW: HTTP 529 / overloaded_error
```

**Rationale for `RetryableError` base:**
- `main.py` has a defensive catch for `RateLimitError` at the `execute_pipeline` call site. By catching `RetryableError` there (a single change), all future subclasses are covered automatically.
- `executor.py` can replace `except RateLimitError` with `except RetryableError` in a single block.
- Semantically correct — 500/529 are not rate-limit errors.

**`RateLimitError` is *moved*, not removed.** All existing `isinstance(e, RateLimitError)` checks continue to pass. All existing imports remain valid (the class is still exported from `exceptions.py`).

### 2.2 Detection Patterns

Mirrors existing `_is_rate_limited_in_lines()` / `_is_rate_limited()` approach. Two new helper pairs are added.

#### stdout NDJSON lines

| Error | Pattern | Function |
|-------|---------|----------|
| 500 api_error | `"api_error"` in `line.lower()` **or** `"status code 500"` in `line.lower()` | `_is_server_error_in_lines(lines)` |
| 529 overloaded | `"overloaded_error"` in `line.lower()` **or** `"status code 529"` in `line.lower()` | `_is_overloaded_in_lines(lines)` |

#### debug log file (last 32 KB)

| Error | Pattern | Function |
|-------|---------|----------|
| 500 api_error | `"api_error"` in text **or** `"status code 500"` in text.lower() | `_is_server_error(debug_log)` |
| 529 overloaded | `"overloaded_error"` in text **or** `"status code 529"` in text.lower() | `_is_overloaded(debug_log)` |

**Detection order in `execute_claude()` (within `if proc.returncode != 0:`):**
1. `_is_rate_limited_in_lines` / `_is_rate_limited` → raise `RateLimitError` (unchanged)
2. `_is_server_error_in_lines` / `_is_server_error` → raise `ServerError`
3. `_is_overloaded_in_lines` / `_is_overloaded` → raise `OverloadedError`
4. Raise `AgentExecutionError` (unchanged fallthrough)

### 2.3 Backoff / Cooldown Configuration

| Error | Status Code | Cooldown | Max Retries | Rationale |
|-------|-------------|----------|-------------|-----------|
| RateLimitError | 429 | 60 min | 24 | **Unchanged** |
| ServerError | 500 | 30 min | 12 | Anthropic internal — retry moderately |
| OverloadedError | 529 | 15 min | 24 | Platform overload — retry sooner |

To support per-error cooldowns correctly without runtime complexity, the cooldown value is **persisted to the database** alongside the `rate_limited` status. This requires a one-column migration.

### 2.4 `postpone_task()` Generalisation

`task_queue.py` gets a new method `postpone_task()` with configurable cooldown and max-retries. The existing `rate_limit_task()` is kept for backward compatibility but delegates to `postpone_task()`.

```python
async def postpone_task(
    self,
    task_id: str,
    error_message: str,
    *,
    cooldown_minutes: int = 60,
    max_retries: int = 24,
) -> None:
    """Mark task as postponed (rate_limited status) with a specific cooldown.

    Increments rate_limit_count. After max_retries, permanently marks failed.
    Persists cooldown_minutes so the resume poller can use per-task values.
    """
```

`get_rate_limited_tasks()` is updated to use the per-row `postpone_cooldown_minutes` column instead of a fixed parameter:

```python
async def get_postponed_tasks(self) -> list[str]:
    """Return task IDs where status='rate_limited' and the per-row cooldown has elapsed."""
    # SQL: WHERE status='rate_limited' AND updated_at < NOW() - make_interval(mins := postpone_cooldown_minutes)
```

`get_rate_limited_tasks(cooldown_minutes=60)` is kept as a deprecated alias calling `get_postponed_tasks()`.
`resume_rate_limited_task()` is unchanged.

### 2.5 `main.py` Changes

1. Import `RetryableError` in addition to (or replacing) `RateLimitError` import.
2. `_resume_rate_limited_tasks()`: call `get_postponed_tasks()` instead of `get_rate_limited_tasks(cooldown_minutes=60)`.
3. Defensive handler around `execute_pipeline`: change `except RateLimitError` → `except RetryableError`, call `postpone_task()` with `cooldown_minutes=_cooldown_for_error(e)`.

### 2.6 `executor.py` Changes

Replace `except RateLimitError as e:` with `except RetryableError as e:` in `_execute_running_phase()`. The handler body uses a helper `_cooldown_for_error(e)` to select the appropriate cooldown, then calls `self._tq.postpone_task(task_id, str(e), cooldown_minutes=cooldown, max_retries=max_retries)`.

The `except (StageError, RateLimitError): raise` in `_execute_planned_stage()` becomes `except (StageError, RetryableError): raise`.

---

## 3. Database Migration

### Migration file: `db/migrations/031_add_postpone_cooldown.sql`

```sql
-- depends: 030_add_agent_group
-- Migration 031: Add per-task postpone cooldown column
-- Supports differentiated backoff for 429/500/529 retryable errors.

SET search_path TO aquarco, public;

ALTER TABLE tasks
  ADD COLUMN IF NOT EXISTS postpone_cooldown_minutes INTEGER NOT NULL DEFAULT 60;
```

### Rollback file: `db/migrations/031_add_postpone_cooldown.rollback.sql`

```sql
SET search_path TO aquarco, public;
ALTER TABLE tasks DROP COLUMN IF EXISTS postpone_cooldown_minutes;
```

---

## 4. File-by-File Change Specification

### 4.1 `exceptions.py`

**Add** `RetryableError` between `AgentExecutionError` and `RateLimitError`:

```python
class RetryableError(AgentExecutionError):
    """Claude API returned a transient error — task should be postponed and retried."""


class RateLimitError(RetryableError):
    """Claude API rate limit (429) hit — task should be postponed."""


class ServerError(RetryableError):
    """Claude API internal server error (500) — task should be postponed."""


class OverloadedError(RetryableError):
    """Claude API platform overload (529) — task should be postponed."""
```

Docstring for `RetryableError` should be precise: it signals that the *Claude API* returned an error that is safe to retry after a cooldown, not a programming bug.

### 4.2 `cli/claude.py`

**Add imports** at top: `ServerError`, `OverloadedError` (and `RetryableError` is not directly needed here, but import for completeness if desired).

**Add four new private helpers** (after `_is_rate_limited()`):

```python
def _is_server_error_in_lines(lines: list[str]) -> bool:
    """Check whether any NDJSON stdout line contains HTTP 500 / api_error indicators."""
    for line in lines:
        lower = line.lower()
        if "api_error" in lower or "status code 500" in lower:
            return True
    return False


def _is_overloaded_in_lines(lines: list[str]) -> bool:
    """Check whether any NDJSON stdout line contains HTTP 529 / overloaded_error indicators."""
    for line in lines:
        lower = line.lower()
        if "overloaded_error" in lower or "status code 529" in lower:
            return True
    return False


def _is_server_error(debug_log: Path) -> bool:
    """Check whether the Claude CLI debug log contains HTTP 500 / api_error signals."""
    try:
        size = debug_log.stat().st_size
        read_size = min(size, 32768)
        with open(debug_log, "rb") as f:
            if size > read_size:
                f.seek(size - read_size)
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return "api_error" in text or "status code 500" in text.lower()


def _is_overloaded(debug_log: Path) -> bool:
    """Check whether the Claude CLI debug log contains HTTP 529 / overloaded_error signals."""
    try:
        size = debug_log.stat().st_size
        read_size = min(size, 32768)
        with open(debug_log, "rb") as f:
            if size > read_size:
                f.seek(size - read_size)
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return "overloaded_error" in text or "status code 529" in text.lower()
```

**Update `execute_claude()` error block** (within `if proc.returncode != 0:` after the existing `log.warning("claude_cli_failed", ...)`):

```python
# Detect rate-limit errors first (429)
if _is_rate_limited_in_lines(lines) or _is_rate_limited(debug_log):
    raise RateLimitError(
        f"Claude API rate limited (429) "
        f"(task={task_id}, stage={stage_num})"
    )

# Detect internal server errors (500)
if _is_server_error_in_lines(lines) or _is_server_error(debug_log):
    raise ServerError(
        f"Claude API server error (500) "
        f"(task={task_id}, stage={stage_num})"
    )

# Detect platform overload errors (529)
if _is_overloaded_in_lines(lines) or _is_overloaded(debug_log):
    raise OverloadedError(
        f"Claude API overloaded (529) "
        f"(task={task_id}, stage={stage_num})"
    )

raise AgentExecutionError(
    f"Claude CLI exited with code {proc.returncode} "
    f"(task={task_id}, stage={stage_num})"
)
```

**Update import line** (line 15):
```python
from ..exceptions import (
    AgentExecutionError, AgentInactivityError, AgentTimeoutError,
    OverloadedError, RateLimitError, ServerError,
)
```

### 4.3 `task_queue.py`

**Add `postpone_task()` method** (insert after `rate_limit_task()`):

```python
async def postpone_task(
    self,
    task_id: str,
    error_message: str,
    *,
    cooldown_minutes: int = 60,
    max_retries: int = 24,
) -> None:
    """Mark task as postponed (rate_limited), or permanently fail if retries exhausted.

    Generalises rate_limit_task() to support configurable cooldown and max retry
    values. The cooldown_minutes value is persisted so the resume poller can use
    per-task wait times.

    Args:
        task_id: The task to postpone.
        error_message: Human-readable error description stored on the task.
        cooldown_minutes: Minutes before the task becomes eligible for retry.
        max_retries: Maximum postpone attempts before permanent failure.
    """
    await self._db.execute(
        """
        UPDATE tasks
        SET rate_limit_count = rate_limit_count + 1,
            error_message = %(error)s,
            postpone_cooldown_minutes = %(cooldown)s,
            status = CASE
                WHEN rate_limit_count + 1 >= %(max)s THEN 'failed'
                ELSE 'rate_limited'
            END,
            completed_at = CASE
                WHEN rate_limit_count + 1 >= %(max)s THEN NOW()
                ELSE NULL
            END,
            updated_at = NOW()
        WHERE id = %(id)s
        """,
        {
            "id": task_id,
            "error": error_message,
            "cooldown": cooldown_minutes,
            "max": max_retries,
        },
    )
    row = await self._db.fetch_one(
        "SELECT status, rate_limit_count FROM tasks WHERE id = %(id)s",
        {"id": task_id},
    )
    if row and row["status"] == "failed":
        log.warning(
            "task_postpone_exhausted",
            task_id=task_id,
            rate_limit_count=row["rate_limit_count"],
        )
    else:
        log.warning(
            "task_postponed",
            task_id=task_id,
            cooldown_minutes=cooldown_minutes,
            rate_limit_count=row["rate_limit_count"] if row else 0,
        )
```

**Update `rate_limit_task()`** to delegate to `postpone_task()`:

```python
async def rate_limit_task(
    self, task_id: str, error_message: str, *, max_rate_limit_retries: int = 24,
) -> None:
    """Mark task as rate-limited (429). Delegates to postpone_task() with 60-min cooldown."""
    await self.postpone_task(
        task_id,
        error_message,
        cooldown_minutes=60,
        max_retries=max_rate_limit_retries,
    )
```

**Add `get_postponed_tasks()` method** (replaces hardcoded cooldown in `get_rate_limited_tasks()`):

```python
async def get_postponed_tasks(self) -> list[str]:
    """Return task IDs that are rate_limited and whose per-task cooldown has elapsed."""
    rows = await self._db.fetch_all(
        """
        SELECT id FROM tasks
        WHERE status = 'rate_limited'
          AND updated_at < NOW() - make_interval(mins := postpone_cooldown_minutes)
        ORDER BY updated_at ASC
        """
    )
    return [r["id"] for r in rows]
```

Keep `get_rate_limited_tasks(cooldown_minutes=60)` as a **deprecated alias** that calls `get_postponed_tasks()` (ignoring the parameter, since per-row values now apply) with a log warning. This preserves any external callers.

### 4.4 `pipeline/executor.py`

**Update import** (line 23):
```python
from ..exceptions import (
    NoAvailableAgentError, PipelineError, RetryableError,
    RateLimitError, StageError,
)
```

**Add helper function** `_cooldown_for_error(e: RetryableError) -> tuple[int, int]` (returns `(cooldown_minutes, max_retries)`):

```python
def _cooldown_for_error(e: RetryableError) -> tuple[int, int]:
    """Return (cooldown_minutes, max_retries) appropriate for a retryable error."""
    from ..exceptions import OverloadedError, ServerError
    if isinstance(e, OverloadedError):
        return 15, 24
    if isinstance(e, ServerError):
        return 30, 12
    # RateLimitError or any future RetryableError subclass → default 429 behaviour
    return 60, 24
```

**Replace `except RateLimitError` block** in `_execute_running_phase()` (lines ~544–562):

```python
except RetryableError as e:
    cooldown, max_retries = _cooldown_for_error(e)
    last_completed = stage_num - 1 if stage_num > 0 else 0
    await self._tq.checkpoint_pipeline(task_id, last_completed)
    await self._tq.postpone_task(
        task_id, str(e),
        cooldown_minutes=cooldown,
        max_retries=max_retries,
    )
    for agent_name in agents:
        sk = f"{stage_num}:{category}:{agent_name}"
        await self._db.execute(
            """
            UPDATE stages SET status = 'rate_limited', completed_at = NOW(),
                   error_message = %(error)s
            WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                  AND run = (
                      SELECT MAX(run) FROM stages
                      WHERE task_id = %(task_id)s AND stage_key = %(stage_key)s
                  )
            """,
            {"task_id": task_id, "stage_key": sk, "error": str(e)},
        )
    return True
```

**Update `except (StageError, RateLimitError): raise`** in `_execute_planned_stage()`:
```python
except (StageError, RetryableError):
    raise
```

### 4.5 `main.py`

**Update import**:
```python
from .exceptions import RetryableError  # replaces: from .exceptions import RateLimitError
```

(Keep `RateLimitError` import if used elsewhere in the file, otherwise remove.)

**Update `_resume_rate_limited_tasks()`**:
```python
async def _resume_rate_limited_tasks(self) -> None:
    """Move postponed tasks back to pending after their per-task cooldown has elapsed."""
    if not self._tq:
        return
    task_ids = await self._tq.get_postponed_tasks()
    for task_id in task_ids:
        log.info("resuming_postponed_task", task_id=task_id)
        await self._tq.resume_rate_limited_task(task_id)
```

**Update defensive handler** around `execute_pipeline`:
```python
except RetryableError as e:
    # Defensive: postpone if executor didn't already do so
    try:
        task = await self._tq.get_task(task_id) if self._tq else None
        if task and task.status.value != "rate_limited":
            from .exceptions import OverloadedError, ServerError
            cooldown = 15 if isinstance(e, OverloadedError) else 30 if isinstance(e, ServerError) else 60
            await self._tq.postpone_task(task_id, str(e), cooldown_minutes=cooldown)
    except Exception:
        log.exception("postpone_task_fallback_error", task_id=task_id)
    log.info("task_postponed_stopped", task_id=task_id)
```

---

## 5. Implementation Steps (Ordered)

The steps are ordered from smallest/foundational to largest/dependent:

1. **[DB] Create migration `031_add_postpone_cooldown.sql`** and rollback file.
   - One `ALTER TABLE` statement adding `postpone_cooldown_minutes INTEGER NOT NULL DEFAULT 60`.
   - Run migration locally to verify.

2. **[exceptions.py] Add `RetryableError`, `ServerError`, `OverloadedError`**
   - Insert `RetryableError` between `AgentExecutionError` and `RateLimitError`.
   - Change `RateLimitError` parent from `AgentExecutionError` → `RetryableError`.
   - Add `ServerError(RetryableError)` and `OverloadedError(RetryableError)`.

3. **[cli/claude.py] Add detection helpers and update `execute_claude()`**
   - Add `_is_server_error_in_lines()`, `_is_overloaded_in_lines()`, `_is_server_error()`, `_is_overloaded()`.
   - Insert the two new raise clauses in `execute_claude()` between the 429 check and the fallthrough `AgentExecutionError`.
   - Update the import line.

4. **[task_queue.py] Add `postpone_task()` and `get_postponed_tasks()`**
   - Add `postpone_task()` method.
   - Refactor `rate_limit_task()` to delegate to `postpone_task()`.
   - Add `get_postponed_tasks()` (per-row SQL cooldown).
   - Retain `get_rate_limited_tasks()` as deprecated alias.

5. **[pipeline/executor.py] Update exception handling**
   - Import `RetryableError`.
   - Add `_cooldown_for_error()` module-level helper.
   - Replace `except RateLimitError` with `except RetryableError` using `postpone_task()`.
   - Update `except (StageError, RateLimitError): raise` → include `RetryableError`.

6. **[main.py] Update imports and call sites**
   - Replace `RateLimitError` import with `RetryableError`.
   - Update `_resume_rate_limited_tasks()` to call `get_postponed_tasks()`.
   - Update defensive handler to catch `RetryableError` and compute per-type cooldown.

---

## 6. Acceptance Criteria

Each criterion is independently verifiable.

### Exception Hierarchy
- **AC-1**: `issubclass(RetryableError, AgentExecutionError)` is `True`.
- **AC-2**: `issubclass(RateLimitError, RetryableError)` is `True`.
- **AC-3**: `issubclass(ServerError, RetryableError)` is `True`.
- **AC-4**: `issubclass(OverloadedError, RetryableError)` is `True`.
- **AC-5**: `isinstance(RateLimitError("x"), AgentExecutionError)` is `True` (no regression).

### Detection — `_is_server_error_in_lines`
- **AC-6**: `_is_server_error_in_lines(['{"type":"error","error":{"type":"api_error"}}'])` returns `True`.
- **AC-7**: `_is_server_error_in_lines(['{"type":"error","error":{"type":"api_error","status_code":500}}'])` returns `True`.
- **AC-8**: `_is_server_error_in_lines(['status code 500 encountered'])` returns `True`.
- **AC-9**: `_is_server_error_in_lines(['{"type":"result","subtype":"success"}'])` returns `False`.
- **AC-10**: `_is_server_error_in_lines(['rate_limit_error'])` returns `False`.

### Detection — `_is_overloaded_in_lines`
- **AC-11**: `_is_overloaded_in_lines(['{"error":{"type":"overloaded_error"}}'])` returns `True`.
- **AC-12**: `_is_overloaded_in_lines(['status code 529'])` returns `True`.
- **AC-13**: `_is_overloaded_in_lines(['{"type":"result"}'])` returns `False`.

### `execute_claude()` exception selection
- **AC-14**: When stdout lines contain `"api_error"` and `returncode != 0`, `execute_claude()` raises `ServerError`.
- **AC-15**: When stdout lines contain `"overloaded_error"` and `returncode != 0`, `execute_claude()` raises `OverloadedError`.
- **AC-16**: When stdout lines contain `"rate_limit_error"` and `returncode != 0`, `execute_claude()` raises `RateLimitError` (regression guard).
- **AC-17**: When stdout lines contain no recognized error type and `returncode != 0`, `execute_claude()` raises `AgentExecutionError` (regression guard).

### `postpone_task()` database behaviour
- **AC-18**: Calling `postpone_task(task_id, "msg", cooldown_minutes=15)` sets `tasks.postpone_cooldown_minutes = 15` and `tasks.status = 'rate_limited'` in the database.
- **AC-19**: Calling `postpone_task()` with `max_retries=1` on a task that has already been postponed once sets `tasks.status = 'failed'`.
- **AC-20**: Calling `rate_limit_task()` sets `postpone_cooldown_minutes = 60` (backward compatibility).

### `get_postponed_tasks()` cooldown logic
- **AC-21**: A task with `status='rate_limited'`, `postpone_cooldown_minutes=15`, and `updated_at` 20 minutes ago is returned by `get_postponed_tasks()`.
- **AC-22**: A task with `status='rate_limited'`, `postpone_cooldown_minutes=60`, and `updated_at` 20 minutes ago is **not** returned by `get_postponed_tasks()`.

### Executor routing
- **AC-23**: When `execute_claude()` raises `ServerError`, `_execute_running_phase()` calls `postpone_task()` with `cooldown_minutes=30` and returns `True` (task is marked `rate_limited`, not `failed`).
- **AC-24**: When `execute_claude()` raises `OverloadedError`, `_execute_running_phase()` calls `postpone_task()` with `cooldown_minutes=15` and returns `True`.
- **AC-25**: When `execute_claude()` raises `RateLimitError`, `_execute_running_phase()` calls `postpone_task()` with `cooldown_minutes=60` (no regression).
- **AC-26**: When `execute_claude()` raises `AgentExecutionError` (no retryable signal), the task is eventually marked `failed` via `fail_task()`.

### `main.py` defensive handler
- **AC-27**: A `ServerError` propagating out of `execute_pipeline()` is caught and triggers `postpone_task()` with `cooldown_minutes=30`.
- **AC-28**: A `OverloadedError` propagating out of `execute_pipeline()` is caught and triggers `postpone_task()` with `cooldown_minutes=15`.

---

## 7. Assumptions and Out-of-Scope Items

### Assumptions
- **A1**: The Claude CLI debug log and stdout NDJSON use the string literals `"api_error"` and `"overloaded_error"` as error type identifiers (consistent with the Claude API JSON error format described in the issue).
- **A2**: The `rate_limit_count` column is appropriate for counting postponements regardless of error type. If stakeholders need separate counters per error type, a follow-up migration would be required.
- **A3**: The `resume_rate_limited_task()` method in `task_queue.py` does not need modification — it resets any `rate_limited` task to `pending`, which is correct for all retryable error types.

### Out of Scope
- **Streaming caveat (errors after HTTP 200)**: The current architecture only inspects error signals when `proc.returncode != 0`. If a 500/529 arrives mid-stream after a 200 response, the NDJSON lines will still contain error events, but the process may exit with code 0. Handling this case is explicitly out of scope for this iteration. The detection helpers would still catch it if `returncode != 0`, but not if the CLI considers it a graceful exit. Document in code comments.
- **`retry-after` header**: The Claude CLI may honour `retry-after` automatically. The supervisor's cooldown is a backstop only; extracting the actual header value is out of scope.
- **Differentiated `rate_limit_count` per error type**: All postponable errors share the same counter. If the system needs to distinguish "how many times was this a 500 vs 529", that requires schema work beyond this issue.

---

## 8. Risk Mitigations

| Risk (from analysis) | Mitigation in this design |
|---------------------|--------------------------|
| 429 path must be preserved | `RateLimitError` is still raised for 429; moved under `RetryableError` so all callers that caught `RateLimitError` still work. The `_is_rate_limited_*` checks run first. |
| DB schema coupling (`rate_limit_count`) | Reuse same column; add only `postpone_cooldown_minutes` column via migration. |
| `main.py` fallback handler | Catch `RetryableError` (base class) instead of just `RateLimitError`. |
| Cooldown differentiation | Persisted per-row in `postpone_cooldown_minutes`; resume SQL uses per-row value. |
| Streaming caveat | Explicitly out of scope; documented in code comments. |
| Detection pattern precision | Mirror existing patterns; check both error-type string and status code string for redundancy. |
| `get_rate_limited_tasks()` / `resume_rate_limited_task()` | `get_postponed_tasks()` replaces callers; `resume_rate_limited_task()` is unchanged. |
| Test coverage gap | Test plan (stage 5) covers new classes, detection helpers, execute_claude routing, and executor retry path. |

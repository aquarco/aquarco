# Design: Switch Claude CLI to `--output-format stream-json`

**Issue**: #17
**Feature Pipeline Stage**: 1 (design)
**Date**: 2026-03-26

---

## Summary

Replace `--output-format json` (blocks until process exits, returns full stdout via `proc.communicate()`) with `--output-format stream-json` (NDJSON events emitted live to stdout). This enables real-time `live_output` streaming per event instead of polling the debug log every 10 seconds, while preserving all existing semantics for structured output extraction, inactivity detection, rate-limit handling, and the auto-resume loop.

---

## Background & Goals

With `--output-format json`, the Claude CLI accumulates all output and writes a single JSON array to stdout only after the process exits. Live output today is approximated by tailing `--debug-file` on a 10-second interval, which is crude and delayed.

With `--output-format stream-json`, each event (assistant turn, tool call, result) is emitted immediately as a JSON object on its own line (NDJSON). This allows:

1. True real-time `on_live_output` callbacks — one call per event, not one call every 10 s.
2. Inactivity detection without file-system polling — track the wall-clock time of the last received stdout event.
3. Simpler output parsing — find the `{type: "result"}` line, which carries the same fields as the `--output-format json` result message.

---

## Affected Files

| File | Nature of Change |
|------|-----------------|
| `supervisor/python/src/aquarco_supervisor/cli/claude.py` | Major rewrite of `execute_claude`, new helpers, remove `_tail_debug_log` and old `_monitor_for_inactivity` |
| `supervisor/python/src/aquarco_supervisor/pipeline/conditions.py` | Migrate `evaluate_ai_condition` to `stream-json` |
| `supervisor/python/tests/test_cli_claude.py` | Update `execute_claude` integration tests (mock stdout line stream, not `proc.communicate()`) |
| `supervisor/python/tests/test_cli_claude_extended.py` | No change needed (tests are for pure functions only) |
| `supervisor/python/tests/test_max_turns_cost.py` | No change needed (mocks `execute_claude` at the executor level) |

---

## Design Decisions

### D1 — Keep `--debug-file`

The `--debug-file` flag is retained for stderr/error diagnostics and as a belt-and-suspenders fallback for rate-limit detection. **Assumption**: `--debug-file` coexists with `--output-format stream-json`. If it turns out they conflict (and the process exits non-zero immediately), implementation must remove `--debug-file`; this assumption should be verified in the implementation step.

### D2 — `on_live_output` receives raw NDJSON lines

The callback is invoked once per non-empty line as events arrive on stdout. The line is the raw NDJSON string (the caller can parse it or store it verbatim). This gives true real-time visibility without batching delay.

### D3 — Remove `_tail_debug_log`

This function is replaced entirely by direct stdout event streaming in `_read_stream_json`. No callers outside `execute_claude` — safe to delete.

### D4 — Replace `_monitor_for_inactivity` with `_monitor_for_inactivity_stream`

The new monitor accepts a `last_event_time: list[float]` (a single-element mutable container updated by the stream reader on every event) and an `asyncio.Event` named `result_seen` (set when a `{type: "result"}` line is received). The logic is otherwise identical: once `result_seen` is set, if `last_event_time[0]` has not advanced for `inactivity_timeout` seconds, kill the process.

The old `_monitor_for_inactivity` (debug-log mtime based) is removed.

### D5 — `_parse_output` → `_parse_ndjson_output`

Replace `_parse_output(raw_output: str, ...)` with `_parse_ndjson_output(lines: list[str], ...)`. The new function:
1. Iterates `lines` and JSON-parses each.
2. Finds the first dict with `type == "result"`.
3. Delegates to the existing `_extract_from_result_message()` (unchanged).
4. Falls back to searching for the last assistant-message text blocks if no result line is found (same fallback logic as before).

`_parse_output` is removed; its callers inside `execute_claude` are updated.

### D6 — Rate-limit detection broadened

`_is_rate_limited` currently reads the debug log. Add a companion `_is_rate_limited_in_lines(lines: list[str]) -> bool` that checks the NDJSON stdout lines for `rate_limit_error` or `status code 429` strings. In `execute_claude`, check stdout lines first, then fall back to the debug log.

### D7 — `evaluate_ai_condition` migrated (conditions.py)

This is a short 1-turn call. Migration is straightforward: change `--output-format json` → `stream-json`, replace `proc.communicate()` with an async line-reader loop, then pass the collected lines to `_parse_ndjson_output` (imported from `cli/claude.py`). This avoids maintaining two execution paths.

### D8 — Resume branch unchanged

`--resume` args use `--output-format json` today. This must be changed to `--output-format stream-json` in the same commit that changes the main path.

---

## New Helper: `_read_stream_json`

```python
async def _read_stream_json(
    proc: asyncio.subprocess.Process,
    last_event_time: list[float],        # mutable; updated on each line
    result_seen: asyncio.Event,           # set when type=result line arrives
    on_live_output: Callable[[str], Awaitable[None]] | None,
) -> list[str]:
    """Read NDJSON events from proc.stdout line-by-line.

    Returns the list of non-empty line strings.
    Raises nothing — all errors are swallowed and logged.
    """
    lines: list[str] = []
    assert proc.stdout is not None
    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        lines.append(line)
        last_event_time[0] = asyncio.get_event_loop().time()
        try:
            evt = json.loads(line)
            if isinstance(evt, dict) and evt.get("type") == "result":
                result_seen.set()
        except json.JSONDecodeError:
            pass
        if on_live_output:
            try:
                await on_live_output(line)
            except Exception:
                pass
    return lines
```

---

## New Helper: `_monitor_for_inactivity_stream`

```python
async def _monitor_for_inactivity_stream(
    proc: asyncio.subprocess.Process,
    last_event_time: list[float],
    result_seen: asyncio.Event,
    *,
    inactivity_timeout: float = 90.0,
    poll_interval: float = 5.0,
    task_id: str = "",
    stage_num: int = 0,
) -> bool:
    """Monitor for inactivity after result event received via stdout.

    Polls every poll_interval seconds. Once result_seen is set,
    if no new events arrive for inactivity_timeout seconds, kills process.
    Returns True if killed for inactivity.
    """
    # Wait for result event (or process exit)
    while not result_seen.is_set() and proc.returncode is None:
        await asyncio.sleep(poll_interval)

    if proc.returncode is not None:
        return False

    stale_since: float | None = None

    while proc.returncode is None:
        await asyncio.sleep(poll_interval)
        if proc.returncode is not None:
            return False

        now = asyncio.get_event_loop().time()
        time_since_last = now - last_event_time[0]

        if time_since_last < inactivity_timeout:
            stale_since = None
        else:
            if stale_since is None:
                stale_since = now
            elif now - stale_since >= inactivity_timeout:
                log.warning(
                    "claude_killed_inactivity",
                    task_id=task_id,
                    stage=stage_num,
                    seconds_idle=inactivity_timeout,
                )
                proc.kill()
                return True

    return False
```

---

## Updated `execute_claude` Control Flow

```
1. Build args with --output-format stream-json (not json)
2. Create subprocess with stdout=PIPE (stderr=file as before)
3. Initialise shared state:
     last_event_time = [loop.time()]
     result_seen = asyncio.Event()
4. Create tasks:
     stream_task  = create_task(_read_stream_json(proc, last_event_time, result_seen, on_live_output))
     monitor_task = create_task(_monitor_for_inactivity_stream(proc, last_event_time, result_seen, ...))
5. await asyncio.wait({stream_task, monitor_task}, timeout=timeout_seconds, ...)
   — same timeout / kill logic as today
6. After both tasks done:
     lines = stream_task.result()   (or [] on cancel)
7. Check returncode:
     if non-zero:
       rate_limit = _is_rate_limited_in_lines(lines) or _is_rate_limited(debug_log)
       raise RateLimitError / AgentExecutionError as appropriate
8. structured = _parse_ndjson_output(lines, task_id, stage_num)
9. raw = "\n".join(lines)
10. return ClaudeOutput(structured=structured, raw=raw)
```

Key differences from today:
- No `proc.communicate()` — reading is done by `stream_task` which completes when stdout closes.
- No `_tail_debug_log` — `on_live_output` is called directly inside `_read_stream_json`.
- `killed_for_inactivity` branch: `stream_task` has already collected all lines; parse whatever was received.

---

## Updated `evaluate_ai_condition` Control Flow

```
1. Build args with --output-format stream-json (not json)
2. Create subprocess with stdout=PIPE, stderr=PIPE
3. Collect stdout lines via async readline loop (inline, no shared-state needed — single turn, no inactivity)
4. await asyncio.wait_for(collect_task, timeout=timeout_seconds)
5. Parse: _parse_ndjson_output(lines, task_id, stage_num)
6. Extract answer boolean from structured output (same logic as today)
```

Because `evaluate_ai_condition` is a 1-turn call with no inactivity concern, a simple inline `async for raw_line in proc.stdout` loop inside a single coroutine suffices — no need for the full `_read_stream_json` machinery with shared state.

---

## Test Strategy

### `test_cli_claude.py` — `execute_claude` integration tests

Tests that mock `proc.communicate()` returning a JSON blob must be rewritten to mock an async stdout stream. Pattern:

```python
async def _make_fake_stdout(lines: list[str]):
    for line in lines:
        yield (line + "\n").encode()

# In test:
mock_process.stdout = _make_fake_stdout([
    json.dumps({"type": "system", "subtype": "init"}),
    json.dumps({"type": "result", "subtype": "success",
                "structured_output": {"key": "val"},
                "total_cost_usd": 0.1, ...}),
])
mock_process.returncode = 0
mock_process.wait = AsyncMock(return_value=None)
```

Tests for `AgentTimeoutError`, `AgentInactivityError`, and rate-limit detection remain; mocks adapt to the new stream pattern.

### `test_cli_claude_extended.py` — No changes needed

All tests target pure functions (`_parse_output`, `_extract_from_result_message`, `_find_result_message`, `_extract_json`, `ClaudeOutput`). These are unchanged or their replacements have identical signatures.

**Exception**: If `_parse_output` is removed, the `test_parse_output_*` tests must be updated to call `_parse_ndjson_output` with the appropriate list-of-lines input format.

### `test_max_turns_cost.py` — No changes needed

All tests mock `execute_claude` at the executor level. The underlying streaming mechanism is opaque.

---

## Assumptions

1. `--debug-file` and `--output-format stream-json` are compatible flags in the current Claude CLI version. If not, remove `--debug-file` from args and rely solely on stdout for rate-limit detection and diagnostics.
2. The `{type: "result"}` event in `stream-json` format contains exactly the same fields as the result message in `--output-format json` (`structured_output`, `result`, `total_cost_usd`, `usage`, `duration_ms`, `num_turns`, `session_id`, `subtype`). If the schema differs, `_extract_from_result_message` must be updated.
3. Rate-limit errors will appear either as a non-zero exit code with `rate_limit_error` in the debug log or as a `{type: "result", subtype: "error_api_error"}` line on stdout containing the rate-limit string. The broadened detection covers both.

---

## Acceptance Criteria

See structured output below.

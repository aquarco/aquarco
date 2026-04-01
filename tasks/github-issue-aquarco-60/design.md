# Design: Set Model Per Agent (Issue #60)

## Summary

Add a `model` field to agent definition schemas, YAML definitions, and the full
execution chain so each agent can specify which Claude model to use via the
`--model` CLI flag. When `model` is not set, the CLI uses its default model
(backward compatible).

## Architecture

The change flows through six layers, bottom-up:

```
Schema → YAML definitions → Registry accessor → Config overlay accessor → Executor → CLI wrapper
```

Each layer is independently testable. The config overlay's 3-layer merge
(default → global → per-repo) automatically supports model overrides once the
field exists — no special merge logic needed.

---

## 1. JSON Schema Changes

### `config/schemas/pipeline-agent-v1.json`

Add `model` as an optional string field inside `spec.properties`, alongside
existing fields like `categories`, `priority`, etc.:

```json
"model": {
  "type": "string",
  "description": "Claude model to use for this agent (e.g. claude-sonnet-4-6). If omitted, the CLI default is used."
}
```

**Location**: Inside `properties.spec.properties`, as a sibling of `categories`.

### `config/schemas/system-agent-v1.json`

Identical addition inside `properties.spec.properties`, as a sibling of `role`.

**Note**: Because both schemas use `"additionalProperties": false` on the `spec`
object, this schema change MUST be deployed before any YAML files reference the
`model` field, or validation will reject them.

---

## 2. YAML Agent Definition Changes

Add `model` to `spec` in each agent definition. Recommended model assignments:

| Agent | Model | Rationale |
|-------|-------|-----------|
| `implementation-agent` | `claude-sonnet-4-6` | Complex code generation needs strong reasoning |
| `design-agent` | `claude-sonnet-4-6` | Architecture design needs strong reasoning |
| `analyze-agent` | `claude-sonnet-4-6` | Analysis needs good reasoning at moderate cost |
| `review-agent` | `claude-sonnet-4-6` | Code review needs good reasoning |
| `test-agent` | `claude-sonnet-4-6` | Test generation needs good reasoning |
| `docs-agent` | `claude-sonnet-4-6` | Documentation is simpler |
| `planner-agent` | `claude-sonnet-4-6` | Planning needs good reasoning |
| `condition-evaluator-agent` | `claude-haiku-4-5` | Simple yes/no evaluations |
| `repo-descriptor-agent` | `claude-haiku-4-5` | Simple repo analysis |

Example placement in `implementation-agent.yaml`:

```yaml
spec:
  model: "claude-sonnet-4-6"       # ← NEW: added as first field in spec
  categories:
    - implementation
  priority: 10
  ...
```

Example placement in `planner-agent.yaml` (system agent):

```yaml
spec:
  model: "claude-sonnet-4-6"       # ← NEW
  role: planner
  promptFile: planner-agent.md
  ...
```

---

## 3. Agent Registry (`agent_registry.py`)

Add a new accessor method following the exact pattern of `get_agent_timeout()`:

```python
def get_agent_model(self, agent_name: str) -> str | None:
    """Get the Claude model for an agent, or None to use CLI default."""
    spec = self._agents.get(agent_name, {})
    return spec.get("model")
```

**Pattern**: Unlike `get_agent_timeout` which reads from `spec.resources`,
`model` lives directly on `spec` (same level as `categories`, `role`,
`promptFile`). This matches the YAML structure and schema.

---

## 4. Config Overlay (`config_overlay.py`)

Add `get_agent_model()` to `ScopedAgentView`, following the same dual-lookup
pattern used by other accessors (handles both flat spec and nested `spec.spec`
from overlay merge):

```python
def get_agent_model(self, agent_name: str) -> str | None:
    spec = self._resolved.agents.get(agent_name, {})
    s = spec.get("spec", spec)
    return s.get("model")
```

**Pattern**: Identical to how `get_agent_timeout` accesses `s.get("resources", {}).get(...)`,
except `model` is a top-level spec field so it's just `s.get("model")`.

---

## 5. Executor (`executor.py`)

In `_execute_agent()`, resolve the model and pass it to `execute_claude()`.

After line ~1061 (where `max_turns` is resolved), add:

```python
model = cfg.get_agent_model(agent_name)
```

Then in the `execute_claude()` call (~line 1082), add the `model` parameter:

```python
claude_output = await execute_claude(
    prompt_file=prompt_file,
    context=agent_context,
    work_dir=clone_dir,
    timeout_seconds=timeout_minutes * 60,
    allowed_tools=cfg.get_allowed_tools(agent_name),
    denied_tools=cfg.get_denied_tools(agent_name),
    task_id=task_id,
    stage_num=stage_num,
    extra_env=cfg.get_agent_environment(agent_name),
    output_schema=...,
    max_turns=max_turns,
    model=model,                    # ← NEW
    resume_session_id=resume_session_id,
    on_live_output=on_live_output,
)
```

Also update the condition-evaluator invocation (~line 562) to pass model
if it resolves one for the condition-evaluator agent.

---

## 6. Claude CLI Wrapper (`cli/claude.py`)

Add an optional `model` parameter to `execute_claude()`:

```python
async def execute_claude(
    prompt_file: Path,
    context: dict[str, Any],
    work_dir: str,
    timeout_seconds: int = 1800,
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    task_id: str = "",
    stage_num: int = 0,
    extra_env: dict[str, str] | None = None,
    output_schema: dict[str, Any] | None = None,
    max_turns: int = 30,
    model: str | None = None,        # ← NEW
    resume_session_id: str | None = None,
    on_live_output: Callable[[str], Awaitable[None]] | None = None,
) -> ClaudeOutput:
```

In the args construction, add `--model` for both fresh and resume paths:

```python
# For fresh invocations (after the base args, before tools):
if model:
    args.extend(["--model", model])

# For resume invocations (after --resume and base args):
if model:
    args.extend(["--model", model])
```

**Placement**: Add the `--model` flag right after `--verbose` and before
tool-related flags. The flag should be added in BOTH the resume and fresh
code paths since a resumed session might need to specify the model too.

**Backward compatibility**: When `model` is `None`, no `--model` flag is added,
preserving existing behavior.

---

## 7. Test Changes

### `tests/test_cli_claude.py`

Add tests:
- `test_execute_claude_with_model` — verify `--model <value>` appears in args when model is provided
- `test_execute_claude_without_model` — verify no `--model` flag when model is None
- `test_execute_claude_resume_with_model` — verify `--model` in resume path

### `tests/test_pipeline/test_agent_registry.py`

Add tests:
- `test_get_agent_model_returns_value` — agent with model set returns it
- `test_get_agent_model_returns_none` — agent without model returns None

### `tests/test_config_overlay.py`

Add tests:
- `test_scoped_view_get_agent_model` — model resolved through overlay
- `test_scoped_view_get_agent_model_none` — returns None when not set
- `test_repo_overlay_overrides_model` — repo-level model overrides default

---

## Assumptions

1. **Model string is not validated at config load time.** Invalid model names
   will fail at Claude CLI invocation time. This matches the existing pattern
   for other string fields like `promptFile`. A future enhancement could add
   an `enum` or `pattern` constraint to the schema.

2. **The `--model` flag is supported by Claude CLI** in both fresh and `--resume`
   invocation modes.

3. **Autoloaded agents** (from DB via `agent_autoloader.py`) will automatically
   support the model field because they store the full `spec` dict — no code
   change needed in the autoloader itself.

4. **The spending system** (`spending.py`) already detects the model from CLI
   output post-hoc, so cost tracking works automatically without changes.

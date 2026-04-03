# Design: Merge Agent Definition and Prompt File (Issue #69)

## Overview

This document describes the technical design for merging each agent's Kubernetes-style YAML
definition file and separate Markdown prompt file into a single hybrid `.md` file using
YAML frontmatter.

**Before** (two files per agent):
- `config/agents/definitions/pipeline/analyze-agent.yaml` — Kubernetes-style `apiVersion/kind/metadata/spec`
- `config/agents/prompts/analyze-agent.md` — plain Markdown prompt

**After** (one file per agent):
- `config/agents/definitions/pipeline/analyze-agent.md` — YAML frontmatter + Markdown prompt

---

## New File Format

```
---
<flat YAML frontmatter>
---
<Markdown prompt body>
```

### Frontmatter Schema — Pipeline Agent

```yaml
---
name: analyze-agent
version: "1.0.0"
description: "Triages issues, analyzes codebase, determines required work and complexity"

model: sonnet

categories:
  - analyze

priority: 1

tools:
  allowed:
    - Read
    - Grep
    - Glob
    - Bash
  denied:
    - Write
    - Edit

resources:
  maxTokens: 50000
  timeoutMinutes: 15
  maxConcurrent: 3
  maxTurns: 20
  maxCost: 1.0

environment:
  AGENT_MODE: "analyze"
  STRICT_MODE: "true"

healthCheck:
  enabled: true
  intervalSeconds: 300
---
# Analyze Agent — System Prompt

...prompt body here...
```

### Frontmatter Schema — System Agent

Same as pipeline agent but uses `role` instead of `categories`/`priority`:

```yaml
---
name: planner-agent
version: "1.0.0"
description: "Analyzes codebase and request to assign agents to pipeline categories"

model: sonnet

role: planner

tools:
  allowed:
    - Read
    - Grep
    - Glob
    - Bash
  denied:
    - Write
    - Edit

resources:
  maxTokens: 100000
  timeoutMinutes: 20
  maxConcurrent: 2
  maxTurns: 20
  maxCost: 2.0

environment:
  AGENT_MODE: "planning"
  STRICT_MODE: "true"

healthCheck:
  enabled: true
  intervalSeconds: 300
---
# Planner Agent — System Prompt

...prompt body here...
```

---

## Design Decisions

### Decision 1: Tools format — keep `allowed`/`denied` dict (not flat list)

The issue example shows a flat `tools` list. However, the current agents use `tools.denied` to
explicitly block tools (e.g., `Write`, `Edit` for the analyze-agent). The supervisor passes
`--disallowedTools` to the Claude CLI subprocess, which enforces this at the API level. Dropping
the denied list would silently grant agents access to tools they should not have.

**Decision**: Retain the `tools.allowed`/`tools.denied` dict structure in the frontmatter.
The flat list in the issue is treated as a simplified illustration, not a spec requirement.

### Decision 2: Directory structure — preserve `system/` and `pipeline/` subdirs

`AgentRegistry._discover_agents()` infers the `_group` tag (system vs pipeline) from which
subdirectory a file lives in. This is a clean separation. Flattening would require adding a
discriminator field to the frontmatter (e.g., `group: pipeline`), adding complexity.

**Decision**: Keep `config/agents/definitions/system/` and `config/agents/definitions/pipeline/`
as subdirectories. All files in `system/` become `.md`; all files in `pipeline/` become `.md`.

### Decision 3: Prompt file resolution — return `.md` file path as-is

Currently `AgentRegistry.get_agent_prompt_file()` resolves a `promptFile` key relative to the
definition file's parent. With merged files, the prompt is embedded in the definition file itself.

**Decision**:
- `get_agent_prompt_file()` returns the `.md` file path when no `promptFile` key exists.
  Callers that read the file must strip frontmatter before use.
- Add new `get_agent_prompt_content(agent_name) -> str | None` method that returns the extracted
  prompt body (everything after the closing `---`). Callers that need the prompt text directly
  should use this method.
- Update `ScopedAgentView.get_agent_prompt_file()` in `config_overlay.py` to follow the same
  pattern for `.md` definition files.

### Decision 4: Parsing — manual `---` split using PyYAML (no new dependency)

`pyyaml` is already in `pyproject.toml`. A simple `str.startswith("---\n")` + `str.find("\n---\n")`
split is sufficient. We do NOT use `python-frontmatter` to avoid a new dependency.

Edge case: if the prompt body itself contains `---` on a line, this is handled correctly because
we only look for the first occurrence of `\n---\n` after the opening delimiter.

---

## Affected Files

### Source files to update

| File | Change |
|------|--------|
| `supervisor/python/src/aquarco_supervisor/pipeline/agent_registry.py` | Add `_parse_md_agent_file()`, update `_discover_agents_from_dir()` and flat scan glob, update `get_agent_prompt_file()`, add `get_agent_prompt_content()` |
| `supervisor/python/src/aquarco_supervisor/config_overlay.py` | Update `ScopedAgentView.get_agent_prompt_file()` for hybrid `.md` files, add `get_agent_prompt_content()` |
| `supervisor/python/src/aquarco_supervisor/cli/agents.py` | Change glob `*.yaml` → `*.md`, rewrite `validate_definition()` for flat frontmatter, update `discover` command |
| `supervisor/python/src/aquarco_supervisor/cli/status.py` | Change `agents_path.glob("*.yaml")` → `"*.md"` on line 122 |
| `config/schemas/pipeline-agent-v1.json` | Rewrite for flat frontmatter (remove apiVersion/kind/metadata/spec wrapper) |
| `config/schemas/system-agent-v1.json` | Rewrite for flat frontmatter |
| `config/schemas/agent-definition-v1.json` | Rewrite as oneOf[pipeline, system] flat schema |
| `supervisor/python/tests/test_pipeline/test_agent_registry.py` | Rewrite YAML-based discovery fixtures to use hybrid `.md` format; add new frontmatter parsing tests |

### Files to create (9 hybrid .md files)

| New file | Source definition | Source prompt |
|----------|-------------------|---------------|
| `config/agents/definitions/pipeline/analyze-agent.md` | `definitions/pipeline/analyze-agent.yaml` | `prompts/analyze-agent.md` |
| `config/agents/definitions/pipeline/design-agent.md` | `definitions/pipeline/design-agent.yaml` | `prompts/design-agent.md` |
| `config/agents/definitions/pipeline/docs-agent.md` | `definitions/pipeline/docs-agent.yaml` | `prompts/docs-agent.md` |
| `config/agents/definitions/pipeline/implementation-agent.md` | `definitions/pipeline/implementation-agent.yaml` | `prompts/implementation-agent.md` |
| `config/agents/definitions/pipeline/review-agent.md` | `definitions/pipeline/review-agent.yaml` | `prompts/review-agent.md` |
| `config/agents/definitions/pipeline/test-agent.md` | `definitions/pipeline/test-agent.yaml` | `prompts/test-agent.md` |
| `config/agents/definitions/system/condition-evaluator-agent.md` | `definitions/system/condition-evaluator-agent.yaml` | `prompts/condition-evaluator-agent.md` |
| `config/agents/definitions/system/planner-agent.md` | `definitions/system/planner-agent.yaml` | `prompts/planner-agent.md` |
| `config/agents/definitions/system/repo-descriptor-agent.md` | `definitions/system/repo-descriptor-agent.yaml` | `prompts/repo-descriptor-agent.md` |

### Files to delete

- `config/agents/definitions/pipeline/*.yaml` (6 files)
- `config/agents/definitions/system/*.yaml` (3 files)
- `config/agents/prompts/*.md` (9 files)
- `config/agents/prompts/` directory (now empty)

---

## Detailed Implementation Spec

### `_parse_md_agent_file(path: Path) -> tuple[dict, str]`

New module-level function in `agent_registry.py`:

```python
def _parse_md_agent_file(path: Path) -> tuple[dict, str]:
    """Parse a hybrid markdown agent file with YAML frontmatter.

    Returns (frontmatter_dict, prompt_body).
    Raises ValueError if frontmatter delimiters are missing or YAML is invalid.
    """
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        raise ValueError(f"Agent file {path} is missing opening '---' frontmatter delimiter")
    end = content.find("\n---\n", 4)
    if end == -1:
        raise ValueError(f"Agent file {path} is missing closing '---' frontmatter delimiter")
    yaml_text = content[4:end]
    prompt_body = content[end + 5:]  # skip the '\n---\n' separator
    frontmatter = yaml.safe_load(yaml_text)
    if not isinstance(frontmatter, dict):
        raise ValueError(f"Agent file {path}: frontmatter must be a YAML mapping, got {type(frontmatter)}")
    return frontmatter, prompt_body
```

### `_discover_agents_from_dir` — updated

```python
def _discover_agents_from_dir(self, directory: Path, group: str) -> None:
    """Scan a single directory for hybrid .md agent files."""
    for md_file in sorted(directory.glob("*.md")):
        try:
            frontmatter, _prompt_body = _parse_md_agent_file(md_file)
            name: str = frontmatter.get("name", md_file.stem)
            # Build spec: all frontmatter keys become top-level spec fields.
            # Internal tracking keys are prefixed with '_'.
            spec: dict[str, Any] = dict(frontmatter)
            spec["name"] = name
            spec["_group"] = group
            spec["_definition_file"] = str(md_file)
            self._agents[name] = spec
        except (ValueError, yaml.YAMLError) as exc:
            log.warning("agent_md_parse_error", file=str(md_file), error=str(exc))
```

Key changes from the old Kubernetes-style parser:
- Glob changes from `*.yaml` to `*.md`
- No `kind == "AgentDefinition"` guard (frontmatter format replaces it)
- `name` comes from `frontmatter["name"]` (not `metadata.name`)
- `spec` is the whole frontmatter dict (no `raw.get("spec", raw)` unwrapping)
- All existing spec accessors (`spec.get("categories")`, `spec.get("model")`, etc.) continue to
  work unchanged because frontmatter keys are now at the top level of the spec dict

Apply the same changes to the flat-scan fallback path in `_discover_agents()`.

### `get_agent_prompt_file` — updated

```python
def get_agent_prompt_file(self, agent_name: str) -> Path:
    spec = self._agents.get(agent_name, {})
    definition_file = spec.get("_definition_file")

    # Hybrid .md file: prompt is embedded; return the definition file itself.
    # Callers must call get_agent_prompt_content() to obtain the stripped body,
    # or strip frontmatter themselves when reading the returned path.
    if definition_file and definition_file.endswith(".md") and not spec.get("promptFile"):
        return Path(definition_file)

    # Legacy path: resolve promptFile relative to definition dir
    prompt_file: str = spec.get("promptFile", f"{agent_name}.md")
    if definition_file:
        base_dir = Path(definition_file).parent
    else:
        base_dir = self._agents_dir
    resolved = (base_dir / prompt_file).resolve()
    config_root = self._agents_dir.parent.resolve()
    if not resolved.is_relative_to(config_root):
        raise AgentRegistryError(f"Prompt file path escapes config directory: {prompt_file}")
    return resolved
```

### `get_agent_prompt_content` — new method

```python
def get_agent_prompt_content(self, agent_name: str) -> str | None:
    """Return the embedded prompt body for a hybrid .md agent file, or None.

    Returns None for agents loaded from JSON registry or DB (no embedded prompt).
    """
    spec = self._agents.get(agent_name, {})
    definition_file = spec.get("_definition_file", "")
    if definition_file and definition_file.endswith(".md"):
        try:
            _, prompt_body = _parse_md_agent_file(Path(definition_file))
            return prompt_body.strip() or None
        except (ValueError, OSError, yaml.YAMLError):
            return None
    return None
```

### `ScopedAgentView.get_agent_prompt_file` — updated in `config_overlay.py`

Add a new branch before the existing `promptFile` resolution:

```python
# Hybrid .md definition file: prompt is embedded; return file path directly
if definition_file and definition_file.endswith(".md"):
    if not (spec.get("promptFile") or spec.get("spec", {}).get("promptFile")):
        return Path(definition_file)
```

### `ScopedAgentView.get_agent_prompt_content` — new method in `config_overlay.py`

```python
def get_agent_prompt_content(self, agent_name: str) -> str | None:
    """Return embedded prompt body from hybrid .md file, or None."""
    from .pipeline.agent_registry import _parse_md_agent_file  # noqa: PLC0415
    spec = self._resolved.agents.get(agent_name, {})
    definition_file = spec.get("_definition_file", "")
    if definition_file and definition_file.endswith(".md"):
        try:
            _, body = _parse_md_agent_file(Path(definition_file))
            return body.strip() or None
        except Exception:
            return None
    return None
```

### `cli/agents.py` — `validate_definition` rewrite

Replace the Kubernetes-style validation with flat frontmatter validation:

1. Parse the `.md` file using `_parse_md_agent_file()` (import or inline the parsing logic)
2. Validate required fields: `name` (kebab-case), `version` (semver), `description` (≥10 chars)
3. Validate `categories` OR `role` is present (not both, not neither)
4. Validate `categories` values against `VALID_CATEGORIES` enum
5. Validate `role` against `VALID_ROLES = {"planner", "condition-evaluator", "repo-descriptor"}`
6. Validate optional `priority` (int, 1-100)
7. Validate prompt body is present and non-empty (after stripping frontmatter)
8. Remove `apiVersion`/`kind` checks entirely
9. Remove `promptFile` existence check (prompt is now embedded)
10. Change `discover` command glob: `definitions_dir.glob("*.yaml")` → `definitions_dir.rglob("*.md")` (recursive to handle `system/` and `pipeline/` subdirs)

Update the normalized registry record:
- Remove `promptFile` field
- Add `definitionFile` pointing to the `.md` file (for reference)

### `cli/status.py` — line 122

Change:
```python
agent_count = sum(1 for _ in agents_path.glob("*.yaml"))
```
To:
```python
agent_count = sum(1 for _ in agents_path.rglob("*.md"))
```

### JSON Schema changes

All three schema files drop the Kubernetes envelope. The new schemas use `type: object` at
the top level with flat required fields.

**`pipeline-agent-v1.json`** required: `["name", "version", "description", "categories"]`
**`system-agent-v1.json`** required: `["name", "version", "description", "role"]`
**`agent-definition-v1.json`** becomes a `oneOf` combining the two schemas via the discriminator
field (`categories` presence → pipeline, `role` presence → system).

The `categories` enum remains: `["review", "implement", "test", "design", "document", "analyze"]`
The `role` enum remains: `["planner", "condition-evaluator", "repo-descriptor"]`

---

## Test Changes

### `test_agent_registry.py`

The key fixture to update is `test_discover_agents_from_system_and_pipeline_subdirs` (line 422).
Change it to write `.md` files with frontmatter:

```python
system_content = """\
---
name: planner-agent
version: "1.0.0"
description: "Plans pipeline stages for incoming tasks"
model: sonnet
role: planner
resources:
  maxConcurrent: 1
---
# Planner Agent
"""
pipeline_content = """\
---
name: analyze-agent
version: "1.0.0"
description: "Triages issues and analyzes codebases"
model: sonnet
categories:
  - analyze
priority: 10
---
# Analyze Agent
"""
(system_dir / "planner-agent.md").write_text(system_content)
(pipeline_dir / "analyze-agent.md").write_text(pipeline_content)
```

### New tests to add

```python
def test_parse_md_agent_file_valid(tmp_path):
    """Valid hybrid .md file returns correct frontmatter and body."""
    ...

def test_parse_md_agent_file_missing_opening_delimiter(tmp_path):
    """File without opening --- raises ValueError."""
    ...

def test_parse_md_agent_file_missing_closing_delimiter(tmp_path):
    """File with opening --- but no closing --- raises ValueError."""
    ...

def test_get_agent_prompt_content_returns_body(tmp_path):
    """get_agent_prompt_content() returns prompt body from hybrid .md file."""
    ...

def test_get_agent_prompt_content_returns_none_for_json_agent(tmp_path):
    """get_agent_prompt_content() returns None for agents loaded from JSON registry."""
    ...

def test_get_agent_prompt_file_returns_md_path_for_hybrid_agent(tmp_path):
    """get_agent_prompt_file() returns the .md definition path when no promptFile key."""
    ...
```

---

## Migration Steps

For each of the 9 agents, the merged file is produced by:
1. Extracting `metadata.name`, `metadata.version`, `metadata.description` → top-level frontmatter keys
2. Lifting `spec.*` keys to top-level (drop `spec` wrapper)
3. Removing `apiVersion`, `kind`, `metadata.labels` (optional, can be kept as comments)
4. Removing `promptFile` key (prompt is now inline)
5. Appending the prompt body from the corresponding `prompts/*.md` file

---

## Backward Compatibility

- **Agents loaded from JSON registry**: Not affected. `get_agent_prompt_file()` falls back to the
  legacy `promptFile`-based resolution for any agent without a `_definition_file` ending in `.md`.
- **Agents stored in the database**: Not affected. DB-loaded agents use `promptInline` or the
  existing spec dict; `_definition_file` is not set.
- **`ScopedAgentView` overlay agents**: The `_config_base` resolution path is preserved for
  overlay agents defined in `.aquarco.yaml`.
- **The `agents discover` CLI command**: Will no longer produce a `promptFile` field in the
  registry JSON. Any consumers of that field must be updated.

---

## Assumptions

1. The `config/agents/definitions/` directory currently contains only the 9 known agent YAML files.
   No other YAML files will be orphaned by the migration.
2. No external tooling outside this repository depends on the Kubernetes-style YAML format.
3. `python-frontmatter` library is NOT required; we use PyYAML directly.
4. The `tools.allowed`/`tools.denied` dict is retained (the flat list in the issue example is
   treated as illustrative only).
5. The executor/CLI that reads prompt files will be updated to call `get_agent_prompt_content()`
   when available; this design doc does not cover `cli/claude.py` caller updates because the
   analysis did not surface that file's internals. If `claude.py` reads the prompt file content
   directly via `Path.read_text()`, it must strip frontmatter (e.g., skip everything up to the
   second `---` line).

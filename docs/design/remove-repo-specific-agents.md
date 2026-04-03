# Design: Remove Repository-Specific Agent Autoloading (Issue #79)

**Date:** 2026-04-03  
**Status:** Approved  
**Author:** design-agent

---

## Overview

This document describes the complete removal of the repository-specific agent autoloading subsystem from the Aquarco supervisor. The autoloader scans cloned repositories for `.claude/agents/*.md` files, heuristically generates agent definitions, persists them in the `agent_definitions` table, and merges them as a 4th config layer at runtime. The feature is being removed in favour of a simpler approach where global pipeline agents read `.claude/agents/` files at runtime and delegate inline.

**Assumption A1:** `executor.py` has no direct references to autoloading (confirmed by analysis agent — do not touch it).  
**Assumption A2:** The `source` and `agent_group` columns on `agent_definitions` are used for non-autoload purposes and must NOT be removed.  
**Assumption A3:** Migration 028 may already be applied in the target database. Therefore the correct approach is a new forward migration (036) to DROP the table rather than deleting or modifying 028.

---

## Files to DELETE Entirely

| File | Reason |
|------|--------|
| `supervisor/python/src/aquarco_supervisor/agent_autoloader.py` | Core autoloading module (~477 lines). All callers are cleaned in subsequent steps. |
| `supervisor/python/tests/test_agent_autoload.py` | Dedicated tests for deleted module. |
| `supervisor/python/tests/test_config_store_autoload.py` | Dedicated tests for autoload functions in config_store. |
| `supervisor/python/tests/test_config_overlay_autoload.py` | Dedicated tests for the autoloaded layer in config_overlay. |
| `config/agents/definitions/system/repo-descriptor-agent.yaml` | System agent intended to replace the heuristics being removed; not yet wired in and no longer needed. |

**Decision on `028_repo_agent_scans.rollback.sql`:** This file is safe to delete since a new forward migration (036) will perform the drop. However, deleting it is optional; keeping it does not break anything. The implementation agent should delete it for cleanliness.

---

## Files to EDIT Surgically

### 1. `supervisor/python/src/aquarco_supervisor/models.py`

**Remove:** `RepoAgentScanStatus` enum and `RepoAgentScan` Pydantic model.  
These are only imported by `agent_autoloader.py` (being deleted) and `test_agent_autoload.py` (being deleted). No other module imports them.

**Exact removals:**
- The `RepoAgentScanStatus` enum block (6 lines, values: `PENDING`, `SCANNING`, `ANALYZING`, `WRITING`, `COMPLETED`, `FAILED`).
- The `RepoAgentScan` model block (9 lines, fields: `id`, `repo_name`, `status`, `agents_found`, `agents_created`, `error_message`, `started_at`, `completed_at`, `created_at`).

---

### 2. `supervisor/python/src/aquarco_supervisor/config_store.py`

**Remove:** The entire "Autoloaded agents helpers" section (lines ~363–419), which contains:
- Section comment: `# Autoloaded agents helpers`
- `deactivate_autoloaded_agents(db, repo_name) -> int` function (Updates `agent_definitions` SET `is_active = false` WHERE `source = 'autoload:<repo_name>'`)
- `read_autoloaded_agents_from_db(db, repo_name) -> list[dict[str, Any]]` function (dead code — only referenced in the deleted test file)

Nothing else in `config_store.py` references autoloading. All other functions (`sync_all_agent_definitions_to_db`, `sync_pipeline_definitions_to_db`, etc.) are unrelated and must not be touched.

---

### 3. `supervisor/python/src/aquarco_supervisor/config_overlay.py`

**Remove from `resolve_config()` function:**

1. The `autoloaded_agents: list[dict[str, Any]] | None = None` parameter from the function signature (line ~103).
2. Update the function docstring: change "Apply config layers: default -> global -> per-repo -> autoloaded." to "Apply config layers: default -> global -> per-repo." Remove mention of the 4th autoloaded layer.
3. Remove the "Layer 4" block (3 lines):
   ```python
   # Layer 4: autoloaded agents (always EXTEND strategy)
   if autoloaded_agents:
       agents = merge_agents(agents, autoloaded_agents, MergeStrategy.EXTEND)
   ```

**No changes needed** to `ScopedAgentView`, `merge_agents`, `merge_pipelines`, or `load_overlay`.

**Caller check:** The only caller of `resolve_config()` that passes `autoloaded_agents` is `pipeline/agent_registry.py` (via `_load_autoloaded_agents()`). That call site is also being removed. No external callers pass this parameter.

---

### 4. `supervisor/python/src/aquarco_supervisor/pipeline/agent_registry.py`

**Remove from `load()` method:**
```python
# Load autoloaded agents from DB
await self._load_autoloaded_agents()
```
(2 lines, at approximately lines 50–51 in the file)

**Remove the entire `_load_autoloaded_agents()` method** (~13 lines):
```python
async def _load_autoloaded_agents(self) -> None:
    """Load autoloaded agents from the database for all registered repositories."""
    try:
        rows = await self._db.fetch_all(
            """SELECT ad.name, ad.spec, ad.source
               FROM agent_definitions ad
               WHERE ad.is_active = true
                 AND ad.source LIKE 'autoload:%%'"""
        )
        for row in rows:
            name = row["name"]
            spec = row["spec"] if isinstance(row["spec"], dict) else json.loads(row["spec"])
            spec["name"] = name
            spec["_group"] = "pipeline"
            self._agents[name] = spec
        if rows:
            log.info("autoloaded_agents_loaded", count=len(rows))
```

After removal, `load()` ends with:
```python
        await self._sync_agent_instances()
        log.info("registry_loaded", agent_count=len(self._agents))
```

---

### 5. `supervisor/python/src/aquarco_supervisor/main.py`

**a) Remove import line (~line 20):**
```python
from .agent_autoloader import autoload_repo_agents, create_scan_record, has_claude_agents
```

**b) Remove main-loop calls and their surrounding comments (~lines 158–164):**

Remove these lines from the main loop:
```python
                # Clone pending repos (and auto-scan for agents)
                if self._clone_worker:
                    await self._clone_worker.clone_pending_repos()
                    await self._auto_scan_new_repos()

                # Process IPC agent scan commands
                await self._process_agent_scan_commands()
```

Replace with the clean version (keeping clone logic, removing scan calls):
```python
                # Clone pending repos
                if self._clone_worker:
                    await self._clone_worker.clone_pending_repos()
```

**c) Remove `_auto_scan_new_repos()` method** (~lines 497–518, approximately 22 lines).

**d) Remove `_process_agent_scan_commands()` method** (~lines 519–557, approximately 38 lines including the inline `import json` inside the method body).

---

### 6. `supervisor/python/tests/test_category_rename_consistency.py`

**Remove:**
- Import block (lines 23–26):
  ```python
  from aquarco_supervisor.agent_autoloader import (
      analyze_agent_prompt,
      generate_agent_definition,
  )
  ```
- All test functions and classes that call `analyze_agent_prompt()` or `generate_agent_definition()`. These are the tests that validated the autoloader used new canonical category names (`document`, `implement` instead of `docs`, `implementation`).

**Keep:**
- The `CANONICAL_CATEGORIES` / `OLD_CATEGORY_NAMES` constants.
- Tests that check schema files (pipeline-agent-v1.json, agent-definition-v1.json), `VALID_CATEGORIES` in `cli/agents.py`, and `pipelines.yaml` — none of these depend on the autoloader.
- The `from aquarco_supervisor.cli.agents import VALID_CATEGORIES` import (line 27).

---

### 7. `supervisor/python/tests/test_category_rename_extended.py`

**Remove:**
- Import line 20: `from aquarco_supervisor.agent_autoloader import analyze_agent_prompt`
- `TestGenerateAgentDefinitionStructure` class (lines ~260–290) — all 4 tests import `generate_agent_definition` from the deleted module.
- Any other test methods that call `analyze_agent_prompt()` (check for `_infer_category` edge-case tests that delegate through `analyze_agent_prompt`).

**Keep:**
- Import of `validate_definition` from `cli.agents` (line 21) — unrelated to autoloading.
- Tests that validate CLI `validate_definition` rejects old category names.
- Tests that check `AGENT_MODE` env vars divergence in agent YAML files.
- Tests that validate pipeline stage category references in `pipelines.yaml`.

---

### 8. `supervisor/python/tests/test_pipeline/test_agent_registry.py`

**Remove:**
- `test_autoloaded_agents_tagged_as_pipeline` test function (lines ~458–476). This test creates a mock DB returning `source: autoload:my-repo` data and verifies the removed `_load_autoloaded_agents()` behaviour.

**Keep:**
- `test_get_all_agent_definitions_json_db_path_includes_group` (lines ~484–510) — this tests `get_all_agent_definitions_json()`, a separate general function unrelated to autoloading.
- All other tests in the file.

**Note:** After removing `_load_autoloaded_agents()`, any fixture that sets `mock_db.fetch_all.return_value` specifically for the autoload query `WHERE source LIKE 'autoload:%%'` should have that side-effect entry removed. The `_sync_agent_instances` call that remains in `load()` may still use `fetch_all`; check that existing mock setups remain valid.

---

### 9. `supervisor/python/tests/test_push_review_coverage.py`

**Remove:**
- `test_autoloaded_agent_model_from_db` test function (lines ~466–~495). This test sets up an agent with `source: autoload:repo1` via `mock_db.fetch_all` and verifies `get_agent_model()` returns the model. After `_load_autoloaded_agents()` is removed, autoloaded agents never enter the registry, making this test both stale and misleading.

**Keep:**
- All other tests in the file.

---

## New File to CREATE

### `db/migrations/036_drop_repo_agent_scans.sql`

```sql
-- depends: 035_simplify_tasks
-- Migration 036: Drop repo_agent_scans table.
-- The repository-specific agent autoloading subsystem has been removed (issue #79).
-- This table is no longer written to by any code path.
SET search_path TO aquarco, public;

DROP INDEX IF EXISTS idx_repo_agent_scans_repo;
DROP TABLE IF EXISTS repo_agent_scans;
```

Also create a corresponding rollback file for consistency with the project's migration pattern:

### `db/migrations/036_drop_repo_agent_scans.rollback.sql`

```sql
-- Rollback migration 036: Recreate repo_agent_scans table.
SET search_path TO aquarco, public;

CREATE TABLE IF NOT EXISTS repo_agent_scans (
    id              SERIAL PRIMARY KEY,
    repo_name       TEXT NOT NULL REFERENCES repositories(name) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'scanning', 'analyzing', 'writing', 'completed', 'failed')),
    agents_found    INT NOT NULL DEFAULT 0,
    agents_created  INT NOT NULL DEFAULT 0,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_repo_agent_scans_repo
    ON repo_agent_scans (repo_name, created_at DESC);
```

---

## Ordered Implementation Sequence

The steps are ordered leaf → caller to ensure that at no point does a remaining source file import a symbol from a file that has already been deleted.

| Step | Action | Files |
|------|--------|-------|
| 1 | Create DB migration 036 (forward + rollback) | `db/migrations/036_drop_repo_agent_scans.sql`, `db/migrations/036_drop_repo_agent_scans.rollback.sql` |
| 2 | Delete `agent_autoloader.py` | `supervisor/python/src/aquarco_supervisor/agent_autoloader.py` |
| 3 | Clean `models.py` — remove `RepoAgentScanStatus` enum and `RepoAgentScan` model | `supervisor/python/src/aquarco_supervisor/models.py` |
| 4 | Clean `config_store.py` — remove "Autoloaded agents helpers" section | `supervisor/python/src/aquarco_supervisor/config_store.py` |
| 5 | Clean `config_overlay.py` — remove `autoloaded_agents` param and Layer 4 block | `supervisor/python/src/aquarco_supervisor/config_overlay.py` |
| 6 | Clean `pipeline/agent_registry.py` — remove `_load_autoloaded_agents()` and its call site | `supervisor/python/src/aquarco_supervisor/pipeline/agent_registry.py` |
| 7 | Clean `main.py` — remove import, two methods, and two call sites | `supervisor/python/src/aquarco_supervisor/main.py` |
| 8 | Delete 3 dedicated test files | `test_agent_autoload.py`, `test_config_store_autoload.py`, `test_config_overlay_autoload.py` |
| 9 | Surgically edit 4 partially-affected test files | `test_category_rename_consistency.py`, `test_category_rename_extended.py`, `test_pipeline/test_agent_registry.py`, `test_push_review_coverage.py` |
| 10 | Delete `repo-descriptor-agent.yaml` | `config/agents/definitions/system/repo-descriptor-agent.yaml` |
| 11 | (Optional) Delete `028_repo_agent_scans.rollback.sql` | `db/migrations/028_repo_agent_scans.rollback.sql` |

---

## What NOT to Touch

- `executor.py` — confirmed no autoload references; do not modify.
- `agent_definitions` table columns `source` and `agent_group` — used for non-autoload purposes.
- `db/migrations/028_repo_agent_scans.sql` — migration history file, must be kept.
- All pollers, clone/pull workers, and config loading logic not mentioned above.

---

## Acceptance Criteria

1. `supervisor/python/src/aquarco_supervisor/agent_autoloader.py` does not exist in the repository.
2. Running `grep -r "agent_autoloader" supervisor/python/src/` returns zero matches.
3. Running `grep -r "from .agent_autoloader" supervisor/python/src/` returns zero matches.
4. `main.py` contains neither `_auto_scan_new_repos` nor `_process_agent_scan_commands` as method definitions or call sites.
5. `models.py` contains neither `RepoAgentScanStatus` nor `RepoAgentScan`.
6. `config_store.py` contains neither `deactivate_autoloaded_agents` nor `read_autoloaded_agents_from_db`.
7. `config_overlay.resolve_config()` accepts exactly 6 parameters: `default_agents`, `default_pipelines`, `global_overlay`, `global_overlay_base`, `repo_overlay`, `repo_overlay_base`. The `autoloaded_agents` parameter is absent.
8. `pipeline/agent_registry.py` contains no `_load_autoloaded_agents` method.
9. `pipeline/agent_registry.py`'s `load()` method does not call `_load_autoloaded_agents`.
10. `test_agent_autoload.py`, `test_config_store_autoload.py`, `test_config_overlay_autoload.py` do not exist.
11. `config/agents/definitions/system/repo-descriptor-agent.yaml` does not exist.
12. `db/migrations/036_drop_repo_agent_scans.sql` exists and contains `DROP TABLE IF EXISTS repo_agent_scans`.
13. `db/migrations/036_drop_repo_agent_scans.rollback.sql` exists and contains `CREATE TABLE IF NOT EXISTS repo_agent_scans`.
14. Running `cd supervisor/python && python -m pytest tests/ -v` exits with code 0, zero test failures, and zero collection errors.
15. No test file in `supervisor/python/tests/` imports from `aquarco_supervisor.agent_autoloader`.
16. The `repo_agent_scans` table no longer exists after migration 036 is applied.
17. The `agent_definitions` table still has `source` and `agent_group` columns (confirmed untouched by running `\d agent_definitions` in psql).

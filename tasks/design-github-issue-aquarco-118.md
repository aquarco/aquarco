# Design: Git Workflow Specification (Issue #118)

**Task ID:** github-issue-aquarco-118  
**Date:** 2026-04-09  
**Status:** Design  

---

## Overview

Implement the Git Workflow system for Aquarco as specified in GitHub issue #118. Two modes:

1. **Simple Branch** — all work branches from and merges into a single configurable branch (current behaviour, no change to branch naming)
2. **Git Flow** — structured branching (`feature/*`, `bugfix/*`, `hotfix/*`, `release/*`, `develop`, `main`) with task-label–driven base selection and automatic post-merge back-merges

---

## Architecture Decisions

### AD-1: Git Flow is opt-in, per-repository

`git_flow_config JSONB` is added to the `repositories` table. A `NULL` value means Simple Branch mode (preserving backward compatibility). Existing workflows are unaffected.

### AD-2: Branch naming is mode-aware

- **Simple Branch** — keeps current naming: `aquarco/{task_id}/{slug}` (no breaking change)
- **Git Flow** — uses spec naming: `feature/<id>-<title>`, `bugfix/<id>-<title>`, `hotfix/<id>-<title>`

The `<id>` is the numeric part of the task ID (GitHub issue number where available). The `<title>` is slugified.

### AD-3: New module `pipeline/git_workflow.py` owns all Git Flow logic

The executor delegates branch setup and PR base selection to this module. This keeps `executor.py` clean and makes the git workflow logic independently testable.

### AD-4: Back-merge is a git operation, not a new pipeline task

Back-merges are performed synchronously as part of the source poller when it detects a merged PR. If the merge fails due to conflicts, a GitHub PR is created with `back-merge` and `conflict` labels. Aquarco never force-merges.

### AD-5: Hotfix tasks without `target:` label abort early with a comment

If a task labelled `hotfix` has no `target:` label, `executor.py` posts an instructional comment on the GitHub issue and raises `PipelineError`, causing the pipeline to fail cleanly rather than creating a branch on the wrong base.

---

## Components

### 1. Database Migration: `db/migrations/001_add_git_flow_config.sql`

```sql
SET search_path TO aquarco, public;

ALTER TABLE repositories
    ADD COLUMN IF NOT EXISTS git_flow_config JSONB;

COMMENT ON COLUMN repositories.git_flow_config IS
    'Optional Git Flow configuration. NULL = Simple Branch mode.
     Schema: {
       "enabled": true,
       "branches": {
         "stable": "main",
         "development": "develop",
         "release": "release/*",
         "feature": "feature/*",
         "bugfix": "bugfix/*",
         "hotfix": "hotfix/*"
       }
     }';
```

Rollback:
```sql
SET search_path TO aquarco, public;
ALTER TABLE repositories DROP COLUMN IF EXISTS git_flow_config;
```

---

### 2. Models: `supervisor/python/src/aquarco_supervisor/models.py`

Add two new Pydantic models **before** the `Repository` model:

```python
class GitFlowBranches(BaseModel):
    stable: str = "main"
    development: str = "develop"
    release: str = "release/*"
    feature: str = "feature/*"
    bugfix: str = "bugfix/*"
    hotfix: str = "hotfix/*"


class GitFlowConfig(BaseModel):
    enabled: bool = True
    branches: GitFlowBranches = Field(default_factory=GitFlowBranches)
```

Update `Repository`:
```python
class Repository(BaseModel):
    ...
    git_flow_config: GitFlowConfig | None = None  # None = Simple Branch mode
```

---

### 3. New Module: `supervisor/python/src/aquarco_supervisor/pipeline/git_workflow.py`

This module contains all git workflow logic. Public API:

```python
# Data classes
@dataclass
class BranchInfo:
    branch_name: str       # e.g. "feature/42-payment-gateway"
    base_branch: str       # e.g. "develop"
    branch_type: str       # "feature" | "bugfix" | "hotfix" | "simple"
    back_merge_note: str   # Human-readable note for the GitHub comment

# Core functions
def resolve_branch_info(
    git_flow_cfg: GitFlowConfig,
    task_id: str,
    title: str,
    labels: list[str],
) -> BranchInfo | str:
    """Returns BranchInfo or an error string (for hotfix without target:).
    
    Label parsing rules:
    - "feature" label → type=feature, base=branches.development
    - "bugfix" label → type=bugfix, base=active release/* (resolved later) or branches.development
    - "hotfix" label → type=hotfix, base from "target: <branch>" label (required)
    - "target: <branch>" label overrides base for feature/bugfix too
    Branch name: "<type>/<numeric-id>-<slug>"
    where numeric-id is extracted from task_id (e.g. "github-issue-aquarco-118" → "118")
    """

async def find_active_release_branch(
    clone_dir: str,
    stable_branch: str,
    release_pattern: str,
) -> str | None:
    """Find the newest active release branch.
    
    Active = has at least one commit not reachable from stable_branch.
    If multiple exist, pick newest by semantic version (semver descending).
    Non-semver release branches are sorted lexicographically as fallback.
    """

async def perform_back_merge(
    clone_dir: str,
    repo_slug: str,
    source_branch: str,
    target_branch: str,
    task_description: str,
) -> bool:
    """Merge source_branch into target_branch and push.
    
    On success: returns True.
    On merge conflict: creates a GitHub PR with labels back-merge and conflict,
                       returns False.
    Never force-pushes or overwrites conflicting changes.
    """
```

#### `resolve_branch_info` — label parsing detail

```
labels = ["feature", "enhancement"]  → type=feature, base=develop
labels = ["bugfix"]                   → type=bugfix, base=<resolved at runtime>
labels = ["hotfix", "target: main"]  → type=hotfix, base=main
labels = ["hotfix"]                   → error: "hotfix requires target: label"
labels = ["feature", "target: release/1.2"] → type=feature, base=release/1.2
labels = []                           → type=simple (no git flow branch)
```

`target:` label format: `"target: <branch>"` (case-insensitive, trimmed).

#### `find_active_release_branch` detail

```python
# 1. List remote branches matching release_pattern prefix
# 2. For each, check: git rev-list --count origin/stable_branch..origin/release_branch
# 3. Active = count > 0
# 4. Sort active branches by semver (packaging.version.Version with fallback)
# 5. Return newest (last in descending sort) or None
```

#### `perform_back_merge` detail

```python
# 1. git fetch origin
# 2. git checkout -B __aquarco_back_merge_tmp origin/target_branch
# 3. git merge --no-ff origin/source_branch -m "chore(aquarco): back-merge ..."
# 4a. On success: git push origin HEAD:target_branch; cleanup tmp branch
# 4b. On conflict: git merge --abort; create GitHub PR via gh pr create
#     PR title: "chore(aquarco): back-merge {source_branch} → {target_branch}"
#     PR labels: back-merge, conflict
#     PR body: includes task_description and manual resolution instructions
#     cleanup tmp branch
```

---

### 4. Updated `pipeline/executor.py`

#### `_setup_branch` changes

```python
async def _setup_branch(self, task_id, context, work_dir, *, resuming=False) -> str:
    # ... existing head_branch fast-path unchanged ...

    task = await self._tq.get_task(task_id)
    git_flow_cfg = await self._get_git_flow_config(task_id)
    
    if git_flow_cfg and git_flow_cfg.enabled:
        # Git Flow mode
        labels = (task.initial_context or {}).get("labels", [])
        result = resolve_branch_info(git_flow_cfg, task_id, task.title, labels)
        
        if isinstance(result, str):
            # Error (e.g. hotfix without target:) → post comment and abort
            await self._post_issue_comment(task_id, result)
            raise PipelineError(f"Cannot create branch: {result}")
        
        branch_info = result
        branch_name = branch_info.branch_name
        base_branch = branch_info.base_branch
        
        # For bugfix: resolve active release branch at runtime
        if branch_info.branch_type == "bugfix" and base_branch == "<resolve>":
            active_release = await find_active_release_branch(
                work_dir,
                git_flow_cfg.branches.stable,
                git_flow_cfg.branches.release,
            )
            base_branch = active_release or git_flow_cfg.branches.development
            # Reconstruct with resolved base
        
        await _run_git(work_dir, "fetch", "origin")
        if not resuming:
            await _run_git(work_dir, "checkout", "-B", branch_name, f"origin/{base_branch}")
        else:
            await _run_git(work_dir, "checkout", "-B", branch_name, branch_name)
        
        # Post a comment on the GitHub issue with branch info and PR pre-fill link
        await self._post_branch_created_comment(task_id, branch_name, base_branch, branch_info)
        return branch_name
    
    else:
        # Simple Branch / legacy mode — unchanged
        slug = re.sub(r"[^a-z0-9]+", "-", task.title.lower()).strip("-")[:50]
        branch_name = f"aquarco/{task_id}/{slug}"
        # ... rest of existing logic unchanged ...
```

#### New private helper: `_get_git_flow_config`

```python
async def _get_git_flow_config(self, task_id: str) -> GitFlowConfig | None:
    row = await self._db.fetch_one(
        """
        SELECT r.git_flow_config FROM tasks t
        JOIN repositories r ON r.name = t.repository
        WHERE t.id = %(id)s
        """,
        {"id": task_id},
    )
    if not row or not row["git_flow_config"]:
        return None
    return GitFlowConfig.model_validate(row["git_flow_config"])
```

#### New private helper: `_post_branch_created_comment`

Posts a GitHub comment on the issue via `gh issue comment`:

```
## Branch Created

**Branch:** `feature/118-git-workflow-specification`  
**Base:** `develop`  
**Create PR:** [Open PR](https://github.com/aquarco/aquarco/compare/develop...feature/118-...)

> **Note:** When this PR is merged into `develop`, no automatic back-merge is triggered.
```

For bugfix/hotfix: adds the relevant back-merge note.

#### `_create_pipeline_pr` changes

In the feature pipeline (no `head_branch` in context), the PR base must be the correct base for the branch type, not simply `repo.branch`. Add:

```python
base_branch = await self._get_effective_base_branch(task_id, branch_name)
# Instead of: base_branch = await self._get_repo_branch(task_id)
```

`_get_effective_base_branch` inspects the branch prefix and git_flow_config:
- `feature/*` → `branches.development`
- `bugfix/*` → find active release branch or `branches.development`
- `hotfix/*` → from the stored branch info (or detect via git)
- Otherwise → `repo.branch`

---

### 5. Updated `pollers/github_source.py`

#### New method: `_poll_merged_prs`

Called from `poll()` after `_poll_prs`. Polls recently merged PRs:

```python
async def _poll_merged_prs(self, repo_name: str, repo_slug: str, cursor: str) -> int:
    """Check for merged PRs that require back-merge."""
    merged_prs = await _gh_list_merged_prs(repo_slug, since=cursor)
    triggered = 0
    
    git_flow_cfg = await self._get_repo_git_flow_config(repo_name)
    if not git_flow_cfg or not git_flow_cfg.enabled:
        return 0
    
    clone_dir = await self._get_repo_clone_dir(repo_name)
    for pr in merged_prs:
        base_ref = pr.get("baseRefName", "")
        head_ref = pr.get("headRefName", "")
        pr_number = pr.get("number")
        
        # Skip PRs we already processed
        state_key = f"back_merge_processed_pr:{repo_name}:{pr_number}"
        if await self._is_pr_back_merge_processed(state_key):
            continue
        
        back_merge_target = await self._resolve_back_merge_target(
            git_flow_cfg, clone_dir, base_ref
        )
        
        if back_merge_target:
            success = await perform_back_merge(
                clone_dir=clone_dir,
                repo_slug=repo_slug,
                source_branch=base_ref,
                target_branch=back_merge_target,
                task_description=f"PR #{pr_number} merged into {base_ref}",
            )
            if success:
                log.info("back_merge_completed", repo=repo_name, source=base_ref, target=back_merge_target)
            else:
                log.warning("back_merge_conflict_pr_created", repo=repo_name, source=base_ref, target=back_merge_target)
            triggered += 1
        
        await self._mark_pr_back_merge_processed(state_key)
    
    return triggered
```

#### `_resolve_back_merge_target`

```python
async def _resolve_back_merge_target(
    self,
    git_flow_cfg: GitFlowConfig,
    clone_dir: str,
    base_ref: str,
) -> str | None:
    """Return back-merge target branch, or None if not applicable."""
    release_prefix = git_flow_cfg.branches.release.rstrip("*").rstrip("/")
    
    if base_ref.startswith(release_prefix):
        # Merged into release/* → back-merge to develop
        return git_flow_cfg.branches.development
    
    if base_ref == git_flow_cfg.branches.stable:
        # Merged into main → back-merge to active release/* or develop
        active_release = await find_active_release_branch(
            clone_dir,
            git_flow_cfg.branches.stable,
            git_flow_cfg.branches.release,
        )
        return active_release or git_flow_cfg.branches.development
    
    return None
```

#### `_gh_list_merged_prs`

New module-level function (parallel to `_gh_list_prs`):

```python
async def _gh_list_merged_prs(repo_slug: str, since: str, timeout: int = 60) -> list[dict[str, Any]]:
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "list",
        "--repo", repo_slug,
        "--state", "merged",
        "--json", "number,title,headRefName,baseRefName,mergedAt,url",
        "--search", f"merged:>{since}",
        "--limit", "50",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    ...
```

---

### 6. Test Module: `supervisor/python/tests/test_pipeline/test_git_workflow.py`

Unit tests (all mocked, no real git):

```python
# Test: resolve_branch_info for feature label
# Test: resolve_branch_info for bugfix label (base = <resolve>)
# Test: resolve_branch_info for hotfix + target: label
# Test: resolve_branch_info for hotfix without target: → error string
# Test: resolve_branch_info with target: override on feature
# Test: resolve_branch_info no matching label → simple branch (no git flow branch)
# Test: find_active_release_branch picks newest by semver
# Test: find_active_release_branch returns None when all releases merged
# Test: perform_back_merge success path
# Test: perform_back_merge conflict path → creates PR
# Test: _extract_task_numeric_id from "github-issue-aquarco-118" → "118"
# Test: branch name slugification truncated at 50 chars
```

---

## Back-Merge State Tracking

To avoid triggering back-merges twice, processed PR numbers are stored in `poll_state.state_data` under keys `back_merge_processed_pr:{repo_name}:{pr_number}`. This is a simple boolean flag. The poller cursor bounds how far back it looks (default: 1 hour), so in practice this map remains small.

---

## Branch Safety

All dynamically constructed branch names (from git_flow_config patterns, task labels, and slugified titles) are validated against `_SAFE_BRANCH_RE` (`^[A-Za-z0-9][A-Za-z0-9._/\-]*$`) before being passed to git subprocesses. This matches the existing pattern in executor.py and github_source.py.

---

## Assumptions & Open Questions

1. **`packaging` module for semver**: Used in `find_active_release_branch` to sort release branches. `packaging` is already a transitive dependency of many Python projects; if it's not in the project's requirements, use `re`-based semver parsing as a fallback.

2. **PR poller for merges**: `_gh_list_merged_prs` uses `--search "merged:>{cursor}"`. This relies on GitHub search API which may have indexing delay (~30s). For most use cases this is acceptable.

3. **Conflict PR base branch**: When creating a conflict back-merge PR, the head branch is a temporary branch (`aquarco/back-merge/{source}-to-{target}-{timestamp}`). The PR targets `target_branch`.

4. **`git_flow_config` UI**: There is no admin UI for setting `git_flow_config`. For now, it must be inserted directly into the DB or via a future UI. This is noted in the migration comment.

5. **Numeric ID extraction**: `_extract_task_numeric_id("github-issue-aquarco-118")` returns `"118"`. For task IDs without a trailing numeric component, fall back to using the full task_id slug.

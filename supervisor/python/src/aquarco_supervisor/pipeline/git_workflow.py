"""Git workflow logic for Simple Branch and Git Flow modes.

This module encapsulates all branch resolution, back-merge, and
active-release-branch detection logic.  It is consumed by the pipeline
executor and the GitHub source poller.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from ..logging import get_logger
from ..models import GitFlowConfig
from ..utils import run_cmd as _run_cmd
from ..utils import run_git as _run_git

log = get_logger("git-workflow")

# Branch names must not contain shell metacharacters or start with a dash.
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]*$")

# Semver-ish pattern for extracting version from release branch names.
_SEMVER_RE = re.compile(r"(\d+(?:\.\d+)*)")


@dataclass
class BranchInfo:
    """Resolved branch name and its base branch."""

    branch_name: str
    base_branch: str
    branch_type: str  # "feature", "bugfix", "hotfix"
    back_merge_note: str = ""


def _slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a URL/branch-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len]


def _extract_task_numeric_id(task_id: str) -> str:
    """Extract the numeric portion of a task ID for branch naming.

    Examples:
        "github-issue-aquarco-118" -> "118"
        "42" -> "42"
        "task-abc" -> "abc"  (fallback: last segment)
    """
    # Try to find a trailing numeric ID
    m = re.search(r"(\d+)$", task_id)
    if m:
        return m.group(1)
    # Fallback: use the last hyphen-delimited segment
    parts = task_id.rsplit("-", 1)
    return parts[-1] if parts else task_id


def _parse_labels(labels: list[str]) -> tuple[str | None, str | None]:
    """Parse branch type and target override from task labels.

    Returns (branch_type, target_override).
    branch_type is one of "feature", "bugfix", "hotfix", or None.
    target_override is the branch name from a "target: <branch>" label, or None.
    """
    branch_type: str | None = None
    target_override: str | None = None

    for label in labels:
        label_lower = label.strip().lower()
        if label_lower in ("feature", "bugfix", "hotfix"):
            branch_type = label_lower
        elif label_lower.startswith("target:"):
            raw_target = label.strip()[len("target:"):].strip()
            if raw_target:
                target_override = raw_target

    return branch_type, target_override


def _validate_branch_name(name: str) -> str:
    """Validate a branch name is safe for git subprocesses."""
    if not _SAFE_BRANCH_RE.match(name):
        raise ValueError(f"Unsafe branch name: {name!r}")
    return name


def resolve_branch_info(
    git_flow_cfg: GitFlowConfig,
    task_id: str,
    title: str,
    labels: list[str],
    *,
    active_release_branch: str | None = None,
) -> BranchInfo | str:
    """Resolve the branch name and base branch for a task in Git Flow mode.

    Parameters
    ----------
    git_flow_cfg:
        The repository's Git Flow configuration.
    task_id:
        The task identifier (e.g. "github-issue-aquarco-118").
    title:
        The task title, used for slug generation.
    labels:
        Task labels (e.g. ["feature", "target: develop"]).
    active_release_branch:
        The currently active release branch, if any.  Must be pre-resolved
        by the caller via :func:`find_active_release_branch`.

    Returns
    -------
    BranchInfo
        On success, the resolved branch name, base branch, and type.
    str
        On failure, an error message (e.g. hotfix missing target label).
    """
    branch_type, target_override = _parse_labels(labels)
    branches = git_flow_cfg.branches
    numeric_id = _extract_task_numeric_id(task_id)
    slug = _slugify(title)

    if not branch_type:
        # Default to feature if no explicit type label
        branch_type = "feature"

    # Build the branch name from the pattern
    pattern_map = {
        "feature": branches.feature,
        "bugfix": branches.bugfix,
        "hotfix": branches.hotfix,
    }
    pattern = pattern_map[branch_type]
    # Replace the wildcard with the id-slug
    prefix = pattern.replace("/*", "/")
    branch_name = f"{prefix}{numeric_id}-{slug}"

    # Validate the constructed branch name
    try:
        _validate_branch_name(branch_name)
    except ValueError:
        return f"Generated branch name is invalid: {branch_name}"

    # Determine base branch
    if target_override:
        # Explicit target label overrides the default
        try:
            _validate_branch_name(target_override)
        except ValueError:
            return f"Target override branch name is invalid: {target_override}"
        base_branch = target_override
        back_merge_note = _back_merge_note_for(branch_type, base_branch, branches)
        return BranchInfo(
            branch_name=branch_name,
            base_branch=base_branch,
            branch_type=branch_type,
            back_merge_note=back_merge_note,
        )

    if branch_type == "feature":
        base_branch = branches.development
        return BranchInfo(
            branch_name=branch_name,
            base_branch=base_branch,
            branch_type=branch_type,
            back_merge_note="No automatic back-merge — features target the development branch only.",
        )

    if branch_type == "bugfix":
        if active_release_branch:
            base_branch = active_release_branch
            back_merge_note = (
                f"After this PR is merged into `{base_branch}`, "
                f"Aquarco will automatically back-merge into `{branches.development}`."
            )
        else:
            base_branch = branches.development
            back_merge_note = "No active release branch found — bugfix targets the development branch."
        return BranchInfo(
            branch_name=branch_name,
            base_branch=base_branch,
            branch_type=branch_type,
            back_merge_note=back_merge_note,
        )

    if branch_type == "hotfix":
        # Hotfix REQUIRES a target label
        return (
            "Hotfix tasks require a `target:` label specifying the base branch "
            "(e.g. `target: main`). Please add a `target:` label to this issue "
            "and Aquarco will create the hotfix branch automatically."
        )

    return f"Unknown branch type: {branch_type}"


def _back_merge_note_for(
    branch_type: str, base_branch: str, branches: Any,
) -> str:
    """Generate a contextual back-merge note for target-override scenarios."""
    stable = branches.stable
    release_prefix = branches.release.replace("/*", "/")

    if base_branch == stable or base_branch.startswith(release_prefix):
        return (
            f"After this PR is merged into `{base_branch}`, "
            f"Aquarco will automatically back-merge downstream."
        )
    return "No automatic back-merge is expected for this target."


async def find_active_release_branch(
    clone_dir: str,
    stable_branch: str,
    release_pattern: str,
) -> str | None:
    """Find the active release branch (newest by semver, not yet merged into stable).

    An active release branch is one that has at least one commit not
    reachable from the stable branch (i.e. it has not been fully merged).

    Parameters
    ----------
    clone_dir:
        Path to the git repository (clone or worktree).
    stable_branch:
        The stable branch name (e.g. "main").
    release_pattern:
        The release branch glob pattern (e.g. "release/*").

    Returns
    -------
    str or None
        The branch name (without "origin/" prefix) of the newest active
        release branch, or None if no active release branches exist.
    """
    # Fetch to make sure we have the latest remote refs
    try:
        await _run_git(clone_dir, "fetch", "origin", "--prune", check=False)
    except Exception:
        pass

    # List remote branches matching the release pattern
    # Convert "release/*" to "refs/remotes/origin/release/"
    release_prefix = release_pattern.replace("/*", "/")
    ref_pattern = f"refs/remotes/origin/{release_prefix}"

    try:
        output = await _run_git(
            clone_dir, "for-each-ref", "--format=%(refname:short)", ref_pattern + "*",
        )
    except Exception:
        return None

    if not output.strip():
        return None

    candidates: list[tuple[tuple[int, ...], str]] = []
    for ref in output.strip().split("\n"):
        ref = ref.strip()
        if not ref:
            continue
        # ref is like "origin/release/1.2"
        branch_name = ref.replace("origin/", "", 1)

        # Check if this branch has commits not in stable
        try:
            count_str = await _run_git(
                clone_dir, "rev-list", "--count",
                f"origin/{stable_branch}..{ref}",
                check=False,
            )
            count = int(count_str.strip()) if count_str.strip() else 0
        except (ValueError, Exception):
            count = 0

        if count == 0:
            # Fully merged into stable — not active
            continue

        # Extract semver for sorting
        m = _SEMVER_RE.search(branch_name)
        if m:
            version_tuple = tuple(int(x) for x in m.group(1).split("."))
        else:
            version_tuple = (0,)

        candidates.append((version_tuple, branch_name))

    if not candidates:
        return None

    # Sort descending by semver, return the newest
    candidates.sort(reverse=True)
    winner = candidates[0][1]
    log.info("active_release_branch_found", branch=winner, candidates=len(candidates))
    return winner


async def perform_back_merge(
    clone_dir: str,
    repo_slug: str,
    source_branch: str,
    target_branch: str,
    task_description: str = "",
) -> bool:
    """Merge source_branch into target_branch and push.

    If the merge fails due to conflicts, creates a GitHub PR with
    'back-merge' and 'conflict' labels.

    Parameters
    ----------
    clone_dir:
        Path to the git repository.
    repo_slug:
        GitHub owner/repo slug (e.g. "aquarco/aquarco").
    source_branch:
        The branch to merge from (e.g. "release/1.2").
    target_branch:
        The branch to merge into (e.g. "develop").
    task_description:
        Optional description for the back-merge PR on conflict.

    Returns
    -------
    bool
        True if the merge succeeded (or PR was created for conflicts).
        False if the operation failed entirely.
    """
    log.info(
        "back_merge_starting",
        source=source_branch,
        target=target_branch,
        repo=repo_slug,
    )

    try:
        # Fetch latest
        await _run_git(clone_dir, "fetch", "origin")

        # Create a temporary branch for the merge
        merge_branch = f"aquarco/back-merge/{source_branch.replace('/', '-')}-to-{target_branch.replace('/', '-')}"
        try:
            _validate_branch_name(merge_branch)
        except ValueError:
            log.error("back_merge_invalid_branch", branch=merge_branch)
            return False

        # Start from the target branch
        await _run_git(
            clone_dir, "checkout", "-B", merge_branch, f"origin/{target_branch}",
        )

        # Attempt the merge
        merge_result = await _run_git(
            clone_dir, "merge", f"origin/{source_branch}",
            "--no-edit",
            "-m", f"chore: back-merge {source_branch} into {target_branch}",
            check=False,
        )

        # Check if merge had conflicts
        status = await _run_git(clone_dir, "status", "--porcelain")
        has_conflicts = any(
            line.startswith("UU") or line.startswith("AA") or line.startswith("DD")
            for line in status.strip().split("\n") if line.strip()
        )

        if has_conflicts:
            log.warning(
                "back_merge_conflict",
                source=source_branch,
                target=target_branch,
            )
            # Abort the merge and create a PR instead
            await _run_git(clone_dir, "merge", "--abort", check=False)

            # Push source branch and create a conflict PR
            body = (
                f"## Automatic Back-Merge\n\n"
                f"Aquarco attempted to back-merge `{source_branch}` into "
                f"`{target_branch}` but encountered merge conflicts.\n\n"
                f"Please resolve the conflicts manually and merge this PR.\n\n"
            )
            if task_description:
                body += f"**Context:** {task_description}\n"

            try:
                pr_output = await _run_cmd(
                    "gh", "pr", "create",
                    "--repo", repo_slug,
                    "--head", source_branch,
                    "--base", target_branch,
                    "--title",
                    f"chore: back-merge {source_branch} into {target_branch} (conflicts)",
                    "--body", body,
                    "--label", "back-merge",
                    "--label", "conflict",
                    check=False,
                )
                if pr_output:
                    log.info(
                        "back_merge_conflict_pr_created",
                        source=source_branch,
                        target=target_branch,
                        pr_output=pr_output[:200],
                    )
            except Exception as e:
                log.error(
                    "back_merge_conflict_pr_failed",
                    source=source_branch,
                    target=target_branch,
                    error=str(e),
                )
            return True  # We handled the conflict by creating a PR

        # Merge succeeded — push directly to target
        await _run_git(clone_dir, "push", "origin", f"{merge_branch}:{target_branch}")
        log.info(
            "back_merge_success",
            source=source_branch,
            target=target_branch,
        )
        return True

    except Exception as e:
        log.error(
            "back_merge_failed",
            source=source_branch,
            target=target_branch,
            error=str(e),
        )
        return False
    finally:
        # Clean up: go back to a detached HEAD to avoid leaving the repo
        # on the merge branch
        try:
            await _run_git(clone_dir, "checkout", "--detach", check=False)
        except Exception:
            pass


def resolve_back_merge_target(
    git_flow_cfg: GitFlowConfig,
    merged_into_branch: str,
    active_release_branch: str | None = None,
) -> str | None:
    """Determine the back-merge target after a PR is merged.

    Parameters
    ----------
    git_flow_cfg:
        The repository's Git Flow configuration.
    merged_into_branch:
        The branch the PR was merged into.
    active_release_branch:
        The currently active release branch, if any.

    Returns
    -------
    str or None
        The branch to back-merge into, or None if no back-merge is needed.
    """
    branches = git_flow_cfg.branches
    release_prefix = branches.release.replace("/*", "/")

    # PR merged into a release branch -> back-merge to develop
    if merged_into_branch.startswith(release_prefix):
        return branches.development

    # PR merged into stable (main) -> back-merge to active release or develop
    if merged_into_branch == branches.stable:
        if active_release_branch:
            return active_release_branch
        return branches.development

    # No back-merge needed for other branches
    return None


async def post_branch_created_comment(
    repo_slug: str,
    issue_number: int | str,
    branch_info: BranchInfo,
) -> None:
    """Post a comment on a GitHub issue after creating a branch.

    The comment includes:
    - Branch name with a direct link
    - Base branch it was created from
    - Pre-filled link to create a PR
    - Contextual back-merge note
    """
    branch = branch_info.branch_name
    base = branch_info.base_branch
    branch_type = branch_info.branch_type

    # Build GitHub compare URL for PR creation
    pr_create_url = (
        f"https://github.com/{repo_slug}/compare/{base}...{branch}"
        f"?expand=1&title={branch_type}%3A+&body=Closes+%23{issue_number}"
    )

    body = (
        f"### 🌿 Branch Created\n\n"
        f"**Branch:** [`{branch}`](https://github.com/{repo_slug}/tree/{branch})\n"
        f"**Base:** `{base}`\n"
        f"**Type:** {branch_type}\n\n"
        f"[➡️ Create Pull Request]({pr_create_url})\n\n"
    )

    if branch_info.back_merge_note:
        body += f"**Back-merge:** {branch_info.back_merge_note}\n"

    try:
        await _run_cmd(
            "gh", "issue", "comment", str(issue_number),
            "--repo", repo_slug,
            "--body", body,
        )
        log.info(
            "branch_comment_posted",
            repo=repo_slug,
            issue=issue_number,
            branch=branch,
        )
    except Exception as e:
        log.warning(
            "branch_comment_failed",
            repo=repo_slug,
            issue=issue_number,
            error=str(e),
        )

from pathlib import Path
from typing import Optional

from .git_ops import (
    add_worktree,
    commits_brought_in_by_merge,
    ensure_ref,
    get_commit_info,
    get_conflicted_files,
    git,
    is_ancestor,
    is_merge_commit,
    merge_tree_conflicted_files,
    remove_worktree,
    require_branch_ref,
)
from .models import ConflictCommit, ConflictOrigin, MergeResult


def source_commits(repo: Path, target_ref: str, source_ref: str) -> list[str]:
    """
    Commits on the source's own first-parent line that the target does not
    have yet, newest first.

    Merge commits stay in the walk because "Merge 10.11 into 11.4" is a step on
    11.4's line. Commits that only arrived through such a merge are left out:
    merging one of them on its own into the target uses a different merge base
    and skips the merge that may already have adapted it.
    """
    proc = git(
        repo,
        [
            "rev-list",
            "--first-parent",
            f"{target_ref}..{source_ref}",
        ],
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def candidate_commits_for_active_conflict(
    worktree: Path,
    limit_per_side: int = 20,
) -> list[ConflictCommit]:
    proc = git(
        worktree,
        [
            "log",
            "--left-right",
            "--merge",
            "--format=%m%x00%H%x00%an <%ae>%x00%s",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return []

    side_for_marker = {
        "<": "target",
        ">": "source",
    }
    counts = {"target": 0, "source": 0, "unknown": 0}
    seen: set[str] = set()
    candidates: list[ConflictCommit] = []

    for line in proc.stdout.splitlines():
        parts = line.split("\x00", 3)
        if len(parts) != 4:
            continue

        marker, sha, author, subject = parts
        if sha in seen:
            continue

        side = side_for_marker.get(marker, "unknown")
        if counts[side] >= limit_per_side:
            continue

        seen.add(sha)
        counts[side] += 1
        candidates.append(
            ConflictCommit(
                side=side,
                sha=sha,
                author=author,
                subject=subject,
            )
        )

    return candidates


def try_merge(
    repo: Path,
    base_ref: str,
    source_ref: str,
    scratch_root: Path,
    collect_conflict_commits: bool = False,
) -> tuple[bool, list[str], Optional[str], list[ConflictCommit]]:
    worktree = add_worktree(repo, base_ref, scratch_root)

    try:
        proc = git(
            worktree,
            [
                "merge",
                "--no-ff",
                "-m",
                f"Local test merge: {source_ref} into {base_ref}",
                source_ref,
            ],
            check=False,
        )

        if proc.returncode == 0:
            sha = git(worktree, ["rev-parse", "HEAD"]).stdout.strip()
            return True, [], sha, []

        conflicted_files = get_conflicted_files(worktree)
        candidates = (
            candidate_commits_for_active_conflict(worktree)
            if collect_conflict_commits
            else []
        )
        return False, conflicted_files, None, candidates

    finally:
        git(worktree, ["merge", "--abort"], check=False)
        remove_worktree(repo, worktree)


def conflict_start_commits(
    repo: Path,
    target_ref: str,
    commits: list[str],
    conflicted_files: list[str],
) -> dict[str, Optional[str]]:
    """
    Map each conflicted file to the source-side commit where it last went from
    merging cleanly to conflicting.

    Walks the source commits from the tip back towards the target, merging each
    one into the target in memory, and stops once every file has merged
    cleanly at some point. A file that conflicts at every commit started at the
    oldest one, whose parent the target already has. A file that does not
    conflict at the tip maps to None.
    """
    started_at: dict[str, Optional[str]] = {}
    pending = set(conflicted_files)
    newer: Optional[str] = None

    for sha in commits:
        if not pending:
            break

        conflicted = set(merge_tree_conflicted_files(repo, target_ref, sha))
        for path in pending - conflicted:
            started_at[path] = newer
        pending &= conflicted
        newer = sha

    for path in pending:
        started_at[path] = newer

    return started_at


def find_conflict_origins(
    repo: Path,
    target_ref: str,
    source_ref: str,
    conflicted_files: list[str],
    source_is_test_merge: bool = False,
) -> list[ConflictOrigin]:
    commits = source_commits(repo, target_ref, source_ref)
    started_at = conflict_start_commits(repo, target_ref, commits, conflicted_files)
    origins: list[ConflictOrigin] = []

    # Oldest commit first, then any files that could not be attributed.
    for sha in [*reversed(commits), None]:
        files = [path for path in conflicted_files if started_at.get(path) == sha]
        if not files:
            continue

        if sha is None:
            origins.append(ConflictOrigin(files=files))
            continue

        via_commits = (
            commits_brought_in_by_merge(repo, sha, files)
            if is_merge_commit(repo, sha)
            else []
        )
        if source_is_test_merge and sha == commits[0]:
            origins.append(
                ConflictOrigin(files=files, via_commits=via_commits, from_test_merge=True)
            )
        else:
            origins.append(
                ConflictOrigin(
                    files=files,
                    commit=get_commit_info(repo, sha),
                    via_commits=via_commits,
                )
            )

    return origins


def check_merge(
    repo: Path,
    source_label: str,
    source_ref: str,
    target: str,
    scratch_root: Path,
    source_is_test_merge: bool = False,
) -> MergeResult:
    target_ref = require_branch_ref(repo, target)
    ensure_ref(repo, target_ref)
    ensure_ref(repo, source_ref)

    if is_ancestor(repo, source_ref, target_ref):
        return MergeResult(
            source_label=source_label,
            source_ref=source_ref,
            target=target,
            status="nothing_to_merge",
            message=f"{source_label} is already contained in {target}.",
        )

    ok, conflicted_files, merge_sha, candidates = try_merge(
        repo,
        target_ref,
        source_ref,
        scratch_root,
        collect_conflict_commits=True,
    )

    if ok:
        assert merge_sha is not None
        return MergeResult(
            source_label=source_label,
            source_ref=merge_sha,
            target=target,
            status="merge_ok",
            message=f"{source_label} merges cleanly into {target}.",
        )

    conflict_origins = find_conflict_origins(
        repo=repo,
        target_ref=target_ref,
        source_ref=source_ref,
        conflicted_files=conflicted_files,
        source_is_test_merge=source_is_test_merge,
    )
    return MergeResult(
        source_label=source_label,
        source_ref=source_ref,
        target=target,
        status="conflict",
        message=f"{source_label} cannot be cleanly merged into {target}.",
        conflicted_files=conflicted_files,
        conflict_origins=conflict_origins,
        candidate_commits=candidates,
    )


def branch_targets_after(branches: list[str], base_branch: str) -> list[str]:
    if base_branch not in branches:
        raise ValueError(
            f"Base branch {base_branch!r} is not in the configured branch chain: "
            + ", ".join(branches)
        )

    return branches[branches.index(base_branch) + 1 :]

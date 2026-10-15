from pathlib import Path
from typing import Optional

from .git_ops import require_branch_ref
from .merge_engine import branch_targets_after, check_merge
from .models import MergeResult
from .output import print_result


def next_source_after_result(repo: Path, result: MergeResult) -> tuple[str, str, bool]:
    """Return the next source label, ref and whether that ref is a local test merge."""
    if result.status == "merge_ok":
        return f"test merge through {result.target}", result.source_ref, True

    return result.target, require_branch_ref(repo, result.target), False


def first_downstream_broken_target_index(
    branches: list[str],
    pr_base_branch: str,
    baseline_results: list[MergeResult],
) -> Optional[int]:
    pr_base_index = branches.index(pr_base_branch)

    for result in baseline_results:
        if result.status != "conflict":
            continue

        target_index = branches.index(result.target)
        if target_index > pr_base_index:
            return target_index

    return None


def run_pr_mode(
    repo: Path,
    branches: list[str],
    base_branch: str,
    pr_ref: str,
    scratch_root: Path,
) -> list[MergeResult]:
    results: list[MergeResult] = []

    if base_branch not in branches:
        print()
        print(f"PR base branch {base_branch!r} is not in the configured forward-merge chain.")
        print("Skipping PR forward-merge check.")
        return results

    print()
    print(f"Checking baseline chain health from {base_branch} before applying PR changes.")
    baseline_results = run_chain_health_mode(repo, branches, base_branch, scratch_root)
    broken_target_index = first_downstream_broken_target_index(
        branches,
        base_branch,
        baseline_results,
    )
    base_index = branches.index(base_branch)
    stop_index = broken_target_index if broken_target_index is not None else len(branches)
    targets = branches[base_index + 1 : stop_index]

    if broken_target_index is not None:
        print()
        print(
            "Stopping PR forward checks before "
            f"{branches[broken_target_index]} because the baseline chain is already broken there."
        )

    if not targets:
        print()
        print("No PR forward-merge targets remain before the next pre-existing chain break.")
        return results

    source_label = "PR head"
    source_ref = pr_ref
    source_is_test_merge = False

    for target in targets:
        result = check_merge(
            repo, source_label, source_ref, target, scratch_root, source_is_test_merge
        )
        results.append(result)
        print_result(result)

        if result.status == "conflict":
            break

        source_label, source_ref, source_is_test_merge = next_source_after_result(repo, result)

    return results


def run_chain_health_mode(repo: Path, branches: list[str], base_branch: str, scratch_root: Path) -> list[MergeResult]:
    results: list[MergeResult] = []
    source_label = base_branch
    source_ref = require_branch_ref(repo, base_branch)
    source_is_test_merge = False

    for target in branch_targets_after(branches, base_branch):
        result = check_merge(
            repo, source_label, source_ref, target, scratch_root, source_is_test_merge
        )
        results.append(result)
        print_result(result)

        source_label, source_ref, source_is_test_merge = next_source_after_result(repo, result)

    return results

#!/usr/bin/env python3
"""
Demonstration case for MDBF-1220: a conflict-less forward merge that is
textually clean but semantically incorrect.

Context (see Jira MDBF-1220): Oleksandr Byelkin asked to see, at least once,
a concrete instance of the failure mode the auto-merge tool is meant to
guard against -- a merge that Git accepts without conflicts, but that
breaks behaviour because SOURCE and TARGET disagree on something that
isn't visible as a textual diff (a version-dependent default).

This script builds a throwaway two-branch repository that reproduces that
exact shape:

  TARGET (e.g. 10.11): a system default has already been changed by an
    earlier, unrelated commit. Nothing in that commit touches the file the
    new test will land in.

  SOURCE (e.g. 10.6): a new commit adds a test that implicitly relies on
    the *old* default (no explicit SET/config in the test itself -- this
    mirrors a real MTR test that doesn't pin the variable it depends on).

Merging SOURCE into TARGET is textually clean: the new test file doesn't
exist on TARGET, so Git fast-merges it in without any conflict. But once
merged, the test's expected output no longer matches what TARGET's default
actually produces -- a real MTR run would report a failure, even though
the auto-merge tool's conflict check saw nothing wrong.

Two things happen when you run this script:

  1. It shells out to the real AutoMerge checker
     (checker/merge_engine.check_merge, the same code path used by
     check_forward_mergeability.py) against the synthetic repo, and prints
     that the merge is reported clean ("merge_ok").
  2. It then evaluates the merged test file's expectation against TARGET's
     actual default and prints that they disagree -- i.e. the same
     scenario a real `mtr` run would flag as a failing test post-merge.

Requires: this script sitting inside a checkout of
https://github.com/MariaDB/AutoMerge (it imports from checker/).

Usage:
    python3 demo_semantic_regression.py [--keep] [--automerge-repo PATH]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def git(repo: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def write(repo: Path, rel_path: str, content: str) -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def build_synthetic_repo(root: Path) -> Path:
    """
    Builds a tiny repo with two branches:

      target_1011  -- simulates the newer branch. A prior, unrelated
                       commit already changed the "default" the tool
                       depends on.
      source_106   -- simulates the older branch. Adds a new MTR-style
                       test that relies on the *old* default and was
                       green there.

    The two branches never touch the same file, so the merge is
    textually clean by construction -- that's the point.
    """
    repo = root / "server"
    repo.mkdir()
    git(repo, ["init", "-q", "-b", "target_1011"])
    git(repo, ["config", "user.email", "demo@example.invalid"])
    git(repo, ["config", "user.name", "MDBF-1220 demo"])

    # --- Common ancestor -------------------------------------------------
    write(
        repo,
        "sql/sys_vars_defaults.inc",
        "# Defaults shared by all versions at the point of divergence.\n"
        "innodb_lock_wait_timeout_default=50\n",
    )
    git(repo, ["add", "-A"])
    git(repo, ["commit", "-q", "-m", "Common ancestor: shared defaults"])
    ancestor = git(repo, ["rev-parse", "HEAD"]).stdout.strip()

    # --- TARGET (10.11-like): default changed by an unrelated commit -----
    # This is the change a version bump legitimately makes -- nothing here
    # conflicts with anything SOURCE will add, because it doesn't touch
    # the new test file at all.
    write(
        repo,
        "sql/sys_vars_defaults.inc",
        "# Defaults shared by all versions at the point of divergence.\n"
        "innodb_lock_wait_timeout_default=100\n",
    )
    git(repo, ["add", "-A"])
    git(
        repo,
        [
            "commit",
            "-q",
            "-m",
            "10.11: raise innodb_lock_wait_timeout_default to 100 "
            "(unrelated version-default change)",
        ],
    )

    # --- SOURCE (10.6-like): branches from the ancestor, adds a new test -
    git(repo, ["checkout", "-q", "-b", "source_106", ancestor])
    write(
        repo,
        "mysql-test/main/lock_wait_timeout_default.test",
        "--echo # Verify the default lock wait timeout matches the\n"
        "--echo # documented default for this series. No explicit SET:\n"
        "--echo # the test relies on whatever the server ships with.\n"
        "SELECT @@innodb_lock_wait_timeout;\n",
    )
    write(
        repo,
        "mysql-test/main/lock_wait_timeout_default.result",
        "SELECT @@innodb_lock_wait_timeout;\n"
        "@@innodb_lock_wait_timeout\n"
        "50\n",
    )
    git(repo, ["add", "-A"])
    git(
        repo,
        [
            "commit",
            "-q",
            "-m",
            "Add lock_wait_timeout_default.test (green on 10.6, default=50)",
        ],
    )

    git(repo, ["checkout", "-q", "target_1011"])
    return repo


def run_automerge_check(automerge_repo: Path, server_repo: Path) -> str:
    sys.path.insert(0, str(automerge_repo))
    from checker.merge_engine import check_merge  # type: ignore

    scratch = server_repo.parent / "scratch"
    scratch.mkdir(exist_ok=True)

    result = check_merge(
        repo=server_repo,
        source_label="source_106",
        source_ref="source_106",
        target="target_1011",
        scratch_root=scratch,
    )
    return result.status, result.message


def evaluate_semantic_regression(server_repo: Path) -> tuple[bool, str]:
    """
    Simulates the post-merge MTR run: merges source_106 into target_1011
    in a scratch worktree, then checks whether the new test's *recorded*
    expectation matches what TARGET's actual default would produce.

    This is the check a real `mtr` invocation performs implicitly by
    diffing actual vs. .result output. Reproducing the arithmetic here
    keeps the demo self-contained without needing a MariaDB build.
    """
    # Use a separate clone rather than `git worktree add`: target_1011 is
    # already checked out in server_repo itself, and Git refuses to check
    # out the same branch in two worktrees at once.
    worktree = server_repo.parent / "post_merge_worktree"
    if worktree.exists():
        shutil.rmtree(worktree)
    git(server_repo.parent, ["clone", "-q", "--no-single-branch", str(server_repo), str(worktree)])
    git(worktree, ["checkout", "-q", "target_1011"])
    git(worktree, ["merge", "--no-ff", "-q", "-m", "merge for demo", "origin/source_106"])

    target_default = None
    for line in (worktree / "sql/sys_vars_defaults.inc").read_text().splitlines():
        if line.startswith("innodb_lock_wait_timeout_default="):
            target_default = line.split("=", 1)[1]

    recorded_expectation = None
    for line in (
        worktree / "mysql-test/main/lock_wait_timeout_default.result"
    ).read_text().splitlines():
        if line.strip().isdigit():
            recorded_expectation = line.strip()

    shutil.rmtree(worktree, ignore_errors=True)

    test_would_fail = target_default != recorded_expectation
    detail = (
        f"post-merge actual default = {target_default}, "
        f"test's recorded expectation = {recorded_expectation}"
    )
    return test_would_fail, detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--automerge-repo",
        type=Path,
        default=Path(__file__).resolve().parent / ".github" / "forward-merge-check",
        help=(
            "Path to the forward-merge-check tool directory inside a "
            "MariaDB/AutoMerge checkout (default: "
            "<this script's directory>/.github/forward-merge-check, i.e. "
            "run this script from the repo root)."
        ),
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the generated scratch repo instead of deleting it.",
    )
    args = parser.parse_args()

    tmp_root = Path(tempfile.mkdtemp(prefix="mdbf1220-demo-"))
    try:
        server_repo = build_synthetic_repo(tmp_root)

        status, message = run_automerge_check(args.automerge_repo, server_repo)
        print("== Step 1: AutoMerge conflict check (checker.merge_engine.check_merge) ==")
        print(f"status:  {status}")
        print(f"message: {message}")
        if status != "merge_ok":
            print(
                "\nUnexpected: the demo repo was built to merge cleanly, "
                "but the checker reported a conflict. Investigate before "
                "using this as evidence.",
                file=sys.stderr,
            )
            return 2
        print(
            "\n-> Confirmed: Git / the AutoMerge conflict check see nothing "
            "wrong. A Phase 2 merge PR would be auto-created here."
        )

        test_would_fail, detail = evaluate_semantic_regression(server_repo)
        print(
            "\n== Step 2: does the merged test still hold "
            "(what a real `mtr` run would find)? =="
        )
        print(detail)
        if test_would_fail:
            print(
                "\n-> Confirmed: the merge is textually clean but "
                "semantically wrong. lock_wait_timeout_default.test, "
                "unmodified by the merge, now fails against TARGET's "
                "default. This is exactly the case Oleksandr asked to "
                "see demonstrated: conflict-less merge, red only once "
                "the test suite runs."
            )
            return 0
        else:
            print(
                "\nUnexpected: the demo repo was built so the recorded "
                "expectation would mismatch TARGET's default. It didn't. "
                "Investigate before using this as evidence.",
                file=sys.stderr,
            )
            return 2
    finally:
        if args.keep:
            print(f"\n(kept scratch repo at {tmp_root})")
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

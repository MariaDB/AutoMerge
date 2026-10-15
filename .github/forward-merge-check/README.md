# Forward Branch Merge Check

Central reusable GitHub Actions workflows and scripts for checking whether
changes can be forward-merged through an ordered branch chain.

Target repositories keep only small workflow stubs. The reusable workflows,
scripts, tests, and per-repository branch-chain configs live in this tool
repository.

## Important Behavior

### PR Workflow

The PR workflow answers one question:

```text
If this PR lands in its target branch, will that result forward-merge cleanly?
```

It does not test whether the PR merges into its own target branch. GitHub
already computes PR mergeability for the target branch, and if that merge has
conflicts GitHub will not run normal PR CI for the synthetic merge commit.

Important PR behavior:

- The PR workflow stub must exist on every PR target branch that should run the
  check. GitHub discovers `pull_request` workflows from the PR base branch.
- If the PR target branch is not in the configured chain, the check exits
  successfully and skips.
- Baseline sanity starts at the PR target branch, not at the global
  `base_branch`.
- If the downstream chain is already broken without the PR, the PR is checked
  only until that known-broken edge.
- The PR fails only when it introduces a new forward-merge conflict before that
  known-broken edge.
- PR workflows do not send Slack or Zulip notifications.

Example:

```text
baseline:
10.6 -> 10.11: ok
10.11 -> 11.4: ok
11.4 -> 11.8: already broken

PR target:
10.11

checked:
PR-applied 10.11 -> 11.4
```

The workflow stops before `11.4 -> 11.8` because that edge is already broken
without the PR.

### Chain-Health Workflow

The chain-health workflow checks the configured chain on a schedule or manual
dispatch. It does not run on every push.

Important chain-health behavior:

- Every adjacent edge is checked.
- If an edge merges cleanly, the next edge uses the synthetic merge result.
- If an edge is broken, the next edge resets to the real target branch.
- The script never builds synthetic merge state across a failed edge.
- The workflow fails if any checked edge has a conflict.
- State is still written and notifications can still be sent before the
  workflow fails.

Example:

```text
10.6 -> 10.11: conflict
```

The next check is:

```text
real 10.11 -> 11.4
```

not:

```text
failed synthetic 10.11 -> 11.4
```

### Conflict Reports

When a merge fails, the script reports:

- blocked edge
- conflicted files, grouped by the source-side commit where each file starts
  to conflict
- candidate source-side and target-side commits that touched conflicted files
  (console output only)

To find where a file starts to conflict, the checker walks the source's own
first-parent line, newest first, and merges each commit into the target in
memory with `git merge-tree --write-tree` (git 2.38 or newer). A file's origin
is the commit where it last went from merging cleanly to conflicting.

Merge commits such as "Merge 10.11 into 11.4" are single steps in that walk.
The 10.11 commits they brought in are not merged into 11.8 on their own, because
that would use a different merge base and skip the merge that may already have
adapted them. When the origin is a merge, the report lists the commits it
brought in that touch the files, and names the merge author as "Merged by"
rather than "Author". When the origin is the checker's own test merge of earlier
branches, the report says "unmerged changes from earlier branches" and lists
the commits from those branches that touch the files.

Candidate commits come from `git log --left-right --merge` while the repository
is still in the conflicted merge state. They show who touched a conflicted file,
not whose change conflicts.

## Repository Model

The target repository normally calls reusable workflows from this tool
repository. Each run checks out the target repo as `target/` and this repo as
`tool/`, then runs scripts from `tool/` against refs in `target/`.

The chain-health workflow can also check a repository other than the workflow
caller by setting `target_repository`.

## Configuration

Branch chains are configured per target repository:

```text
.github/forward-merge-check/repositories/mariadb-server.yml
```

Example:

```yaml
name: MariaDB Server
repository: MariaDB/server

base_branch: "bb-10.6-release"

branches:
  - "bb-10.6-release"
  - "bb-10.11-release"
  - "bb-11.4-release"
  - "bb-11.8-release"

notifications:
  slack:
    enabled: true
  zulip:
    enabled: true
```

Rules:

- `branches` must be ordered oldest/stablest to newest.
- `base_branch` is where scheduled chain-health starts.
- Add one file under `repositories/` per target repository.
- Each target workflow stub selects its config with `config_path`.

## Target Setup

Copy the small workflow stubs from `examples/target-workflows/` into the target
repository:

```text
.github/workflows/pr-forward-mergeability.yml
.github/workflows/forward-merge-chain-health.yml
```

Update each stub:

```text
YOUR_ORG/forward-branch-merge-check
config_path: .github/forward-merge-check/repositories/mariadb-server.yml
```

For chain-health, `target_repository` is optional:

```text
target_repository: YOUR_ORG/YOUR_TARGET_REPOSITORY
```

Omit it when the workflow stub runs inside the repository being checked; set it
when a scheduler or other repository should run chain-health against a different
checkout target.

If the tool repository is private, add this workflow-caller secret:

```text
FORWARD_MERGE_CHECK_TOKEN
```

If the tool repository is public, remove the `tool_repository_token` secret
mapping from the stubs.

If chain-health checks a private target repository outside the caller
repository, add a token secret such as:

```text
TARGET_REPOSITORY_TOKEN
```

and map it as `target_repository_token`. Omit that mapping when the default
`GITHUB_TOKEN` can read the target repository.

Placement:

- `pr-forward-mergeability.yml` must exist on every branch that can be a PR
  target.
- `forward-merge-chain-health.yml` only needs to exist on the target
  repository default branch.

## Notifications And State

Only chain-health sends Slack or Zulip notifications.

State is stored in the workflow caller repository's GitHub Actions cache
(normally the target repository):

```text
restore prefix: forward-merge-chain-state-
saved key:      forward-merge-chain-state-<run_id>-<run_attempt>
path:           .forward-merge-check-state/state.json
```

This avoids personal access tokens and GitHub Apps. Cache entries are immutable
and may be evicted; if state disappears, the next run behaves like a first run.

The workflow uses a concurrency group so scheduled/manual runs do not compute
and save state at the same time.

If one caller repository checks multiple target repositories, give each target a
distinct `state_cache_key`.

Notifications are sent when:

- the first observed state is already broken
- the chain changes from healthy to broken
- the chain changes from broken to healthy
- the broken result changes
- the configured chain changes while the chain is broken

Notifications are suppressed when the chain stays healthy or stays broken in
the same way.

Optional target-repository secrets:

```text
SLACK_WEBHOOK_URL
ZULIP_WEBHOOK_URL
```

If a webhook secret is missing, that destination is skipped.

For Zulip, create an incoming webhook bot and generate a
`Slack-compatible webhook` integration URL. Select the target channel, enable
`Send all notifications to a single topic`, choose a topic such as
`Forward Merge Checker`, and store that generated URL as `ZULIP_WEBHOOK_URL`.

## Local Use

Run from this tool repository and point `--repo` at any local target clone:

```bash
python3 .github/forward-merge-check/scripts/check_forward_mergeability.py \
  --repo ~/src/server \
  --mode chain-health \
  --config-file .github/forward-merge-check/repositories/mariadb-server.yml
```

PR-like local check:

```bash
python3 .github/forward-merge-check/scripts/check_forward_mergeability.py \
  --repo ~/src/server \
  --mode pr \
  --config-file .github/forward-merge-check/repositories/mariadb-server.yml \
  --base-branch bb-10.6-release \
  --pr-ref my-local-pr-branch
```

Branch refs:

- The script prefers `refs/remotes/origin/<branch>`.
- For local-only repositories, `refs/heads/<branch>` also works.
- If both exist, `origin/<branch>` wins.

Useful commands:

```bash
python3 .github/forward-merge-check/scripts/check_forward_mergeability.py \
  --config-file .github/forward-merge-check/repositories/mariadb-server.yml \
  --print-branches
```

```bash
python3 .github/forward-merge-check/scripts/check_forward_mergeability.py \
  --repo ~/src/server \
  --mode chain-health \
  --base-branch release/1.0 \
  --branches release/1.0 release/1.1 main
```

## Exit Codes

- `0`: clean, already merged, or skipped
- `1`: checked PR/chain has a conflict
- `2`: script/config/runtime error

In PR mode, baseline-chain conflicts do not directly cause exit code `1`.
Only PR-forward conflicts in the tested range fail the PR.

## Tests

```bash
python3 -m unittest discover -s .github/forward-merge-check/tests
```

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .git_ops import branch_head
from .models import CommitInfo, ConflictCommit, ConflictOrigin, MergeResult, NotificationReason


def commit_to_dict(commit: Optional[CommitInfo]) -> Optional[dict]:
    if commit is None:
        return None
    return {
        "sha": commit.sha,
        "author": commit.author,
        "subject": commit.subject,
    }


def conflict_origin_to_dict(origin: ConflictOrigin) -> dict:
    return {
        "files": origin.files,
        "commit": commit_to_dict(origin.commit),
        "via_commits": [commit_to_dict(commit) for commit in origin.via_commits],
        "from_test_merge": origin.from_test_merge,
    }


def conflict_commit_to_dict(commit: ConflictCommit) -> dict:
    return {
        "side": commit.side,
        "sha": commit.sha,
        "author": commit.author,
        "subject": commit.subject,
    }


def result_to_dict(result: MergeResult) -> dict:
    return {
        "source_label": result.source_label,
        "source_ref": result.source_ref,
        "target": result.target,
        "status": result.status,
        "message": result.message,
        "conflicted_files": result.conflicted_files,
        "conflict_origins": [
            conflict_origin_to_dict(origin) for origin in result.conflict_origins
        ],
        "candidate_commits": [
            conflict_commit_to_dict(commit) for commit in result.candidate_commits
        ],
    }


def stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def fingerprint(value: object) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def current_state(repo: Path, branches: list[str], base_branch: str, results: list[MergeResult]) -> dict:
    branch_heads = {branch: branch_head(repo, branch) for branch in branches}
    blocked = next((result for result in results if result.status == "conflict"), None)
    status = "broken" if blocked else "healthy"

    config_payload = {
        "branches": branches,
        "base_branch": base_branch,
    }
    health_payload = {
        "status": status,
        "blocked_edge": None if blocked is None else [blocked.source_label, blocked.target],
        "conflicted_files": [] if blocked is None else blocked.conflicted_files,
        # Via commits are left out: for a test merge they grow with every new
        # commit on the earlier branches, which is not a health change.
        "conflict_origins": []
        if blocked is None
        else [
            {
                "files": origin.files,
                "commit": None if origin.commit is None else origin.commit.sha,
                "from_test_merge": origin.from_test_merge,
            }
            for origin in blocked.conflict_origins
        ],
        "results": [
            {
                "target": result.target,
                "status": result.status,
                "conflicted_files": result.conflicted_files,
            }
            for result in results
        ],
    }

    return {
        "version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "base_branch": base_branch,
        "branches": branches,
        "branch_heads": branch_heads,
        "config_fingerprint": fingerprint(config_payload),
        "health_fingerprint": fingerprint(health_payload),
        "results": [result_to_dict(result) for result in results],
    }


def load_previous_state(raw_state: str | None) -> Optional[dict]:
    if not raw_state:
        return None

    try:
        parsed = json.loads(raw_state)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def load_previous_state_file(path: Optional[Path]) -> Optional[dict]:
    if path is None or not path.exists():
        return None

    return load_previous_state(path.read_text(encoding="utf-8"))


def notification_reasons(previous: Optional[dict], state: dict) -> list[NotificationReason]:
    if previous is None:
        return [NotificationReason.FIRST_RUN] if state["status"] == "broken" else []

    reasons: list[NotificationReason] = []

    if previous.get("config_fingerprint") != state.get("config_fingerprint"):
        reasons.append(NotificationReason.CHAIN_CHANGED)

    old_status = previous.get("status")
    new_status = state.get("status")

    if old_status != "broken" and new_status == "broken":
        reasons.append(NotificationReason.BROKEN)
    elif old_status == "broken" and new_status != "broken":
        reasons.append(NotificationReason.RESOLVED)
    elif old_status == "broken" and new_status == "broken":
        if previous.get("health_fingerprint") != state.get("health_fingerprint"):
            reasons.append(NotificationReason.HEALTH_CHANGED)

    return reasons


def compact_state(state: dict) -> dict:
    return {
        "version": state["version"],
        "checked_at": state["checked_at"],
        "status": state["status"],
        "base_branch": state["base_branch"],
        "branches": state["branches"],
        "branch_heads": state["branch_heads"],
        "config_fingerprint": state["config_fingerprint"],
        "health_fingerprint": state["health_fingerprint"],
        "results": [
            {
                "source_label": result["source_label"],
                "target": result["target"],
                "status": result["status"],
                "conflicted_files": result["conflicted_files"],
                "conflict_origins": [
                    {
                        "files": origin["files"],
                        "commit": None
                        if origin["commit"] is None
                        else {
                            "sha": origin["commit"]["sha"],
                            "subject": origin["commit"]["subject"],
                        },
                        "via_commits": [commit["sha"] for commit in origin["via_commits"]],
                        "from_test_merge": origin["from_test_merge"],
                    }
                    for origin in result["conflict_origins"]
                ],
            }
            for result in state["results"]
        ],
    }

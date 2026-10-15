import os
from typing import Optional

from .models import NotificationReason

MAX_VIA_COMMITS = 5

def github_repository_label() -> str:
    return (
        os.environ.get("FORWARD_MERGE_TARGET_REPOSITORY")
        or os.environ.get("GITHUB_REPOSITORY")
        or "unknown repository"
    )


def repository_url(repository: str) -> str:
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    return f"{server_url}/{repository}"


def github_repository_url() -> Optional[str]:
    repository = (
        os.environ.get("FORWARD_MERGE_TARGET_REPOSITORY")
        or os.environ.get("GITHUB_REPOSITORY")
    )
    if not repository:
        return None

    return repository_url(repository)


def github_workflow_repository_url() -> Optional[str]:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        return None

    return repository_url(repository)


def github_action_run_url() -> Optional[str]:
    repository_url = github_workflow_repository_url()
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not repository_url or not run_id:
        return None

    return f"{repository_url}/actions/runs/{run_id}"


def github_commit_url(sha: str) -> Optional[str]:
    repository_url = github_repository_url()
    if not repository_url:
        return None

    return f"{repository_url}/commit/{sha}"


def format_link(url: Optional[str], label: str, style: str) -> str:
    if not url:
        return label
    if style == "zulip":
        return f"[{label}]({url})"
    return f"<{url}|{label}>"


def humanize_notification_reasons(reasons: list[NotificationReason]) -> str:
    labels = {
        NotificationReason.FIRST_RUN: "first observed broken state",
        NotificationReason.BROKEN: "chain became blocked",
        NotificationReason.RESOLVED: "chain recovered",
        NotificationReason.HEALTH_CHANGED: "blocked result changed",
        NotificationReason.CHAIN_CHANGED: "configured chain changed",
    }
    return ", ".join(labels.get(reason, reason.value) for reason in reasons)


def result_status_label(status: str) -> str:
    labels = {
        "merge_ok": "merged cleanly",
        "nothing_to_merge": "already contained",
        "conflict": "conflict",
        "skipped": "skipped",
    }
    return labels.get(status, status)


def result_status_icon(status: str) -> str:
    if status == "conflict":
        return "❌"
    if status in {"merge_ok", "nothing_to_merge"}:
        return "✅"
    return "⚪"


def format_notification(state: dict, reasons: list[NotificationReason], style: str = "slack") -> str:
    status_text = "✅ healthy"
    if state["status"] == "broken":
        status_text = "🚨 blocked"

    lines = [
        "*Forward Merge Checker*",
        "",
        f"- *Status:* {status_text}",
        f"- *Repository:* `{github_repository_label()}`",
    ]

    if reasons:
        lines.append(f"- *Reason:* {humanize_notification_reasons(reasons)}")

    indexed_results = list(enumerate(state["results"], start=1))
    blocked_results = [
        (index, result) for index, result in indexed_results if result["status"] == "conflict"
    ]
    if blocked_results:
        blocked_edges = ", ".join(
            f"{index}. `{result['source_label']}` -> `{result['target']}`"
            for index, result in blocked_results
        )
        lines.append(f"- *Blocked edges ({len(blocked_results)}):* {blocked_edges}")

    lines.append(f"- *Chain:* {' -> '.join(f'`{branch}`' for branch in state['branches'])}")

    action_run_url = github_action_run_url()
    if action_run_url:
        lines.append(f"- *GitHub Actions run:* {format_link(action_run_url, 'open run', style)}")

    if indexed_results:
        lines.append("")
        lines.append("*Checked edges:*")
        for index, result in indexed_results:
            lines.append(
                f"{index}. {result_status_icon(result['status'])} "
                f"`{result['source_label']}` -> `{result['target']}`: "
                f"{result_status_label(result['status'])}"
            )

    if blocked_results:
        lines.append("")
        lines.append("*Conflict details:*")

    for index, blocked in blocked_results:
        lines.append("")
        lines.append(f"{index}. *Edge:* `{blocked['source_label']}` -> `{blocked['target']}`")

        if not blocked["conflicted_files"]:
            continue

        lines.append(f"*Conflicted files ({len(blocked['conflicted_files'])}):*")
        origins = blocked.get("conflict_origins") or []
        if not origins:
            lines.extend(format_file_block(blocked["conflicted_files"]))
        for origin in origins:
            lines.extend(format_conflict_origin(origin, style))

    return "\n".join(lines)


def format_commit(commit: dict, style: str) -> str:
    label = format_link(github_commit_url(commit["sha"]), commit["sha"][:12], style)
    return f"{label} - {commit['subject']}"


def format_file_block(files: list[str]) -> list[str]:
    return ["```", *(f"- {path}" for path in files), "```"]


def format_conflict_origin(origin: dict, style: str) -> list[str]:
    commit = origin.get("commit")
    via_commits = origin.get("via_commits") or []

    if origin.get("from_test_merge"):
        lines = ["*Started conflicting at:* unmerged changes from earlier branches"]
    elif commit:
        lines = [f"*Started conflicting at:* {format_commit(commit, style)}"]
        # A merge that brought the change in did not author it.
        author_label = "Merged by" if via_commits else "Author"
        lines.append(f"*{author_label}:* {commit['author']}")
    else:
        lines = ["*Started conflicting at:* not identified"]

    if via_commits:
        lines.append("*Changes brought in that touch these files:*")
        lines.extend(
            f"- {format_commit(via, style)} ({via['author']})"
            for via in via_commits[:MAX_VIA_COMMITS]
        )
        if len(via_commits) > MAX_VIA_COMMITS:
            lines.append(f"- and {len(via_commits) - MAX_VIA_COMMITS} more")

    lines.extend(format_file_block(origin["files"]))
    return lines

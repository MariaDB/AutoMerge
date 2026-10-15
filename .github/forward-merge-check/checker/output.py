import argparse
import json

from .models import MergeResult, NotificationReason
from .notification_text import format_notification
from .state import compact_state


def write_outputs(args: argparse.Namespace, state: dict, reasons: list[NotificationReason]) -> None:
    if args.state_output:
        args.state_output.write_text(
            json.dumps(compact_state(state), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    notification = {
        "notify": bool(reasons),
        "reasons": [reason.value for reason in reasons],
        "status": state["status"],
        "text": format_notification(state, reasons, "slack") if reasons else "",
        "slack_text": format_notification(state, reasons, "slack") if reasons else "",
        "zulip_text": format_notification(state, reasons, "zulip") if reasons else "",
        "state": state,
    }

    if args.notification_output:
        args.notification_output.write_text(
            json.dumps(notification, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"status={state['status']}\n")
            output.write(f"should_notify={'true' if reasons else 'false'}\n")
            output.write(f"health_fingerprint={state['health_fingerprint']}\n")


def print_result(result: MergeResult) -> None:
    print()
    print(f"Merge check: {result.source_label} -> {result.target}")
    print("-" * 72)
    print(f"Status: {result.status}")
    print(result.message)

    if result.conflicted_files:
        print()
        print("Conflicted files:")
        for path in result.conflicted_files:
            print(f"  - {path}")

    if result.conflict_origins:
        print()
        print("Source-side commits where the conflicted files start to conflict:")
        for origin in result.conflict_origins:
            if origin.from_test_merge:
                print("  - unmerged changes from earlier branches")
            elif origin.commit:
                c = origin.commit
                print(f"  - {c.sha}")
                print(f"    Author:  {c.author}")
                print(f"    Subject: {c.subject}")
            else:
                print("  - not identified")
            for c in origin.via_commits:
                print(f"    Via:     {c.sha[:12]} {c.subject} ({c.author})")
            for path in origin.files:
                print(f"    File:    {path}")

    if result.candidate_commits:
        print()
        print("Candidate commits touching conflicted files:")
        side_labels = {
            "source": f"Source side ({result.source_label})",
            "target": f"Target side ({result.target})",
            "unknown": "Unknown side",
        }
        for side in ["source", "target", "unknown"]:
            commits = [
                commit for commit in result.candidate_commits if commit.side == side
            ]
            if not commits:
                continue

            print(f"  {side_labels[side]}:")
            for c in commits:
                print(f"    - {c.sha}")
                print(f"      Author:  {c.author}")
                print(f"      Subject: {c.subject}")


def print_summary(results: list[MergeResult]) -> None:
    print()
    print("=" * 72)
    print("Summary")
    print("=" * 72)

    if not results:
        print("No forward branches to check.")
        print("Final result: forward merge chain is clean.")
        return

    for result in results:
        print(f"{result.source_label} -> {result.target}: {result.status}")

    print()

    if any(result.status == "conflict" for result in results):
        print("Final result: forward merge chain is blocked by conflicts.")
    else:
        print("Final result: forward merge chain is clean or already merged.")

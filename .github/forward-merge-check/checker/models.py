from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


@dataclass
class CommitInfo:
    sha: str
    author: str
    subject: str


@dataclass
class ConflictCommit:
    side: str
    sha: str
    author: str
    subject: str


@dataclass
class ConflictOrigin:
    """
    Conflicted files grouped by the source-side commit where they start to
    conflict. When that commit is a merge, via_commits lists the non-merge
    commits it brought in that touch the files.
    """

    files: list[str]
    commit: Optional[CommitInfo] = None
    via_commits: list[CommitInfo] = field(default_factory=list)
    # The files start to conflict at the checker's own test merge of earlier
    # branches. That commit only exists locally, so it is not reported.
    from_test_merge: bool = False


@dataclass
class MergeResult:
    source_label: str
    source_ref: str
    target: str
    status: str
    message: str
    conflicted_files: list[str] = field(default_factory=list)
    conflict_origins: list[ConflictOrigin] = field(default_factory=list)
    candidate_commits: list[ConflictCommit] = field(default_factory=list)


class NotificationReason(str, Enum):
    FIRST_RUN = "first_run"
    BROKEN = "broken"
    RESOLVED = "resolved"
    HEALTH_CHANGED = "health_changed"
    CHAIN_CHANGED = "chain_changed"

"""Structured human-review decisions for explicit HITL topology gates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


REVIEW_APPROVE = "approve"
REVIEW_REQUEST_CHANGES = "request_changes"
REVIEW_ABORT = "abort"
REVIEW_DECISIONS = {REVIEW_APPROVE, REVIEW_REQUEST_CHANGES, REVIEW_ABORT}


@dataclass(frozen=True)
class ReviewResult:
    decision: str
    comments: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if self.decision not in REVIEW_DECISIONS:
            raise ValueError(f"invalid review decision: {self.decision!r}")
        if not self.timestamp:
            object.__setattr__(
                self,
                "timestamp",
                datetime.now(timezone.utc).isoformat(),
            )
        if self.decision == REVIEW_REQUEST_CHANGES and not self.comments.strip():
            raise ValueError("request_changes requires reviewer comments")


def format_review_history(reviews: list[ReviewResult]) -> str:
    sections = []
    for attempt, review in enumerate(reviews, start=1):
        body = review.comments.strip() or "(no comments)"
        sections.append(
            f"## Review {attempt}\n\n"
            f"- Decision: `{review.decision}`\n"
            f"- Timestamp: `{review.timestamp}`\n\n"
            f"{body}\n"
        )
    return "\n".join(sections)

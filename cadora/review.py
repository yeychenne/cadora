"""Structured human-review decisions for explicit HITL topology gates."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


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


REQUEST_FILE = "cadora-review-request.json"
DECISION_FILE = "cadora-review-decision.json"


def file_review_fn(timeout: float = 3600.0, interval: float = 2.0):
    """A headless HITL reviewer: write a request file, poll for a decision file.

    For non-interactive runs (Quick Desktop, CI, background) where there is no TTY. Cadora writes
    ``cadora-review-request.json`` into the node's workspace listing the stage's documents, then
    blocks until any tool or human drops a ``cadora-review-decision.json`` next to it:

        {"decision": "approve" | "request_changes" | "abort", "comments": "..."}

    Fails **closed**: an invalid decision, or no decision within ``timeout`` seconds, returns
    ``abort`` — the run stops honestly rather than proceeding unreviewed.
    """

    def review_fn(node, node_cwd: str, documents=None) -> ReviewResult:
        base = Path(node_cwd)
        request, decision = base / REQUEST_FILE, base / DECISION_FILE
        decision.unlink(missing_ok=True)  # clear any stale decision before we ask
        request.write_text(
            json.dumps(
                {
                    "node_id": node.id,
                    "documents": [{"path": p, "kind": k} for p, k in (documents or [])],
                    "how_to_respond": (
                        f"write {DECISION_FILE} next to this file: "
                        '{"decision": "approve" | "request_changes" | "abort", "comments": "..."}'
                    ),
                },
                indent=2,
            )
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if decision.is_file():
                try:
                    data = json.loads(decision.read_text())
                except (OSError, ValueError):
                    time.sleep(interval)
                    continue
                request.unlink(missing_ok=True)
                decision.unlink(missing_ok=True)
                choice = str(data.get("decision", "")).strip()
                comments = str(data.get("comments", "")).strip()
                if choice not in REVIEW_DECISIONS:
                    return ReviewResult(REVIEW_ABORT, f"invalid decision in {DECISION_FILE}: {choice!r}")
                if choice == REVIEW_REQUEST_CHANGES and not comments:
                    return ReviewResult(REVIEW_ABORT, "request_changes requires reviewer comments")
                return ReviewResult(choice, comments)
            time.sleep(interval)
        request.unlink(missing_ok=True)
        return ReviewResult(REVIEW_ABORT, f"file review timed out after {int(timeout)}s")

    return review_fn

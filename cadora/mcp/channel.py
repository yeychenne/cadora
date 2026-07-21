"""Interface-agnostic HITL plumbing — the review side of the MCP seam.

The runner calls ``review_fn(node, cwd) -> ReviewResult`` at each ``review: true`` gate.
``channel_review_fn(channel)`` returns such a review_fn backed by a ``ReviewChannel``, so an external
front-end (an MCP client — Claude Desktop, Codex/ChatGPT — or a test) supplies the
decision. The run executes in its own thread; the front-end polls ``pending()`` and calls ``respond()``.

This decouples HITL from any one surface, mirroring how ``NodeExecutor`` decouples Cadora from any
one coding agent.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from cadora.review import REVIEW_ABORT, ReviewResult


@dataclass
class ReviewRequest:
    """A pending review surfaced to the front-end."""

    node_id: str
    docs_dir: str
    artifacts: list[str] = field(default_factory=list)


class ReviewChannel:
    """A one-at-a-time rendezvous between the run thread and a review front-end (thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._request: ReviewRequest | None = None
        self._response: ReviewResult | None = None
        self._has_request = threading.Event()
        self._has_response = threading.Event()

    def request_review(self, request: ReviewRequest, timeout: float | None = None) -> ReviewResult:
        """Called by the run thread; blocks until the front-end responds (or times out -> abort)."""
        with self._lock:
            self._request = request
            self._response = None
            self._has_response.clear()
            self._has_request.set()
        if not self._has_response.wait(timeout):
            self._clear()
            return ReviewResult(REVIEW_ABORT, "review timed out — no decision received")
        with self._lock:
            response = self._response or ReviewResult(REVIEW_ABORT, "no decision received")
            self._request = None
            self._response = None
            return response

    def pending(self) -> ReviewRequest | None:
        """Called by the front-end: the review awaiting a decision, if any."""
        return self._request if self._has_request.is_set() else None

    def respond(self, result: ReviewResult) -> None:
        """Called by the front-end to deliver the operator's decision."""
        with self._lock:
            if not self._has_request.is_set():
                raise RuntimeError("no review is pending")
            self._response = result
            self._has_request.clear()  # stop surfacing this request so the front-end can't re-submit
            self._has_response.set()

    def _clear(self) -> None:
        with self._lock:
            self._request = None
            self._has_request.clear()


def channel_review_fn(channel: ReviewChannel, timeout: float | None = None):
    """Return a runner-compatible ``review_fn(node, cwd)`` backed by ``channel``.

    ``timeout`` bounds how long the run thread blocks on a gate; on expiry the channel fails closed
    to an abort (see :meth:`ReviewChannel.request_review`) so a front-end that starts a run and never
    submits cannot pin the run thread forever. ``None`` or a non-positive value waits indefinitely —
    the explicit opt-out for a genuinely interactive client that may sit on a gate for a long time.
    """
    effective = timeout if (timeout and timeout > 0) else None

    def review_fn(node, node_cwd: str, documents=None) -> ReviewResult:
        docs = Path(node_cwd) / "aidlc-docs"
        if documents:
            # Scope the review surface to the document(s) THIS stage produced.
            artifacts = [relpath for relpath, _kind in documents]
        elif docs.is_dir():
            artifacts = sorted(str(p.relative_to(node_cwd)) for p in docs.rglob("*.md"))
        else:
            artifacts = []
        return channel.request_review(
            ReviewRequest(node_id=node.id, docs_dir=str(docs), artifacts=artifacts),
            effective,
        )

    return review_fn

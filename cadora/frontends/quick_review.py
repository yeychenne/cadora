"""Quick Desktop MCP front-end — the reviewer experience over the cadora-mcp seam.

This module implements the HITL reviewer UX for Quick Desktop (or any MCP client that
supports conversational review). It connects to ``cadora mcp`` over stdio and drives:

1. Decision cards (approve / request changes / abort)
2. Document tabs (artifact rendering for review)
3. Conversational feedback (comments feed back into revision cycles)

Quick Desktop loads this as a skill — it calls the 5 cadora-mcp tools and presents
the review experience natively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cadora.mcp.session import RunSession
from cadora.review import ReviewResult


@dataclass
class ReviewCard:
    """A decision card presented to the reviewer at a HITL gate."""

    node_id: str
    role: str
    artifacts: list[str]
    options: list[str] = field(default_factory=lambda: [
        "✅ Approve — proceed to next stage",
        "✏️ Request Changes — provide feedback",
        "🛑 Abort — stop the run",
    ])


@dataclass
class ArtifactTab:
    """A document tab for reviewing a generated artifact."""

    path: str
    title: str
    content: str


DecisionFn = Callable[[ReviewCard, list[ArtifactTab]], tuple[str, str]]
"""(card, tabs) -> (decision, comments). Supplied by the host (Quick Desktop UI)."""


class QuickReviewFrontEnd:
    """MCP client front-end that drives the HITL review experience.

    In production, Quick Desktop instantiates this and supplies a ``decision_fn``
    that renders the card/tabs in its UI and collects the user's choice.
    For testing, supply a programmatic decision_fn.
    """

    def __init__(self, session: RunSession, decision_fn: DecisionFn):
        self.session = session
        self.decision_fn = decision_fn
        self.history: list[dict] = []

    def drive_review_loop(self, poll_interval: float = 0.05, timeout: float = 300.0) -> dict:
        """Poll for review gates and drive them to completion. Returns final status."""
        import time

        deadline = time.time() + timeout
        while self.session.is_running() and time.time() < deadline:
            request = self.session.pending_review()
            if request is None:
                time.sleep(poll_interval)
                continue

            # Build artifact tabs
            tabs = self._render_artifacts(request.artifacts)

            # Present the decision card
            card = ReviewCard(
                node_id=request.node_id,
                role=request.node_id,  # simplification; real impl could carry node.role
                artifacts=request.artifacts,
            )

            decision, comments = self.decision_fn(card, tabs)
            review = ReviewResult(decision, comments)
            self.session.submit_review(review)
            self.history.append({
                "node_id": request.node_id,
                "decision": decision,
                "comments": comments,
            })

        self.session.join(timeout=5)
        return {
            "running": self.session.is_running(),
            "result_path": str(self.session.result_path) if self.session.result_path else None,
            "error": self.session.error,
            "reviews": self.history,
        }

    def _render_artifacts(self, artifact_paths: list[str]) -> list[ArtifactTab]:
        """Read artifacts and build tabs for rendering."""
        cwd = Path(self.session.run_kwargs.get("cwd", "."))
        tabs = []
        for rel_path in artifact_paths:
            full = cwd / rel_path
            if full.is_file():
                tabs.append(ArtifactTab(
                    path=rel_path,
                    title=full.stem.replace("-", " ").title(),
                    content=full.read_text(errors="replace"),
                ))
        return tabs

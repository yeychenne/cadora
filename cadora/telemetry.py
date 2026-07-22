"""Live run telemetry artifacts for the local dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from cadora.provenance import conductor_fingerprint
from cadora.topology import Topology


class RunTelemetry:
    """Write a small event stream and latest-status snapshot for one run."""

    def __init__(self, archive_root: str | Path, run_id: str, topology: Topology, executor: str):
        self.dir = Path(archive_root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.status_path = self.dir / "status.json"
        self.events_path = self.dir / "run-events.jsonl"
        self.status: dict = {
            "run_id": run_id,
            "topology": topology.name,
            "executor": executor,
            # Which Cadora is driving this run — stamped at start so even an aborted or
            # mid-flight-inspected run shows the conductor it launched under.
            "conductor": conductor_fingerprint(),
            "status": "created",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "resumed_from": None,
            "skipped_nodes": [],
            "nodes": {
                node.id: {
                    "node_id": node.id,
                    "role": node.role,
                    "phase": node.phase,
                    "depends_on": list(node.depends_on),
                    "status": "idle",
                    "started_at": None,
                    "completed_at": None,
                    "model": node.model,
                    "cost_usd": None,
                    "credits": None,
                    "duration_seconds": None,
                    "review_wait_seconds": 0.0,
                    "generation_tokens": 0,
                    "context_tokens": 0,
                    "gate": None,
                    "integrity": None,
                    "review": None,
                    "error": None,
                }
                for node in topology.nodes
            },
        }
        # Same reasoning as the manifest's node carry-forward: `--resume-from` reuses the run id,
        # and a node it skips would otherwise be reported at cost 0 — under-reporting the run's
        # real spend on the dashboard and in every reader of status.json.
        self._prior_nodes = _prior_node_records(self.status_path)
        # W5: human review is not agent work. Record each review-wait interval so a node's
        # duration_seconds can exclude review time that overlapped its span — its own gates, and
        # under --max-parallel a sibling's review it sat idle through. review_wait_seconds keeps the
        # node's own deliberation as a separate honest field. Both flow into the signed evidence pack.
        self._review_wait_start: dict[str, str] = {}
        self._review_intervals: list[tuple[str, str]] = []
        self._write_status()

    def emit(self, event_type: str, *, node_id: str | None = None, **payload) -> None:
        event = {
            "ts": _now(),
            "run_id": self.run_id,
            "type": event_type,
            "node_id": node_id,
            "payload": {k: v for k, v in payload.items() if v is not None},
        }
        with self.events_path.open("a") as f:
            f.write(json.dumps(event) + "\n")

    def run_started(self) -> None:
        ts = _now()
        self.status["status"] = "running"
        self.status["started_at"] = ts
        self.emit("run_started")
        self._write_status()

    def run_completed(self, ok: bool, *, error: str | None = None) -> None:
        ts = _now()
        self.status["status"] = "completed" if ok else "failed"
        self.status["completed_at"] = ts
        self.status["error"] = error
        self.emit("run_completed" if ok else "run_failed", error=error)
        self._write_status()

    def mark_resume(self, resume_from: str | None, skipped_nodes: list[str]) -> None:
        """Record run-level resume metadata: which node the run resumed from and what it skipped."""
        self.status["resumed_from"] = resume_from
        self.status["skipped_nodes"] = list(skipped_nodes)
        self.emit("run_resumed", resume_from=resume_from, skipped=list(skipped_nodes))
        self._write_status()

    def node_skipped(self, node_id: str, *, reason: str = "") -> None:
        """Mark a node as not executed in this run (``--resume-from`` / ``--skip``).

        Distinct from ``completed`` — the node did not run here; its artifacts are trusted to
        already exist in the workspace.
        """
        node = self._node(node_id)
        prior = self._prior_nodes.get(node_id, {})
        # The node did not run *here*, but it did run — restore what that invocation recorded so
        # the run's cost and duration stay whole.
        for field in ("cost_usd", "credits", "duration_seconds", "review_wait_seconds",
                      "generation_tokens", "context_tokens", "model", "gate", "integrity"):
            if prior.get(field) is not None:
                node[field] = prior[field]
        node["status"] = "skipped"
        node["skipped_reason"] = reason
        self.emit("node_skipped", node_id=node_id, reason=reason)
        self._write_status()

    def node_started(self, node_id: str, *, model: str | None = None, at: str | None = None) -> None:
        """Mark a node running. ``at`` overrides the start timestamp.

        A node whose agent ran concurrently (in a ``--max-parallel`` wave) already finished
        executing by the time the sequential loop reaches it, so the caller passes the *real*
        executor start captured in the worker thread. Without it, ``duration_seconds`` would time
        only the gate/archive step and the agent's work would be attributed to no node at all.
        """
        ts = at or _now()
        node = self._node(node_id)
        node["status"] = "running"
        node["started_at"] = ts
        if model:
            node["model"] = model
        self.emit("node_started", node_id=node_id, model=model)
        self._write_status()

    def node_recorded(
        self,
        node_id: str,
        *,
        ok: bool,
        model: str | None = None,
        cost_usd: float | None = None,
        usage: dict | None = None,
        gate: dict | None = None,
        integrity: dict | None = None,
        review: str | None = None,
        error: str | None = None,
    ) -> None:
        ts = _now()
        node = self._node(node_id)
        node["status"] = "completed" if ok else "failed"
        node["completed_at"] = ts
        # W5: exclude human-review time from the node's WORK duration. Subtract every review-wait
        # interval that overlapped this node's span, so duration_seconds is agent work — not
        # wall-clock that silently includes a human deliberating. This value feeds the signed pack.
        raw = _duration(node.get("started_at"), ts)
        review_overlap = _overlap_seconds(node.get("started_at"), ts, self._review_intervals)
        node["duration_seconds"] = None if raw is None else round(max(0.0, raw - review_overlap), 3)
        node["review_wait_seconds"] = round(node.get("review_wait_seconds") or 0.0, 3)
        node["model"] = model or node.get("model")
        # A node re-run after a resume spent the earlier invocation's money too — report the run
        # total, matching the manifest, so the dashboard and the evidence never disagree.
        prior_cost = (self._prior_nodes.get(node_id) or {}).get("cost_usd")
        node["cost_usd"] = (
            cost_usd if prior_cost is None else round((cost_usd or 0.0) + prior_cost, 6)
        )
        node["credits"] = (usage or {}).get("credits")
        node["gate"] = gate
        node["integrity"] = integrity
        node["review"] = review
        node["error"] = error
        generation, context = _token_totals(usage or {})
        node["generation_tokens"] = generation
        node["context_tokens"] = context
        self.emit(
            "node_completed" if ok else "node_failed",
            node_id=node_id,
            model=node["model"],
            cost_usd=cost_usd,
            credits=node["credits"],
            duration_seconds=node["duration_seconds"],
            review_wait_seconds=node["review_wait_seconds"] or None,
            generation_tokens=generation,
            context_tokens=context,
            error=error,
        )
        self._write_status()

    def review_waiting(self, node_id: str) -> None:
        node = self._node(node_id)
        node["status"] = "review_waiting"
        self._review_wait_start[node_id] = _now()  # W5: start of a human-deliberation interval
        self.emit("review_waiting", node_id=node_id)
        self._write_status()

    def review_resolved(self, node_id: str, decision: str) -> None:
        node = self._node(node_id)
        node["review"] = decision
        # W5: close the review-wait interval — record it globally (for the duration correction) and
        # accumulate this node's own deliberation time (across request_changes reruns).
        started = self._review_wait_start.pop(node_id, None)
        waited = 0.0
        if started is not None:
            ended = _now()
            self._review_intervals.append((started, ended))
            waited = _duration(started, ended) or 0.0
            node["review_wait_seconds"] = round((node.get("review_wait_seconds") or 0.0) + waited, 3)
        event_type = {
            "approve": "review_approved",
            "request_changes": "review_requested_changes",
            "abort": "review_aborted",
        }.get(decision, "review_resolved")
        self.emit(event_type, node_id=node_id, decision=decision, waited_seconds=round(waited, 3) or None)
        self._write_status()

    def _node(self, node_id: str) -> dict:
        return self.status["nodes"].setdefault(
            node_id,
            {
                "node_id": node_id,
                "status": "idle",
                "started_at": None,
                "completed_at": None,
            },
        )

    def _write_status(self) -> None:
        tmp = self.status_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.status, indent=2))
        tmp.replace(self.status_path)


def _prior_node_records(status_path: Path) -> dict[str, dict]:
    """Node records already written for this run id, keyed by node id (empty when absent/corrupt)."""
    if not status_path.is_file():
        return {}
    try:
        prior = json.loads(status_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    nodes = prior.get("nodes")
    return {k: v for k, v in nodes.items() if isinstance(v, dict)} if isinstance(nodes, dict) else {}


def _token_totals(usage: dict) -> tuple[int, int]:
    input_tokens = _int(usage.get("input_tokens") or usage.get("inputTokens"))
    output_tokens = _int(usage.get("output_tokens") or usage.get("outputTokens"))
    cache_creation = _int(
        usage.get("cache_creation_input_tokens") or usage.get("cacheCreationInputTokens")
    )
    cache_read = _int(
        usage.get("cache_read_input_tokens")
        or usage.get("cacheReadInputTokens")
        or usage.get("cached_input_tokens")
        or usage.get("cachedInputTokens")
    )
    total = _int(usage.get("total_tokens") or usage.get("totalTokens"))
    generation = input_tokens + output_tokens
    context = generation + cache_creation + cache_read
    return generation or total, context or total


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duration(started_at: str | None, completed_at: str | None) -> float | None:
    """Wall-clock seconds between two ISO timestamps, so status.json carries per-node duration
    live (as each node completes) instead of only in the end-of-run manifest."""
    if not started_at or not completed_at:
        return None
    try:
        delta = datetime.fromisoformat(completed_at) - datetime.fromisoformat(started_at)
    except (ValueError, TypeError):
        return None
    return round(delta.total_seconds(), 3)


def _overlap_seconds(span_start: str | None, span_end: str | None, intervals) -> float:
    """Total seconds of ``intervals`` (each an ISO ``(start, end)`` pair) that fall inside the span.

    Used to debit a node's work duration for human-review time: each interval counts only for the
    part overlapping ``[span_start, span_end]``, so a node loses its own review waits and — under
    ``--max-parallel`` — any sibling review it sat idle through, but nothing outside its own span.
    """
    if not span_start or not span_end:
        return 0.0
    try:
        s = datetime.fromisoformat(span_start)
        e = datetime.fromisoformat(span_end)
    except (ValueError, TypeError):
        return 0.0
    total = 0.0
    for istart, iend in intervals:
        try:
            a = datetime.fromisoformat(istart)
            b = datetime.fromisoformat(iend)
        except (ValueError, TypeError):
            continue
        lo, hi = max(s, a), min(e, b)
        if hi > lo:
            total += (hi - lo).total_seconds()
    return round(total, 3)

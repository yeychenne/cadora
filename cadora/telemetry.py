"""Live run telemetry artifacts for the local dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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
            "status": "created",
            "started_at": None,
            "completed_at": None,
            "error": None,
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

    def node_started(self, node_id: str, *, model: str | None = None) -> None:
        ts = _now()
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
        node["model"] = model or node.get("model")
        node["cost_usd"] = cost_usd
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
            generation_tokens=generation,
            context_tokens=context,
            error=error,
        )
        self._write_status()

    def review_waiting(self, node_id: str) -> None:
        node = self._node(node_id)
        node["status"] = "review_waiting"
        self.emit("review_waiting", node_id=node_id)
        self._write_status()

    def review_resolved(self, node_id: str, decision: str) -> None:
        node = self._node(node_id)
        node["review"] = decision
        event_type = {
            "approve": "review_approved",
            "request_changes": "review_requested_changes",
            "abort": "review_aborted",
        }.get(decision, "review_resolved")
        self.emit(event_type, node_id=node_id, decision=decision)
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

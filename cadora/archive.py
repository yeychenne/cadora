"""Run archive — Cadora's knowledge / experiment layer.

Every run lands in ``runs/<run_id>/`` with a ``manifest.json`` plus per-node
outputs, in a stable, tool-readable shape so comparison and eval tooling can
read Cadora runs with minimal change.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

from cadora.executors.base import ExecutionResult
from cadora.gates import GateResult
from cadora.integrity import IntegrityReport
from cadora.review import ReviewResult, format_review_history


class RunArchive:
    def __init__(self, root: str | Path, run_id: str, executor: str, topology: str):
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest: dict = {
            "run_id": run_id,
            "executor": executor,
            "topology": topology,
            "nodes": [],
        }

    def record(
        self,
        result: ExecutionResult,
        gate: GateResult | None = None,
        *,
        cwd: str | Path | None = None,
        integrity: IntegrityReport | None = None,
        repair: ExecutionResult | None = None,
        reviews: list[ReviewResult] | None = None,
        attempts: list[ExecutionResult] | None = None,
    ) -> None:
        node_dir = self.dir / result.node_id
        node_dir.mkdir(exist_ok=True)
        (node_dir / "output.txt").write_text(result.text or "")
        if result.events:
            (node_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(e) for e in result.events)
            )
        entry = {k: v for k, v in asdict(result).items() if k != "events"}
        if attempts and len(attempts) > 1:
            attempt_dir = node_dir / "attempts"
            attempt_dir.mkdir(exist_ok=True)
            attempt_entries = []
            for number, attempt in enumerate(attempts, start=1):
                attempt_entry = {
                    k: v for k, v in asdict(attempt).items() if k != "events"
                }
                attempt_entries.append(attempt_entry)
                (attempt_dir / f"{number}-output.txt").write_text(attempt.text or "")
                if attempt.events:
                    (attempt_dir / f"{number}-events.jsonl").write_text(
                        "\n".join(json.dumps(event) for event in attempt.events)
                    )
            entry["attempts"] = attempt_entries
            costs = [
                attempt.cost_usd
                for attempt in attempts
                if attempt.cost_usd is not None
            ]
            entry["cost_usd"] = sum(costs) if costs else None
        # Snapshot the AI-DLC artifacts the node wrote into its workspace, if any.
        if cwd is not None:
            src = Path(cwd) / "aidlc-docs"
            if src.is_dir():
                dst = node_dir / "aidlc-docs"
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                entry["aidlc_docs"] = f"{result.node_id}/aidlc-docs"
        if gate is not None:
            entry["gate"] = asdict(gate)
        if integrity is not None:
            integrity_data = asdict(integrity)
            entry["integrity"] = integrity_data
            (node_dir / "integrity.json").write_text(json.dumps(integrity_data, indent=2))
        if repair is not None:
            repair_entry = {k: v for k, v in asdict(repair).items() if k != "events"}
            entry["repair"] = repair_entry
            (node_dir / "integrity-repair.txt").write_text(repair.text or "")
            if repair.events:
                (node_dir / "integrity-repair.events.jsonl").write_text(
                    "\n".join(json.dumps(e) for e in repair.events)
                )
        if reviews:
            entry["human_reviews"] = [asdict(review) for review in reviews]
            (node_dir / "human-review.md").write_text(format_review_history(reviews))
        self.manifest["nodes"].append(entry)

    def finalize(self, ok: bool) -> Path:
        self.manifest["ok"] = ok
        (self.dir / "manifest.json").write_text(json.dumps(self.manifest, indent=2))
        return self.dir


def list_runs(root: str | Path) -> list[dict]:
    """Return the manifest of every run under ``root``, sorted by run id."""
    base = Path(root)
    if not base.is_dir():
        return []
    manifests: list[dict] = []
    for d in sorted(base.iterdir()):
        mf = d / "manifest.json"
        if mf.is_file():
            try:
                manifests.append(json.loads(mf.read_text()))
            except json.JSONDecodeError:
                continue
    return manifests


def read_manifest(root: str | Path, run_id: str) -> dict:
    """Load one run's manifest, or raise ``FileNotFoundError``."""
    mf = Path(root) / run_id / "manifest.json"
    if not mf.is_file():
        raise FileNotFoundError(mf)
    return json.loads(mf.read_text())

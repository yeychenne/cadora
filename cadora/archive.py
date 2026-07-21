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
from cadora.provenance import (
    conductor_fingerprint,
    fingerprint_workspace,
    write_workspace_manifest,
)
from cadora.remediation import RemediationOutcome
from cadora.review import ReviewResult, format_review_history


class RunArchive:
    def __init__(self, root: str | Path, run_id: str, executor: str, topology: str):
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.manifest: dict = {
            "run_id": run_id,
            "executor": executor,
            "topology": topology,
            # Which Cadora produced this evidence (version + git state for editable installs) —
            # without it, a conductor that changed mid-run is invisible in the pack.
            "conductor": conductor_fingerprint(),
            # A run id identifies the whole run, not one invocation of it. `--resume-from` opens a
            # SECOND invocation against the same id and only records the nodes it actually runs, so
            # starting from an empty list would write a manifest containing just those — deleting
            # the earlier nodes' cost, usage, and gate records from the evidence. Carry them
            # forward; `record` replaces by node_id, so a node that runs again updates in place.
            "nodes": _carry_forward_nodes(self.dir),
        }
        self._ws_cwd: str | Path | None = None
        self._ws_archive_root: str | Path | None = None

    def track_workspace(self, cwd: str | Path, archive_root: str | Path) -> None:
        """Register the run's workspace so :meth:`finalize` snapshots its content fingerprint.

        The snapshot is provenance (the pack records exactly what source the gates ran over) and
        the baseline a future ``--resume-from`` verifies against. Because it happens in
        ``finalize``, every terminal path is covered — success *and* the ``finalize(False)`` failure
        exits, which matter most since the run you resume is usually a failed one.
        """
        self._ws_cwd = cwd
        self._ws_archive_root = archive_root

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
        remediation: RemediationOutcome | None = None,
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
        if remediation is not None and remediation.attempts:
            remediation_dir = node_dir / "remediation"
            remediation_dir.mkdir(exist_ok=True)
            trail = []
            for attempt in remediation.attempts:
                execution_entry = {
                    k: v for k, v in asdict(attempt.execution).items() if k != "events"
                }
                trail.append(
                    {
                        "number": attempt.number,
                        "prompt": attempt.prompt,
                        "execution": execution_entry,
                        "gate": asdict(attempt.gate) if attempt.gate else None,
                        "integrity": asdict(attempt.integrity) if attempt.integrity else None,
                        "cost_usd": attempt.cost_usd,
                    }
                )
                (remediation_dir / f"{attempt.number}-prompt.txt").write_text(attempt.prompt)
                (remediation_dir / f"{attempt.number}-output.txt").write_text(
                    attempt.execution.text or ""
                )
                if attempt.execution.events:
                    (remediation_dir / f"{attempt.number}-events.jsonl").write_text(
                        "\n".join(json.dumps(e) for e in attempt.execution.events)
                    )
            remediation_costs = [
                a.cost_usd for a in remediation.attempts if a.cost_usd is not None
            ]
            remediation_cost = sum(remediation_costs) if remediation_costs else None
            entry["remediation"] = {
                "state": remediation.state,
                "blocked_reason": remediation.blocked_reason,
                "attempts": len(remediation.attempts),
                "cost_usd": remediation_cost,
                "final_gate": (
                    asdict(remediation.final_gate) if remediation.final_gate else None
                ),
                "final_integrity": (
                    asdict(remediation.final_integrity) if remediation.final_integrity else None
                ),
                "trail": trail,
            }
            if result.cost_usd is not None or remediation_cost is not None:
                entry["cost_usd"] = (result.cost_usd or 0.0) + (remediation_cost or 0.0)
        # Replace, don't append: on a resume (or a re-run under the same id) this node may already
        # be carried forward from an earlier invocation, and the manifest must hold one entry per
        # node, showing its latest outcome — never a duplicate pair.
        nodes = self.manifest["nodes"]
        for index, existing in enumerate(nodes):
            if existing.get("node_id") == entry.get("node_id"):
                nodes[index] = entry
                break
        else:
            nodes.append(entry)

    def finalize(self, ok: bool) -> Path:
        if self._ws_cwd is not None:
            try:
                write_workspace_manifest(
                    self.dir,
                    fingerprint_workspace(self._ws_cwd, archive_root=self._ws_archive_root),
                )
            except OSError:
                pass
        self.manifest["ok"] = ok
        (self.dir / "manifest.json").write_text(json.dumps(self.manifest, indent=2))
        return self.dir


def _carry_forward_nodes(run_dir: Path) -> list[dict]:
    """Node entries already recorded for this run id, so a later invocation extends them.

    A corrupt or unreadable manifest yields an empty list rather than aborting: refusing to start
    a run because a *previous* one left bad JSON would be worse than losing the carry-forward.
    """
    manifest = run_dir / "manifest.json"
    if not manifest.is_file():
        return []
    try:
        prior = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    nodes = prior.get("nodes")
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


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

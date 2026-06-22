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
    ) -> None:
        node_dir = self.dir / result.node_id
        node_dir.mkdir(exist_ok=True)
        (node_dir / "output.txt").write_text(result.text or "")
        if result.events:
            (node_dir / "events.jsonl").write_text(
                "\n".join(json.dumps(e) for e in result.events)
            )
        entry = {k: v for k, v in asdict(result).items() if k != "events"}
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

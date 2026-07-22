"""Park-and-exit — the durable form of a human-review gate.

A blocking gate holds the conductor process hostage to the reviewer's calendar: the laptop must
stay awake for as long as the human takes. Park-and-exit inverts that. When a run reaches review
gates under ``--on-review park``, it lets the current wave drain (siblings finish and are
recorded), writes ONE park record holding every pending gate, and terminates cleanly with a
distinct exit code. ``cadora resume <archive>/<run_id>`` continues the run later — the parked
nodes' agent work is **not** re-run and **not** re-paid; only the review happens.

The park record is deliberately **self-contained**: it embeds the topology, the resolved gate
specs, and the execution contract (backend, model, funding, budget policy…), so a resume depends
on nothing outside the archive — not the original YAML file, not the original shell. What it
does NOT contain is trust: the workspace fingerprint is written alongside it, and a resume
re-verifies both the fingerprint (drift refused unless ``--allow-drift``) and each pending gate
(deterministic gates re-run; a gate that no longer passes fails honestly).

Exit code 75 (``EX_TEMPFAIL``) — "waiting for a human" must be distinguishable from "broke", or
every wrapper and scheduler treats a parked run as a failure.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from cadora.executors.base import ExecutionResult
from cadora.gates import ShellGate
from cadora.topology import Node, Topology

PARK_FILE = "park.json"
PARK_SCHEMA = 1
# sysexits.h EX_TEMPFAIL: temporary condition, caller is invited to retry — exactly a parked gate.
PARK_EXIT_CODE = 75


def serialize_result(result: ExecutionResult) -> dict:
    return asdict(result)


def deserialize_result(data: dict) -> ExecutionResult:
    known = {f for f in ExecutionResult.__dataclass_fields__}
    return ExecutionResult(**{k: v for k, v in data.items() if k in known})


def topology_to_dict(topology: Topology) -> dict:
    return {"name": topology.name, "nodes": [asdict(node) for node in topology.nodes]}


def topology_from_dict(data: dict) -> Topology:
    known = {f for f in Node.__dataclass_fields__}
    nodes = [Node(**{k: v for k, v in n.items() if k in known}) for n in data.get("nodes", [])]
    return Topology(name=data.get("name", "resumed"), nodes=nodes)


def gates_to_dict(gates: dict[str, ShellGate]) -> dict:
    return {
        name: {"cmd": g.command, "setup": g.setup_mode, "wheelhouse": g.wheelhouse}
        for name, g in gates.items()
    }


def gates_from_dict(data: dict) -> dict[str, ShellGate]:
    return {
        name: ShellGate(
            name=name,
            command=spec.get("cmd") or "",
            setup_mode=spec.get("setup") or "off",
            wheelhouse=spec.get("wheelhouse"),
        )
        for name, spec in (data or {}).items()
    }


def write_park_record(run_dir: str | Path, record: dict) -> Path:
    """Atomically write the park record — a torn park.json would strand the run unresumable."""
    target = Path(run_dir) / PARK_FILE
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    tmp.replace(target)
    return target


def load_park_record(run_dir: str | Path) -> dict:
    """Load and sanity-check a park record; loud, actionable errors — this is a CLI entry path."""
    run_dir = Path(run_dir)
    path = run_dir / PARK_FILE
    if not path.is_file():
        raise SystemExit(
            f"no park record at {path} — either this run never parked, or it already resumed to "
            "completion (a finished run deletes its park record)"
        )
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"unreadable park record {path}: {exc}") from exc
    schema = record.get("schema")
    if schema != PARK_SCHEMA:
        raise SystemExit(
            f"park record {path} has schema {schema!r}; this cadora understands {PARK_SCHEMA} — "
            "resume with the cadora version that parked it"
        )
    for key in ("run_id", "topology", "pending", "contract"):
        if key not in record:
            raise SystemExit(f"park record {path} is missing {key!r} — refusing to guess")
    if not record["pending"]:
        raise SystemExit(f"park record {path} has no pending gates — nothing to resume")
    return record

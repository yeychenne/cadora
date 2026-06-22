"""The DAG runner — wires topology + executor + gates + archive together.

For each dependency wave, render each node's prompt (with upstream outputs), run
it on the chosen backend, apply its post-step gate, snapshot any artifacts, and
record to the archive. A blocking gate failure (or executor failure) stops the run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cadora.archive import RunArchive
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.gates import GateResult, ShellGate
from cadora.topology import Node, Topology, topo_sort


def run_topology(
    topology: Topology,
    executor: NodeExecutor,
    *,
    run_id: str,
    cwd: str = ".",
    archive_root: str = "runs",
    gates: dict[str, ShellGate] | None = None,
) -> Path:
    gates = gates or {}
    # Pre-flight: every referenced gate must be registered (fail fast, before any agent runs).
    unknown = sorted({n.gate for n in topology.nodes if n.gate and n.gate not in gates})
    if unknown:
        raise SystemExit(
            f"topology references unregistered gate(s): {unknown}; registered: {sorted(gates)}"
        )

    archive = RunArchive(archive_root, run_id, executor.name, topology.name)
    outputs: dict[str, str] = {}
    funding = getattr(executor, "funding", None)
    _log(
        f"cadora · executor={executor.name}"
        + (f" · funding={funding}" if funding else "")
        + f" · run={run_id}"
    )

    for wave in topo_sort(topology):
        # TODO: run nodes within a wave concurrently — they are independent.
        for node in wave:
            node_cwd = node.cwd or cwd
            result = executor.run(node, _render(node, outputs), cwd=node_cwd)
            outputs[node.id] = result.text

            gate_result = gates[node.gate].check(node_cwd) if node.gate else None
            archive.record(result, gate_result, cwd=node_cwd)
            _log(_node_line(node, result, gate_result))

            if not result.ok or (gate_result and not gate_result.passed):
                out = archive.finalize(False)
                reason = "executor failed" if not result.ok else f"gate {node.gate!r} blocked"
                _log(f"✗ stopped at node {node.id!r}: {reason}  ->  {out}")
                raise SystemExit(f"node {node.id!r}: {reason}")

    out = archive.finalize(True)
    _log(f"✓ run complete -> {out}")
    return out


def _render(node: Node, outputs: dict[str, str]) -> str:
    """Compose a node's prompt with its upstream outputs (UPSTREAM ARTIFACTS).

    TODO: richer manifest format so downstream nodes read upstream artifacts
    by UUID-prefixed path rather than inlined text.
    """
    upstream = "\n\n".join(
        f"## Output of upstream node `{dep}`\n{outputs.get(dep, '')}"
        for dep in node.depends_on
    )
    return f"{node.prompt}\n\n{upstream}".strip()


def _node_line(node: Node, result: ExecutionResult, gate: GateResult | None) -> str:
    bits = [f"  {'✓' if result.ok else '✗'} {node.id}"]
    if result.cost_usd is not None:
        bits.append(f"${result.cost_usd:.4f}")
    if result.meta.get("funding_resolved"):
        bits.append(f"funding={result.meta['funding_resolved']}")
    if gate is not None:
        bits.append(f"gate:{gate.name} {'ok' if gate.passed else 'BLOCKED'}")
    return "   ".join(bits)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)

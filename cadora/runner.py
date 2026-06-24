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
from cadora.gates import GATE_BLOCKED_PREREQUISITE, GateResult, ShellGate
from cadora.integrity import IntegrityReport, repair_prompt, scan_toolchain_integrity
from cadora.review import (
    REVIEW_ABORT,
    REVIEW_APPROVE,
    REVIEW_REQUEST_CHANGES,
    ReviewResult,
)
from cadora.topology import Node, Topology, topo_sort

MAX_REVIEW_REVISIONS = 3


def run_topology(
    topology: Topology,
    executor: NodeExecutor,
    *,
    run_id: str,
    cwd: str = ".",
    archive_root: str = "runs",
    gates: dict[str, ShellGate] | None = None,
    integrity_mode: str = "audit",
    hitl: bool = False,
    review_fn=None,
) -> Path:
    if integrity_mode not in {"off", "audit", "enforce", "repair"}:
        raise ValueError(f"invalid integrity mode: {integrity_mode!r}")
    gates = gates or {}
    review_fn = review_fn or _stdin_review
    reviews: dict[str, str] = {}
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
            base_prompt = _render(node, outputs, reviews)
            revision_comments = ""
            review_history: list[ReviewResult] = []
            attempt_results: list[ExecutionResult] = []

            while True:
                prompt = base_prompt
                if revision_comments:
                    prompt += (
                        "\n\n## Human review of your previous attempt — revise this same stage\n"
                        + revision_comments
                    )
                result = executor.run(node, prompt, cwd=node_cwd)
                attempt_results.append(result)
                gate_result = gates[node.gate].check(node_cwd) if node.gate else None
                integrity = (
                    scan_toolchain_integrity(node_cwd)
                    if integrity_mode != "off"
                    else None
                )
                repair_result = None
                if integrity_mode == "repair" and _needs_repair(gate_result, integrity):
                    repair_node = Node(
                        id=f"{node.id}-integrity-repair",
                        role="integrity-repair",
                        prompt="",
                        tools=node.tools,
                        model=node.model,
                    )
                    repair_result = executor.run(
                        repair_node,
                        repair_prompt(
                            integrity or IntegrityReport(True),
                            _gate_detail(gate_result),
                        ),
                        cwd=node_cwd,
                    )
                    gate_result = gates[node.gate].check(node_cwd) if node.gate else None
                    integrity = scan_toolchain_integrity(node_cwd)

                integrity_blocked = (
                    integrity_mode in {"enforce", "repair"}
                    and integrity is not None
                    and not integrity.passed
                )
                repair_failed = repair_result is not None and not repair_result.ok
                failed = (
                    not result.ok
                    or repair_failed
                    or (gate_result is not None and not gate_result.passed)
                    or integrity_blocked
                )

                if failed:
                    archive.record(
                        result,
                        gate_result,
                        cwd=node_cwd,
                        integrity=integrity,
                        repair=repair_result,
                        reviews=review_history,
                        attempts=attempt_results,
                    )
                    out = archive.finalize(False)
                    reason = _failure_reason(
                        node, result, gate_result, integrity_blocked, repair_failed
                    )
                    _log(_node_line(node, result, gate_result, integrity, repair_result))
                    _log(f"✗ stopped at node {node.id!r}: {reason}  ->  {out}")
                    raise SystemExit(f"node {node.id!r}: {reason}")

                if hitl and node.review:
                    review = review_fn(node, node_cwd)
                    if not isinstance(review, ReviewResult):
                        raise TypeError("review_fn must return ReviewResult")
                    review_history.append(review)
                    if review.decision == REVIEW_REQUEST_CHANGES:
                        revisions = sum(
                            item.decision == REVIEW_REQUEST_CHANGES
                            for item in review_history
                        )
                        if revisions >= MAX_REVIEW_REVISIONS:
                            archive.record(
                                result,
                                gate_result,
                                cwd=node_cwd,
                                integrity=integrity,
                                repair=repair_result,
                                reviews=review_history,
                                attempts=attempt_results,
                            )
                            out = archive.finalize(False)
                            raise SystemExit(
                                f"node {node.id!r}: human review revision limit exceeded "
                                f"-> {out}"
                            )
                        revision_comments = review.comments
                        continue
                    if review.decision == REVIEW_ABORT:
                        archive.record(
                            result,
                            gate_result,
                            cwd=node_cwd,
                            integrity=integrity,
                            repair=repair_result,
                            reviews=review_history,
                            attempts=attempt_results,
                        )
                        out = archive.finalize(False)
                        raise SystemExit(
                            f"node {node.id!r}: human review aborted run -> {out}"
                        )
                    reviews[node.id] = review.comments

                outputs[node.id] = result.text
                archive.record(
                    result,
                    gate_result,
                    cwd=node_cwd,
                    integrity=integrity,
                    repair=repair_result,
                    reviews=review_history,
                    attempts=attempt_results,
                )
                _log(_node_line(node, result, gate_result, integrity, repair_result))
                break

    out = archive.finalize(True)
    _log(f"✓ run complete -> {out}")
    return out


def _render(node: Node, outputs: dict[str, str], reviews: dict[str, str] | None = None) -> str:
    """Compose a node's prompt with its upstream outputs and any human review of them.

    TODO: richer manifest format so downstream nodes read upstream artifacts
    by UUID-prefixed path rather than inlined text.
    """
    reviews = reviews or {}
    parts: list[str] = []
    for dep in node.depends_on:
        parts.append(f"## Output of upstream node `{dep}`\n{outputs.get(dep, '')}")
        if reviews.get(dep):
            parts.append(
                f"## Human review of `{dep}` — you MUST address these comments\n{reviews[dep]}"
            )
    upstream = "\n\n".join(parts)
    return f"{node.prompt}\n\n{upstream}".strip()


def _node_line(
    node: Node,
    result: ExecutionResult,
    gate: GateResult | None,
    integrity: IntegrityReport | None = None,
    repair: ExecutionResult | None = None,
) -> str:
    bits = [f"  {'✓' if result.ok else '✗'} {node.id}"]
    if result.cost_usd is not None:
        bits.append(f"${result.cost_usd:.4f}")
    if result.meta.get("funding_resolved"):
        bits.append(f"funding={result.meta['funding_resolved']}")
    if gate is not None:
        gate_mark = "ok" if gate.passed else gate.status.upper()
        bits.append(f"gate:{gate.name} {gate_mark}")
    if integrity is not None:
        mark = "ok" if integrity.passed else f"{integrity.blocking_count} BLOCKING"
        bits.append(f"integrity:{mark}")
    if repair is not None:
        bits.append(f"repair:{'ok' if repair.ok else 'FAILED'}")
    return "   ".join(bits)


def _needs_repair(
    gate: GateResult | None,
    integrity: IntegrityReport | None,
) -> bool:
    repairable_gate = (
        gate is not None
        and not gate.passed
        and gate.status != GATE_BLOCKED_PREREQUISITE
    )
    return bool(repairable_gate or (integrity and not integrity.passed))


def _gate_detail(gate: GateResult | None) -> str:
    return gate.detail if gate is not None else ""


def _failure_reason(
    node: Node,
    result: ExecutionResult,
    gate: GateResult | None,
    integrity_blocked: bool,
    repair_failed: bool,
) -> str:
    if not result.ok:
        return "executor failed"
    if repair_failed:
        return "integrity repair failed"
    if gate and not gate.passed:
        if gate.status == GATE_BLOCKED_PREREQUISITE:
            missing = ", ".join(gate.missing_prerequisites) or "gate tooling"
            return f"gate {node.gate!r} blocked by missing prerequisite(s): {missing}"
        return f"gate {node.gate!r} blocked"
    if integrity_blocked:
        return "toolchain integrity blocked"
    return "run blocked"


def _stdin_review(node: Node, node_cwd: str) -> ReviewResult:
    """Read an explicit review decision; unavailable or closed input fails closed."""
    docs = Path(node_cwd) / "aidlc-docs"
    _log("")
    _log(f"=== HITL REVIEW GATE — stage {node.id!r} complete ===")
    _log(f"Review the documents in: {docs}")
    if not sys.stdin.isatty():
        return ReviewResult(
            REVIEW_ABORT,
            "interactive review unavailable: stdin is not a TTY",
        )

    try:
        choice = input("Decision: [a]pprove, [r]equest changes, [x]abort: ").strip().lower()
    except EOFError:
        return ReviewResult(REVIEW_ABORT, "interactive review input closed unexpectedly")

    if choice in {"a", "approve"}:
        return ReviewResult(REVIEW_APPROVE)
    if choice in {"x", "abort"}:
        return ReviewResult(REVIEW_ABORT, "operator aborted at human review gate")
    if choice not in {"r", "request_changes", "request changes"}:
        return ReviewResult(REVIEW_ABORT, f"invalid review decision: {choice or '(empty)'}")

    _log("Enter required changes; finish with a line containing only EOF.")
    lines: list[str] = []
    try:
        while True:
            line = input()
            if line.strip() == "EOF":
                break
            lines.append(line)
    except EOFError:
        return ReviewResult(REVIEW_ABORT, "review comments input closed unexpectedly")
    comments = "\n".join(lines).strip()
    if not comments:
        return ReviewResult(REVIEW_ABORT, "request_changes requires reviewer comments")
    return ReviewResult(REVIEW_REQUEST_CHANGES, comments)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)

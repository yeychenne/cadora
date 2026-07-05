"""The DAG runner — wires topology + executor + gates + archive together.

For each dependency wave, render each node's prompt (with upstream outputs), run
it on the chosen backend, apply its post-step gate, snapshot any artifacts, and
record to the archive. A blocking gate failure (or executor failure) stops the run.
"""

from __future__ import annotations

import contextlib
import itertools
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from cadora.archive import RunArchive
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.gates import GATE_BLOCKED_PREREQUISITE, GateResult, ShellGate
from cadora.integrity import IntegrityReport, repair_prompt, scan_toolchain_integrity
from cadora.remediation import (
    STATE_COMPLETED_GREEN,
    STATE_HONEST_BLOCKED,
    RemediationOutcome,
    RemediationPolicy,
    needs_remediation,
    run_remediation,
)
from cadora.review import (
    REVIEW_ABORT,
    REVIEW_APPROVE,
    REVIEW_REQUEST_CHANGES,
    ReviewResult,
)
from cadora.telemetry import RunTelemetry
from cadora.topology import Node, Topology, topo_sort

MAX_REVIEW_REVISIONS = 3


@contextlib.contextmanager
def _stage_progress(node: Node, executor: NodeExecutor):
    """Announce the running stage and show a live elapsed-time heartbeat.

    The executor captures the agent's output, so without this the terminal is silent for the
    minutes a stage takes. Heartbeat is TTY-only — it no-ops when output is piped or captured.
    """
    model = node.model or getattr(executor, "model", None) or "default model"
    _log(f"▶ {node.id} · {model} · running… (generating documents; this can take a few minutes)")
    stop = threading.Event()

    def _beat() -> None:
        if not sys.stderr.isatty():
            return
        spinner = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")
        start = time.monotonic()
        while not stop.wait(0.5):
            secs = int(time.monotonic() - start)
            sys.stderr.write(f"\r  {next(spinner)} {node.id} running… {secs // 60}m{secs % 60:02d}s ")
            sys.stderr.flush()
        sys.stderr.write("\r" + " " * 60 + "\r")  # clear the heartbeat line
        sys.stderr.flush()

    beat = threading.Thread(target=_beat, daemon=True)
    beat.start()
    try:
        yield
    finally:
        stop.set()
        beat.join(timeout=1)


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
    construction_executor: NodeExecutor | None = None,
    remediation_policy: RemediationPolicy | None = None,
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
    telemetry = RunTelemetry(archive_root, run_id, topology, executor.name)
    telemetry.run_started()
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
            # Phase-aware routing: construction nodes use a dedicated executor if configured.
            node_executor = (
                construction_executor
                if construction_executor and node.phase == "construction"
                else executor
            )
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
                telemetry.node_started(
                    node.id,
                    model=node.model or getattr(node_executor, "model", None),
                )
                with _stage_progress(node, node_executor):
                    result = node_executor.run(node, prompt, cwd=node_cwd)
                result.executor = node_executor.name  # per-node backend, for cost attribution
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
                    repair_result = node_executor.run(
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

                remediation_outcome: RemediationOutcome | None = None
                if failed and needs_remediation(
                    gate_result, integrity, integrity_mode, remediation_policy
                ):
                    gate_obj = gates.get(node.gate) if node.gate else None
                    remediation_outcome = run_remediation(
                        node,
                        node_executor,
                        node_cwd,
                        gate_obj,
                        gate_result,
                        integrity,
                        integrity_mode,
                        remediation_policy,
                    )
                    gate_result = remediation_outcome.final_gate
                    integrity = remediation_outcome.final_integrity
                    if remediation_outcome.state == STATE_COMPLETED_GREEN:
                        failed = False

                node_cost = result.cost_usd
                if remediation_outcome is not None and remediation_outcome.cost_usd is not None:
                    node_cost = (node_cost or 0.0) + remediation_outcome.cost_usd

                if failed:
                    archive.record(
                        result,
                        gate_result,
                        cwd=node_cwd,
                        integrity=integrity,
                        repair=repair_result,
                        reviews=review_history,
                        attempts=attempt_results,
                        remediation=remediation_outcome,
                    )
                    out = archive.finalize(False)
                    reason = _failure_reason(
                        node, result, gate_result, integrity_blocked, repair_failed,
                        remediation_outcome,
                    )
                    telemetry.node_recorded(
                        node.id,
                        ok=False,
                        model=result.model,
                        cost_usd=node_cost,
                        usage=result.usage,
                        gate=asdict(gate_result) if gate_result else None,
                        integrity=asdict(integrity) if integrity else None,
                        review=review_history[-1].decision if review_history else None,
                        error=reason,
                    )
                    telemetry.run_completed(False, error=f"node {node.id!r}: {reason}")
                    _log(_node_line(node, result, gate_result, integrity, repair_result, remediation_outcome))
                    _log(f"✗ stopped at node {node.id!r}: {reason}  ->  {out}")
                    raise SystemExit(f"node {node.id!r}: {reason}")

                if hitl and node.review:
                    telemetry.review_waiting(node.id)
                    review = review_fn(node, node_cwd)
                    if not isinstance(review, ReviewResult):
                        raise TypeError("review_fn must return ReviewResult")
                    review_history.append(review)
                    telemetry.review_resolved(node.id, review.decision)
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
                                remediation=remediation_outcome,
                            )
                            out = archive.finalize(False)
                            telemetry.node_recorded(
                                node.id,
                                ok=False,
                                model=result.model,
                                cost_usd=node_cost,
                                usage=result.usage,
                                gate=asdict(gate_result) if gate_result else None,
                                integrity=asdict(integrity) if integrity else None,
                                review=review.decision,
                                error="human review revision limit exceeded",
                            )
                            telemetry.run_completed(
                                False,
                                error=f"node {node.id!r}: human review revision limit exceeded",
                            )
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
                            remediation=remediation_outcome,
                        )
                        out = archive.finalize(False)
                        telemetry.node_recorded(
                            node.id,
                            ok=False,
                            model=result.model,
                            cost_usd=node_cost,
                            usage=result.usage,
                            gate=asdict(gate_result) if gate_result else None,
                            integrity=asdict(integrity) if integrity else None,
                            review=review.decision,
                            error="human review aborted run",
                        )
                        telemetry.run_completed(
                            False,
                            error=f"node {node.id!r}: human review aborted run",
                        )
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
                    remediation=remediation_outcome,
                )
                telemetry.node_recorded(
                    node.id,
                    ok=True,
                    model=result.model,
                    cost_usd=node_cost,
                    usage=result.usage,
                    gate=asdict(gate_result) if gate_result else None,
                    integrity=asdict(integrity) if integrity else None,
                    review=review_history[-1].decision if review_history else None,
                )
                _log(_node_line(node, result, gate_result, integrity, repair_result, remediation_outcome))
                break

    out = archive.finalize(True)
    telemetry.run_completed(True)
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
    remediation: RemediationOutcome | None = None,
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
    if remediation is not None:
        bits.append(f"remediate:{remediation.state} x{len(remediation.attempts)}")
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
    remediation: RemediationOutcome | None = None,
) -> str:
    if not result.ok:
        return "executor failed"
    if repair_failed:
        return "integrity repair failed"
    base = _gate_failure_reason(node, gate, integrity_blocked)
    if remediation is not None and remediation.state == STATE_HONEST_BLOCKED:
        return (
            f"{base} — remediation exhausted after {len(remediation.attempts)} attempt(s) "
            f"({remediation.blocked_reason})"
        )
    return base


def _gate_failure_reason(
    node: Node,
    gate: GateResult | None,
    integrity_blocked: bool,
) -> str:
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

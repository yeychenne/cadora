"""The DAG runner — wires topology + executor + gates + archive together.

For each dependency wave, render each node's prompt (with upstream outputs), run
it on the chosen backend, apply its post-step gate, snapshot any artifacts, and
record to the archive. A blocking gate failure (or executor failure) stops the run.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import itertools
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from cadora.archive import RunArchive
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.gates import GATE_BLOCKED_PREREQUISITE, GateResult, ShellGate
from cadora.integrity import IntegrityReport, repair_prompt, scan_toolchain_integrity
from cadora.provenance import (
    diff_fingerprints,
    fingerprint_workspace,
    latest_prior_fingerprint,
)
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
from cadora.telemetry import RunTelemetry, _now
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
    max_parallel: int = 1,
    resume_from: str | None = None,
    skip: list[str] | None = None,
    allow_drift: bool = False,
) -> Path:
    if integrity_mode not in {"off", "audit", "enforce", "repair"}:
        raise ValueError(f"invalid integrity mode: {integrity_mode!r}")
    max_parallel = max(1, max_parallel)
    gates = gates or {}
    review_fn = review_fn or _stdin_review
    reviews: dict[str, str] = {}
    # Pre-flight: every referenced gate must be registered (fail fast, before any agent runs).
    unknown = sorted({n.gate for n in topology.nodes if n.gate and n.gate not in gates})
    if unknown:
        raise SystemExit(
            f"topology references unregistered gate(s): {unknown}; registered: {sorted(gates)}"
        )
    # Resolve --resume-from / --skip up front (fail fast on unknown node names, before any agent).
    skip_ids, skipped_sorted = _compute_skip_set(topology, resume_from, skip)

    archive = RunArchive(archive_root, run_id, executor.name, topology.name)
    archive.track_workspace(cwd, archive_root)
    telemetry = RunTelemetry(archive_root, run_id, topology, executor.name)
    telemetry.run_started()
    _write_run_input(archive_root, run_id, topology, cwd)
    if resume_from or skip_ids:
        telemetry.mark_resume(resume_from, skipped_sorted)
        _log(
            "↩ resume: skipping "
            + (", ".join(skipped_sorted) or "(none)")
            + (f" · running from {resume_from!r}" if resume_from else "")
        )
        # A resume trusts the skipped nodes' artifacts already in --cwd. VERIFY that trust against
        # the last run's recorded fingerprint instead of assuming it — a drifted workspace must not
        # silently certify gates over source that never passed the earlier stages.
        resume_drift = _verify_resume_workspace(cwd, archive_root, run_id, allow_drift)
        archive.manifest["resume"] = {
            "resume_from": resume_from,
            "skipped": skipped_sorted,
            "allow_drift": allow_drift,
            "workspace_drift": resume_drift.as_dict() if resume_drift else None,
        }
    outputs: dict[str, str] = {}
    funding = getattr(executor, "funding", None)
    _log(
        f"cadora · executor={executor.name}"
        + (f" · funding={funding}" if funding else "")
        + f" · run={run_id}"
    )

    for wave in topo_sort(topology):
        # Independent nodes in a wave: pre-run their INITIAL agent execution concurrently — the
        # minutes-long part. Gates, integrity, remediation, review, and archiving stay sequential
        # and deterministic in the per-node loop below, so the manifest order and all fail-closed
        # semantics are unchanged; only wall-clock improves.
        runnable = [n for n in wave if n.id not in skip_ids]
        prepared = (
            _execute_wave_concurrently(
                runnable, outputs, reviews, executor, construction_executor, cwd, hitl,
                max_parallel, skip_ids,
            )
            if max_parallel > 1 and len(runnable) > 1
            else {}
        )
        for node in wave:
            if node.id in skip_ids:
                telemetry.node_skipped(
                    node.id,
                    reason=(f"resumed from {resume_from!r}" if resume_from else "explicitly skipped"),
                )
                _log(f"↩ skip {node.id!r} — artifacts trusted in the workspace")
                continue
            node_cwd = node.cwd or cwd
            # Phase-aware routing: construction nodes use a dedicated executor if configured.
            node_executor = (
                construction_executor
                if construction_executor and node.phase == "construction"
                else executor
            )
            base_prompt = _render(node, outputs, reviews, skip_ids)
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
                # A wave node's agent already ran (concurrently) before the loop reached it, so its
                # span must START at the real executor start captured in the worker thread —
                # otherwise duration_seconds times only the gate step and the agent's work is
                # attributed to no node at all.
                from_wave = not revision_comments and node.id in prepared
                telemetry.node_started(
                    node.id,
                    model=node.model or getattr(node_executor, "model", None),
                    at=prepared[node.id][2] if from_wave else None,
                )
                if from_wave:
                    # Initial execution already run concurrently for this wave; the review-doc
                    # snapshot was taken there too (so the HITL gate scopes correctly).
                    pre_review_docs, result, _ = prepared[node.id]
                else:
                    # Snapshot review docs BEFORE this attempt so the HITL gate can surface
                    # exactly what this stage (or its revision) produced — not the whole tree.
                    pre_review_docs = _doc_snapshot(node_cwd) if (hitl and node.review) else None
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
                    documents = _changed_docs(node_cwd, pre_review_docs or {})
                    review = _invoke_review(review_fn, node, node_cwd, documents)
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

    # The workspace fingerprint (provenance + the baseline a future --resume-from verifies against)
    # is captured inside archive.finalize(), so it covers the failure exits too — see track_workspace.
    out = archive.finalize(True)
    telemetry.run_completed(True)
    _log(f"✓ run complete -> {out}")
    return out


def _execute_wave_concurrently(
    wave, outputs, reviews, executor, construction_executor, cwd, hitl, max_parallel, skipped=None
) -> dict[str, tuple[dict[str, str] | None, ExecutionResult, str]]:
    """Run the INITIAL agent execution of a wave's independent nodes concurrently.

    Only the (minutes-long) executor call is parallelized; every gate / integrity / remediation /
    review / archive step stays sequential and deterministic in the caller. Same-wave nodes are
    independent by construction (no ``depends_on`` among them) and neither ``outputs`` nor
    ``reviews`` is mutated until the whole wave is processed, so these concurrent reads are safe.

    Returns ``{node.id: (pre_review_docs, result, started_at)}``. The review-doc snapshot is taken
    here (before the execution) so a concurrently-run review node still scopes its HITL gate
    correctly, and ``started_at`` is the node's REAL executor start — captured in this thread,
    because by the time the sequential loop reaches the node its agent has already finished. The
    caller hands that timestamp to telemetry so ``duration_seconds`` covers the agent's work rather
    than just the gate step.
    """

    def _run(node: Node) -> tuple[str, tuple[dict[str, str] | None, ExecutionResult, str]]:
        node_cwd = node.cwd or cwd
        node_executor = (
            construction_executor
            if construction_executor and node.phase == "construction"
            else executor
        )
        pre_review_docs = _doc_snapshot(node_cwd) if (hitl and node.review) else None
        started_at = _now()  # real agent start, inside the worker thread
        result = node_executor.run(node, _render(node, outputs, reviews, skipped), cwd=node_cwd)
        result.executor = node_executor.name
        return node.id, (pre_review_docs, result, started_at)

    _log(f"▶ wave · {len(wave)} independent nodes running concurrently (max {max_parallel})")
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(wave))) as pool:
        return dict(pool.map(_run, wave))


def _compute_skip_set(
    topology: Topology, resume_from: str | None, skip: list[str] | None
) -> tuple[set[str], list[str]]:
    """Resolve which node ids to skip for ``--resume-from`` / ``--skip``.

    ``--skip`` names nodes directly. ``--resume-from X`` skips every node that is neither ``X`` nor
    downstream of ``X`` (``X`` and its transitive descendants run; everything earlier or unrelated
    is trusted to already exist in the workspace). Both validate that every named node exists, so a
    typo fails fast — before any agent runs. Returns ``(skip_ids, sorted_skip_ids)``.
    """
    ids = {n.id for n in topology.nodes}
    requested = set(skip or [])
    named = requested | ({resume_from} if resume_from else set())
    unknown = sorted(name for name in named if name not in ids)
    if unknown:
        raise SystemExit(
            f"--resume-from/--skip references unknown node(s): {unknown}; "
            f"topology has: {sorted(ids)}"
        )
    skip_ids = set(requested)
    if resume_from:
        parents = {n.id: set(n.depends_on) for n in topology.nodes}

        def _descends_from(node_id: str, target: str) -> bool:
            seen: set[str] = set()
            stack = list(parents.get(node_id, ()))
            while stack:
                cur = stack.pop()
                if cur == target:
                    return True
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(parents.get(cur, ()))
            return False

        run_set = {resume_from} | {nid for nid in ids if _descends_from(nid, resume_from)}
        skip_ids |= ids - run_set
    skip_ids.discard(resume_from or "")  # the resume point itself always runs
    return skip_ids, sorted(skip_ids)


def _verify_resume_workspace(cwd, archive_root, run_id, allow_drift):
    """Verify a resume's workspace against the most recent prior run's fingerprint.

    Returns the :class:`WorkspaceDrift` (possibly empty) or ``None`` when there is no baseline to
    check against. Raises ``SystemExit`` on drift unless ``allow_drift`` is set — a resumed run must
    not silently certify gates over source that never matched the run it claims to continue. When
    drift is allowed, it is logged and recorded in the run manifest so the evidence stays honest.
    """
    prior = latest_prior_fingerprint(archive_root, exclude_run_id=run_id)
    if prior is None:
        _log("  ↳ no prior workspace manifest to verify against — resuming on trust")
        return None
    baseline_run, baseline = prior
    drift = diff_fingerprints(
        baseline, fingerprint_workspace(cwd, archive_root=archive_root), baseline_run=baseline_run
    )
    if not drift.has_drift:
        _log(f"  ↳ workspace verified against {baseline_run} — no drift ({len(baseline)} files)")
        return drift
    detail = _format_drift(drift)
    if allow_drift:
        _log(
            f"  ⚠ workspace DRIFTED since {baseline_run} ({drift.summary()}) — "
            "proceeding under --allow-drift"
        )
        for line in detail:
            _log(f"      {line}")
        _log("    this run's evidence will record that it resumed against a drifted workspace")
        return drift
    raise SystemExit(
        f"✗ resume refused: workspace drifted since {baseline_run} ({drift.summary()}).\n"
        + "\n".join(f"    {line}" for line in detail)
        + "\n  The skipped nodes' artifacts no longer match the run you are resuming, so the gates\n"
        "  would certify source that never passed the earlier stages. Re-run from scratch, or pass\n"
        "  --allow-drift to resume anyway (the drift is recorded in the evidence pack)."
    )


def _format_drift(drift, limit: int = 12) -> list[str]:
    """Human-readable, bounded listing of a workspace drift (most-actionable classes first)."""
    lines: list[str] = []
    for tag, items in (
        ("modified", drift.modified),
        ("removed", drift.removed),
        ("added", drift.added),
    ):
        for path in items[:limit]:
            lines.append(f"{tag:>8}: {path}")
        if len(items) > limit:
            lines.append(f"{'':>8}  … +{len(items) - limit} more {tag}")
    return lines


def _write_run_input(archive_root: str, run_id: str, topology: Topology, cwd: str) -> None:
    """Capture *the prompt given at entry* into the run archive.

    The DAG's entry points are the root nodes (no ``depends_on``); their prompts are what the run
    was actually launched with. If the workspace carries a ``vision.md`` (the human's original
    request), include it too. Surfaced by the dashboard's run-detail view; best-effort — a failure
    here never breaks the run.
    """
    try:
        roots = [
            {"node_id": n.id, "role": n.role, "prompt": n.prompt}
            for n in topology.nodes
            if not n.depends_on
        ]
        vision = None
        vpath = Path(cwd) / "vision.md"
        if vpath.is_file():
            vision = vpath.read_text(errors="replace")[:20000]
        out = Path(archive_root) / run_id / "run-input.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "topology": topology.name,
                    # The live workspace, so an out-of-process surface (the dashboard) can find the
                    # documents a --review-file gate is waiting on and drop the decision back.
                    "cwd": str(Path(cwd).resolve()),
                    "roots": roots,
                    "vision": vision,
                },
                indent=2,
            )
        )
    except OSError:
        pass


def _render(
    node: Node,
    outputs: dict[str, str],
    reviews: dict[str, str] | None = None,
    skipped: set[str] | None = None,
) -> str:
    """Compose a node's prompt with its upstream outputs and any human review of them.

    TODO: richer manifest format so downstream nodes read upstream artifacts
    by UUID-prefixed path rather than inlined text.
    """
    reviews = reviews or {}
    skipped = skipped or set()
    parts: list[str] = []
    for dep in node.depends_on:
        if dep in skipped:
            # Resumed run: the upstream node did not execute here, so there is no piped output —
            # its artifacts already live in the workspace from an earlier run. Point the agent at
            # them rather than injecting an empty section.
            parts.append(
                f"## Upstream node `{dep}` (resumed — not re-run this run)\n"
                "Its artifacts already exist in the workspace from an earlier run; "
                "read them directly from disk as needed."
            )
        else:
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


def _executor_error_detail(result: ExecutionResult) -> str:
    """A short, human-readable *why* for an executor failure — timeout, exit code, stderr tail.

    Turns the bare "executor failed" into e.g. "executor failed (exit 1: <stderr line>)" or
    "executor failed (timed out after 600s)", so the archived error says what actually happened.
    """
    parts: list[str] = []
    if result.meta.get("timed_out"):
        parts.append(f"timed out after {result.meta.get('timeout_seconds')}s")
    elif result.exit_code is not None:
        parts.append(f"exit {result.exit_code}")
    tail = str(result.meta.get("stderr_tail") or "").strip()
    if tail:
        parts.append(tail.splitlines()[-1][:200])
    return f" ({': '.join(parts)})" if parts else ""


def _failure_reason(
    node: Node,
    result: ExecutionResult,
    gate: GateResult | None,
    integrity_blocked: bool,
    repair_failed: bool,
    remediation: RemediationOutcome | None = None,
) -> str:
    if not result.ok:
        return "executor failed" + _executor_error_detail(result)
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


_REVIEW_DOC_DIR = "aidlc-docs"
_REVIEW_PREVIEW_CHARS = 1200


def _doc_snapshot(node_cwd: str) -> dict[str, str]:
    """Content-hash every review document under the workspace's doc dir."""
    root = Path(node_cwd) / _REVIEW_DOC_DIR
    snapshot: dict[str, str] = {}
    if not root.is_dir():
        return snapshot
    for path in root.rglob("*"):
        if path.is_file():
            try:
                snapshot[str(path.relative_to(node_cwd))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            except OSError:
                continue
    return snapshot


def _changed_docs(node_cwd: str, before: dict[str, str]) -> list[tuple[str, str]]:
    """``(relpath, "new"|"modified")`` for docs written or changed since ``before``.

    This is how the HITL gate surfaces *the document to review*: the artifact(s) THIS
    stage actually produced, not the whole (growing) doc tree.
    """
    after = _doc_snapshot(node_cwd)
    changed: list[tuple[str, str]] = []
    for relpath in sorted(after):
        if relpath not in before:
            changed.append((relpath, "new"))
        elif after[relpath] != before[relpath]:
            changed.append((relpath, "modified"))
    return changed


def _invoke_review(review_fn, node: Node, node_cwd: str, documents: list[tuple[str, str]]):
    """Call ``review_fn``, passing the scoped documents only if it accepts a third arg.

    Keeps backward compatibility with 2-arg ``review_fn(node, cwd)`` callbacks.
    """
    try:
        accepts_documents = len(inspect.signature(review_fn).parameters) >= 3
    except (TypeError, ValueError):
        accepts_documents = False
    if accepts_documents:
        return review_fn(node, node_cwd, documents)
    return review_fn(node, node_cwd)


def _surface_documents(node_cwd: str, documents: list[tuple[str, str]] | None) -> None:
    """Print the specific document(s) this stage produced, with a short preview each."""
    docs_dir = Path(node_cwd) / _REVIEW_DOC_DIR
    if not documents:
        _log(f"Review the documents in: {docs_dir}")
        return
    _log(f"This stage produced {len(documents)} document(s) to review:")
    for relpath, kind in documents:
        _log(f"  [{kind:>8}] {relpath}")
    for relpath, kind in documents:
        full = Path(node_cwd) / relpath
        _log(f"\n----- {relpath} ({kind}) -----")
        try:
            text = full.read_text()
        except OSError as exc:
            _log(f"(could not read: {exc})")
            continue
        if len(text) > _REVIEW_PREVIEW_CHARS:
            _log(text[:_REVIEW_PREVIEW_CHARS].rstrip() + f"\n… [preview truncated — full document at {full}]")
        else:
            _log(text)
    _log("")


def _stdin_review(
    node: Node, node_cwd: str, documents: list[tuple[str, str]] | None = None
) -> ReviewResult:
    """Read an explicit review decision; unavailable or closed input fails closed.

    Surfaces the specific document(s) this stage produced (path + a short preview)
    instead of only pointing at the doc directory.
    """
    _log("")
    _log(f"=== HITL REVIEW GATE — stage {node.id!r} complete ===")
    _surface_documents(node_cwd, documents)
    if not sys.stdin.isatty():
        return ReviewResult(
            REVIEW_ABORT,
            "interactive review unavailable: stdin is not a TTY — use --review-file for headless "
            "review (poll a decision file), the MCP review surface, or --yes to auto-approve",
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

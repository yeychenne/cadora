"""``cadora eval`` — deterministic evaluation of an archived run.

Scores a run against AI-DLC / quality invariants WITHOUT calling an LLM:
completion, per-node success, gate verdicts, integrity findings, cost
attribution, and artifact presence. Deterministic checks are the base layer
(cheap, reproducible); LLM-as-judge graders are a later, optional layer.

Verdict gates on the CRITICAL checks only (run_ok, all_nodes_ok, gates_passed,
integrity_clean). Non-critical checks (cost attribution, artifact capture) are
surfaced as warnings but don't fail the run.
"""

from __future__ import annotations

from pathlib import Path

from cadora.usage import normalize_manifest_usage

_CRITICAL = {"run_ok", "all_nodes_ok", "gates_passed", "integrity_clean"}


def _check(name: str, passed: bool, detail: str = "") -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


def evaluate_run(manifest: dict, run_dir: str | Path | None = None) -> dict:
    nodes = manifest.get("nodes", [])
    checks: list[dict] = []

    checks.append(_check(
        "run_ok", manifest.get("ok") is True, f"manifest.ok={manifest.get('ok')}"
    ))

    failed = [str(n.get("node_id")) for n in nodes if not n.get("ok")]
    checks.append(_check(
        "all_nodes_ok", not failed,
        "all nodes ok" if not failed else f"failed nodes: {', '.join(failed)}",
    ))

    bad_gates = []
    for n in nodes:
        g = n.get("gate")
        if g and not g.get("passed"):
            bad_gates.append(f"{n.get('node_id')}:{g.get('status') or 'failed'}")
    checks.append(_check(
        "gates_passed", not bad_gates,
        "no failing gates" if not bad_gates else f"bad gates: {', '.join(bad_gates)}",
    ))

    bad_integrity = [
        str(n.get("node_id"))
        for n in nodes
        if (it := n.get("integrity")) and (it.get("failed") or it.get("findings"))
    ]
    checks.append(_check(
        "integrity_clean", not bad_integrity,
        "no integrity findings"
        if not bad_integrity
        else f"findings in: {', '.join(bad_integrity)}",
    ))

    # Attribution goes through the usage layer, not raw manifest cost_usd: Codex/GLM report
    # tokens (priced from the rate table) and Kiro reports credits — a node is attributed if it
    # has EITHER dollars or credits, not only a backend-reported dollar figure.
    usage_by_node = {u.node_id: u for u in normalize_manifest_usage(manifest)}
    missing_cost = []
    for n in nodes:
        u = usage_by_node.get(str(n.get("node_id")))
        if u is None or (u.cost_usd is None and u.credits is None):
            missing_cost.append(str(n.get("node_id")))
    estimated = sum(1 for u in usage_by_node.values() if u.cost_estimated)
    credited = sum(1 for u in usage_by_node.values() if u.credits is not None)
    detail_bits = []
    if estimated:
        detail_bits.append(f"{estimated} estimated from price table")
    if credited:
        detail_bits.append(f"{credited} in credits")
    checks.append(_check(
        "cost_attributed", bool(nodes) and not missing_cost,
        ("all nodes have cost" + (f" ({'; '.join(detail_bits)})" if detail_bits else ""))
        if (nodes and not missing_cost)
        else (f"missing cost: {', '.join(missing_cost)}" if nodes else "no nodes"),
    ))

    has_artifacts = any(n.get("aidlc_docs") for n in nodes)
    if not has_artifacts and run_dir is not None:
        rd = Path(run_dir)
        has_artifacts = any(
            (rd / str(n.get("node_id")) / "aidlc-docs").is_dir() for n in nodes
        )
    checks.append(_check(
        "aidlc_artifacts", has_artifacts,
        "AI-DLC artifacts captured" if has_artifacts else "no aidlc-docs artifacts found",
    ))

    passed = sum(1 for c in checks if c["passed"])
    total = len(checks)
    verdict = "pass" if all(
        c["passed"] for c in checks if c["name"] in _CRITICAL
    ) else "fail"
    return {
        "run_id": manifest.get("run_id"),
        "executor": manifest.get("executor"),
        "topology": manifest.get("topology"),
        "checks": checks,
        "passed": passed,
        "total": total,
        "score": round(passed / total, 3) if total else 0.0,
        "verdict": verdict,
    }


def format_evaluation(result: dict) -> str:
    lines = [
        f"eval {result['run_id']}  ·  executor={result['executor']}  ·  "
        f"topology={result['topology']}"
    ]
    for c in result["checks"]:
        crit = "" if c["name"] in _CRITICAL else "  (warn)"
        lines.append(f"  {'✓' if c['passed'] else '✗'} {c['name']}: {c['detail']}{crit}")
    lines.append(
        f"  score {result['passed']}/{result['total']} "
        f"({result['score'] * 100:.0f}%)  →  {result['verdict'].upper()}"
    )
    return "\n".join(lines)

"""``cadora compare`` — diff two archived runs.

The differentiator no single vendor ships: run the SAME topology through
different backends (Claude vs Codex) or across time, and diff outcome + cost
per node. Pure functions over two manifests; no LLM, no network.
"""

from __future__ import annotations

from cadora.usage import normalize_manifest_usage


def _summary(manifest: dict) -> dict:
    nodes = manifest.get("nodes", [])
    usage = normalize_manifest_usage(manifest)
    return {
        "run_id": manifest.get("run_id"),
        "executor": manifest.get("executor"),
        "topology": manifest.get("topology"),
        "ok": manifest.get("ok"),
        "n_nodes": len(nodes),
        "n_pass": sum(1 for n in nodes if n.get("ok")),
        "cost_usd": round(sum((u.cost_usd or 0.0) for u in usage), 6),
        "output_tokens": sum(u.output_tokens for u in usage),
    }


def compare_runs(a: dict, b: dict) -> dict:
    """Diff run ``a`` against run ``b`` (both are manifest dicts)."""
    na = {n.get("node_id"): n for n in a.get("nodes", [])}
    nb = {n.get("node_id"): n for n in b.get("nodes", [])}
    ua = {u.node_id: u for u in normalize_manifest_usage(a)}
    ub = {u.node_id: u for u in normalize_manifest_usage(b)}

    node_diffs = []
    for nid in dict.fromkeys([*na, *nb]):  # a's order first, then b-only
        xa, xb = na.get(nid), nb.get(nid)
        pa, pb = ua.get(nid), ub.get(nid)
        oka = xa.get("ok") if xa else None
        okb = xb.get("ok") if xb else None
        node_diffs.append({
            "node_id": nid,
            "in_a": xa is not None,
            "in_b": xb is not None,
            "ok_a": oka,
            "ok_b": okb,
            "ok_changed": xa is not None and xb is not None and oka != okb,
            "model_a": xa.get("model") if xa else None,
            "model_b": xb.get("model") if xb else None,
            "cost_a": pa.cost_usd if pa else None,
            "cost_b": pb.cost_usd if pb else None,
            "output_tokens_a": pa.output_tokens if pa else None,
            "output_tokens_b": pb.output_tokens if pb else None,
        })

    sa, sb = _summary(a), _summary(b)
    return {
        "run_a": a.get("run_id"),
        "run_b": b.get("run_id"),
        "same_topology": a.get("topology") == b.get("topology"),
        "summary_a": sa,
        "summary_b": sb,
        "cost_delta": round(sb["cost_usd"] - sa["cost_usd"], 6),
        "nodes": node_diffs,
    }


def _money(v) -> str:
    return f"${v:.4f}" if v is not None else "—"


def format_comparison(diff: dict) -> str:
    sa, sb = diff["summary_a"], diff["summary_b"]
    lines = [f"compare  A={diff['run_a']}  B={diff['run_b']}"]
    if not diff["same_topology"]:
        lines.append(f"  ⚠ different topologies: A={sa['topology']} B={sb['topology']}")
    for tag, s in (("A", sa), ("B", sb)):
        lines.append(
            f"  {tag}: executor={s['executor']} topology={s['topology']} ok={s['ok']} "
            f"pass={s['n_pass']}/{s['n_nodes']} cost={_money(s['cost_usd'])} "
            f"out_tok={s['output_tokens']}"
        )
    sign = "+" if diff["cost_delta"] >= 0 else ""
    lines.append(f"  Δcost (B−A): {sign}{_money(diff['cost_delta'])}")
    lines.append("  nodes:")
    for n in diff["nodes"]:
        if not (n["in_a"] and n["in_b"]):
            lines.append(f"    · {n['node_id']}: {'A only' if n['in_a'] else 'B only'}")
            continue
        flag = "  ⚠ ok changed" if n["ok_changed"] else ""
        lines.append(
            f"    · {n['node_id']}: "
            f"A[{'✓' if n['ok_a'] else '✗'} {n['model_a'] or '—'} {_money(n['cost_a'])}] "
            f"B[{'✓' if n['ok_b'] else '✗'} {n['model_b'] or '—'} {_money(n['cost_b'])}]{flag}"
        )
    return "\n".join(lines)

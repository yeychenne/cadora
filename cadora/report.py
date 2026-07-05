"""The evidence pack — ``cadora report <run-id>``.

Turns one archived run into a portable, self-contained proof pack:

- ``report.html`` — human-readable, single file, no external assets (mail it, print it,
  attach it to a deliverable);
- ``report.json`` — the same content structured, for machines;
- ``checksums.txt`` — SHA-256 of every archived run file + ``report.json``, so the pack
  can be verified after it leaves your machine (``sha256sum -c checksums.txt``).

Honesty contract: the pack claims exactly what the archive recorded — deterministic gate
verdicts, integrity findings, human-review decisions, and per-node cost (backend-reported,
or price-table estimates explicitly flagged). It does not claim the agents were honest
beyond what the gates and integrity checks verified, and it is checksummed, not signed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from cadora import __version__
from cadora.usage import normalize_manifest_usage

_GATE_LABEL = {
    "passed": "passed",
    "failed": "FAILED",
    "vacuous": "VACUOUS (ran zero tests)",
    "blocked_prerequisite": "blocked: missing prerequisite",
}


def build_report(run_dir: str | Path) -> dict:
    """Assemble the structured evidence report for one archived run."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    status: dict = {}
    if (run_dir / "status.json").exists():
        try:
            status = json.loads((run_dir / "status.json").read_text())
        except json.JSONDecodeError:
            status = {}

    usage_by_node = {u.node_id: u for u in normalize_manifest_usage(manifest)}

    nodes = []
    gate_rollup: dict[str, int] = {}
    integrity_findings = 0
    integrity_failed_nodes = 0
    review_decisions: list[dict] = []
    for node in manifest.get("nodes", []):
        node_id = str(node.get("node_id", ""))
        usage = usage_by_node.get(node_id)
        gate = node.get("gate")
        if gate:
            key = gate.get("status") or ("passed" if gate.get("passed") else "failed")
            gate_rollup[key] = gate_rollup.get(key, 0) + 1
        integrity = node.get("integrity")
        if integrity:
            findings = integrity.get("findings") or []
            integrity_findings += len(findings)
            if not integrity.get("passed", integrity.get("ok", True)):
                integrity_failed_nodes += 1
        for review in node.get("human_reviews") or []:
            review_decisions.append({"node_id": node_id, **review})
        remediation = node.get("remediation")
        nodes.append(
            {
                "node_id": node_id,
                "executor": node.get("executor") or manifest.get("executor"),
                "model": node.get("model"),
                "ok": node.get("ok"),
                "exit_code": node.get("exit_code"),
                "gate": gate,
                "integrity": integrity,
                "human_reviews": node.get("human_reviews") or [],
                "remediation": (
                    {
                        "state": remediation.get("state"),
                        "attempts": remediation.get("attempts"),
                        "blocked_reason": remediation.get("blocked_reason"),
                    }
                    if remediation
                    else None
                ),
                "cost_usd": usage.cost_usd if usage else node.get("cost_usd"),
                "cost_estimated": usage.cost_estimated if usage else False,
                "credits": usage.credits if usage else None,
                "context_tokens": usage.context_tokens if usage else 0,
                "funding": usage.funding if usage else "unknown",
            }
        )

    artifacts = _artifact_checksums(run_dir)
    total_cost = sum(n["cost_usd"] or 0.0 for n in nodes)
    total_credits = sum(n["credits"] or 0.0 for n in nodes)
    return {
        "evidence_pack": {
            "format": "cadora-evidence/1",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cadora_version": __version__,
            "claims": "deterministic gate verdicts, integrity findings, human-review "
            "decisions, and per-node cost as recorded in the run archive; "
            "checksummed, not signed",
        },
        "run": {
            "run_id": manifest.get("run_id"),
            "topology": manifest.get("topology"),
            "executor": manifest.get("executor"),
            "ok": manifest.get("ok"),
            "started_at": status.get("started_at"),
            "completed_at": status.get("completed_at"),
            "status": status.get("status"),
        },
        "summary": {
            "nodes": len(nodes),
            "backends": sorted({str(n["executor"]) for n in nodes if n["executor"]}),
            "gates": gate_rollup,
            "integrity_findings": integrity_findings,
            "integrity_failed_nodes": integrity_failed_nodes,
            "human_review_decisions": len(review_decisions),
            "remediated_nodes": sum(1 for n in nodes if n["remediation"]),
            "remediation_green": sum(
                1 for n in nodes if (n["remediation"] or {}).get("state") == "completed-green"
            ),
            "cost_usd": round(total_cost, 4),
            "credits": round(total_credits, 2),
            "estimated_cost_nodes": sum(1 for n in nodes if n["cost_estimated"]),
        },
        "nodes": nodes,
        "human_reviews": review_decisions,
        "artifacts": artifacts,
    }


def write_report(
    archive_dir: str | Path, run_id: str, out: str | Path | None = None
) -> dict[str, Path]:
    """Write the pack (html + json + checksums) and return its paths."""
    run_dir = Path(archive_dir) / run_id
    if not (run_dir / "manifest.json").exists():
        raise FileNotFoundError(f"no manifest.json under {run_dir}")
    report = build_report(run_dir)
    out_dir = Path(out) if out else run_dir / "report"
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "report.json"
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    html_path = out_dir / "report.html"
    html_path.write_text(render_html(report))

    lines = [f"{a['sha256']}  {a['path']}" for a in report["artifacts"]]
    # Every line must verify from the RUN DIR (the documented cwd for `shasum -c`) — when
    # --out points outside the run dir, reference the report by absolute path, not a
    # hard-coded "report/" prefix that only exists in the default layout.
    try:
        json_ref = json_path.resolve().relative_to(run_dir.resolve())
    except ValueError:
        json_ref = json_path.resolve()
    lines.append(f"{_sha256(json_path)}  {json_ref}")
    checksums = out_dir / "checksums.txt"
    checksums.write_text("\n".join(lines) + "\n")
    return {"html": html_path, "json": json_path, "checksums": checksums}


def _artifact_checksums(run_dir: Path) -> list[dict]:
    """SHA-256 every archived file (the evidence base), excluding the pack output itself."""
    out = []
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or "report" in path.relative_to(run_dir).parts[:1]:
            continue
        out.append(
            {
                "path": str(path.relative_to(run_dir)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return out


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- rendering -----------------------------------------------------------------------


_CSS = """
body{font:15px/1.5 -apple-system,'Helvetica Neue',Arial,sans-serif;color:#1c2733;margin:2rem auto;
max-width:900px;padding:0 1rem}
h1{font-size:1.5rem;margin:.2rem 0}h2{font-size:1.05rem;margin:1.6rem 0 .5rem;border-bottom:1px solid #d8dee5;padding-bottom:.2rem}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:.85em}
.muted{color:#5c6b7a}.small{font-size:.8rem}
.banner{padding:.7rem 1rem;border-radius:6px;font-weight:600;margin:.8rem 0}
.ok{background:#e7f6ee;color:#116644;border:1px solid #bfe3cf}
.bad{background:#fdeaec;color:#8f1f2c;border:1px solid #f2c3c9}
table{border-collapse:collapse;width:100%;font-size:.85rem;margin:.4rem 0}
th,td{padding:.35rem .5rem;text-align:left;border-bottom:1px solid #e3e8ee;vertical-align:top}
th{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:#5c6b7a}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:.05rem .5rem;border-radius:999px;font-size:.72rem;font-weight:600}
.p-ok{background:#e7f6ee;color:#116644}.p-bad{background:#fdeaec;color:#8f1f2c}
.p-warn{background:#fdf3e2;color:#8a5a10}.p-mut{background:#eef1f4;color:#5c6b7a}
.cards{display:flex;gap:.7rem;flex-wrap:wrap;margin:.8rem 0}
.card{border:1px solid #e3e8ee;border-radius:6px;padding:.5rem .8rem;min-width:8.5rem}
.card b{display:block;font-size:1.15rem}.card span{font-size:.72rem;color:#5c6b7a;text-transform:uppercase;letter-spacing:.04em}
footer{margin-top:2rem;padding-top:.8rem;border-top:1px solid #d8dee5;font-size:.8rem;color:#5c6b7a}
@media print{body{margin:0 auto}}
"""


def _cost_value(cost_usd: float, credits: float | None, est: int) -> str:
    """Headline cost figure — dollars, credits (Kiro), or both; honest about a $0 credit run."""
    parts = []
    if cost_usd or not credits:
        parts.append(f"${cost_usd:.4f}" + (f" ({est} est.)" if est else ""))
    if credits:
        parts.append(f"{credits:.2f} cr")
    return " · ".join(parts)


def _pill(text: str, tone: str) -> str:
    return f'<span class="pill p-{tone}">{escape(text)}</span>'


def _gate_cell(gate: dict | None) -> str:
    if not gate:
        return '<span class="muted">—</span>'
    status = gate.get("status") or ("passed" if gate.get("passed") else "failed")
    tone = "ok" if status == "passed" else ("warn" if status == "blocked_prerequisite" else "bad")
    return _pill(_GATE_LABEL.get(status, status), tone)


def _integrity_cell(integrity: dict | None) -> str:
    if not integrity:
        return '<span class="muted">—</span>'
    passed = integrity.get("passed", integrity.get("ok", True))
    findings = len(integrity.get("findings") or [])
    if passed and not findings:
        return _pill("clean", "ok")
    return _pill(f"{findings} finding(s)", "ok" if passed else "bad")


def _remediation_cell(remediation: dict | None) -> str:
    if not remediation:
        return '<span class="muted">—</span>'
    tone = "ok" if remediation.get("state") == "completed-green" else "bad"
    text = f"{remediation.get('state')} x{remediation.get('attempts')}"
    return _pill(text, tone)


def render_html(report: dict) -> str:
    run = report["run"]
    summary = report["summary"]
    pack = report["evidence_pack"]

    ok = bool(run.get("ok"))
    banner = (
        '<div class="banner ok">RUN VERIFIED — all deterministic gates and integrity checks '
        "recorded as passing</div>"
        if ok
        else '<div class="banner bad">RUN NOT CLEAN — see gate / integrity detail below</div>'
    )

    gates = ", ".join(f"{k}: {v}" for k, v in summary["gates"].items()) or "no gated nodes"
    est = summary["estimated_cost_nodes"]
    cards = f"""
<div class="cards">
 <div class="card"><b>{summary["nodes"]}</b><span>nodes</span></div>
 <div class="card"><b>{escape(" + ".join(summary["backends"]) or "?")}</b><span>backends</span></div>
 <div class="card"><b>{escape(gates)}</b><span>gates</span></div>
 <div class="card"><b>{summary["integrity_findings"]}</b><span>integrity findings</span></div>
 <div class="card"><b>{summary["human_review_decisions"]}</b><span>human decisions</span></div>
 <div class="card"><b>{_cost_value(summary["cost_usd"], summary.get("credits"), est)}</b><span>cost</span></div>
</div>"""

    rows = []
    for node in report["nodes"]:
        cost = node["cost_usd"]
        if cost is not None:
            cost_text = f"${cost:.4f}" + (" est." if node["cost_estimated"] else "")
        elif node.get("credits") is not None:
            cost_text = f"{node['credits']:.2f} credits"
        else:
            cost_text = "—"
        reviews = len(node["human_reviews"])
        rows.append(
            "<tr>"
            f'<td class="mono">{escape(node["node_id"])}</td>'
            f"<td>{escape(str(node['executor'] or '—'))}</td>"
            f'<td class="mono small">{escape(str(node["model"] or "—"))}</td>'
            f"<td>{_pill('ok', 'ok') if node['ok'] else _pill('failed', 'bad')}</td>"
            f"<td>{_gate_cell(node['gate'])}</td>"
            f"<td>{_integrity_cell(node['integrity'])}</td>"
            f"<td>{_remediation_cell(node['remediation'])}</td>"
            f"<td>{reviews or '—'}</td>"
            f'<td class="num">{node["context_tokens"]:,}</td>'
            f'<td class="num">{escape(cost_text)}</td>'
            "</tr>"
        )

    reviews_html = ""
    if report["human_reviews"]:
        review_rows = "".join(
            "<tr>"
            f'<td class="mono small">{escape(r.get("timestamp", ""))}</td>'
            f'<td class="mono">{escape(r["node_id"])}</td>'
            f"<td>{escape(r.get('decision', ''))}</td>"
            f"<td>{escape(r.get('comments', '') or '—')}</td></tr>"
            for r in report["human_reviews"]
        )
        reviews_html = (
            "<h2>Human review trail</h2><table><tr><th>when</th><th>node</th>"
            f"<th>decision</th><th>comments</th></tr>{review_rows}</table>"
        )

    findings_html = ""
    finding_rows = []
    for node in report["nodes"]:
        for finding in (node["integrity"] or {}).get("findings") or []:
            text = finding if isinstance(finding, str) else json.dumps(finding)
            finding_rows.append(
                f'<tr><td class="mono">{escape(node["node_id"])}</td><td>{escape(text)}</td></tr>'
            )
    if finding_rows:
        findings_html = (
            "<h2>Integrity findings</h2><table><tr><th>node</th><th>finding</th></tr>"
            + "".join(finding_rows)
            + "</table>"
        )

    artifact_rows = "".join(
        f'<tr><td class="mono small">{escape(a["path"])}</td>'
        f'<td class="num">{a["bytes"]:,}</td>'
        f'<td class="mono small">{a["sha256"][:16]}…</td></tr>'
        for a in report["artifacts"]
    )

    started = escape(str(run.get("started_at") or "?"))
    completed = escape(str(run.get("completed_at") or "?"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evidence pack — {escape(str(run.get("run_id")))}</title>
<style>{_CSS}</style></head><body>
<p class="muted small">cadora evidence pack · {escape(pack["format"])}</p>
<h1>{escape(str(run.get("run_id")))}</h1>
<p class="muted">topology <span class="mono">{escape(str(run.get("topology")))}</span>
 · started {started} · completed {completed}</p>
{banner}
{cards}
<h2>Nodes</h2>
<table><tr><th>node</th><th>backend</th><th>model</th><th>result</th><th>gate</th>
<th>integrity</th><th>remediation</th><th>reviews</th><th>ctx tokens</th><th>cost</th></tr>
{"".join(rows)}</table>
{findings_html}
{reviews_html}
<h2>Archived artifacts ({len(report["artifacts"])})</h2>
<table><tr><th>file</th><th>bytes</th><th>sha-256</th></tr>{artifact_rows}</table>
<footer>
<p><b>What this pack claims:</b> {escape(pack["claims"])}.</p>
<p><b>Verify integrity of the pack:</b> from the run directory, run
<span class="mono">shasum -a 256 -c report/checksums.txt</span> (or
<span class="mono">sha256sum -c</span>). Estimated costs are computed from public price tables
where the backend reported tokens but no dollars, and are marked <i>est.</i></p>
<p class="small">generated {escape(pack["generated_at"])} · cadora {escape(pack["cadora_version"])}</p>
</footer>
</body></html>
"""

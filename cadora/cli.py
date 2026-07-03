"""Cadora CLI — ``cadora run | compare | eval``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from cadora.archive import list_runs, read_manifest
from cadora.executors import get_executor
from cadora.gates import ShellGate
from cadora.runner import run_topology
from cadora.topology import load_topology
from cadora.usage import summarize_usage


def _default_run_id() -> str:
    # Caller may override with --run-id.
    return time.strftime("run-%Y%m%d-%H%M%S")


def cmd_run(args) -> int:
    topology = load_topology(args.topology)
    if args.vision is not None:
        from cadora.workspace import setup_aidlc_workspace

        setup_aidlc_workspace(
            args.cwd,
            vision=args.vision,
            tech_env=args.tech_env,
            executor=args.executor,
        )
    executor = get_executor(
        args.executor,
        funding=args.funding,
        timeout=args.timeout,
        model=args.model,
    )
    construction_executor = None
    if getattr(args, "construction_executor", None):
        # Phase-aware routing: construction-phase nodes run on a second backend (e.g. Codex)
        # while inception/operations nodes stay on --executor (e.g. Claude Code).
        construction_executor = get_executor(
            args.construction_executor,
            funding=args.funding,
            timeout=args.timeout,
            model=args.construction_model,
        )
    # Register every gate the topology references, all running the configured command.
    gate_names = {n.gate for n in topology.nodes if n.gate}
    gates = {
        g: ShellGate(
            g,
            args.gate_cmd,
            setup_mode=args.gate_setup,
            wheelhouse=args.gate_wheelhouse,
        )
        for g in gate_names
    }
    run_id = args.run_id or _default_run_id()
    out = run_topology(
        topology,
        executor,
        run_id=run_id,
        cwd=args.cwd,
        archive_root=args.archive_dir,
        gates=gates,
        integrity_mode=args.integrity_mode,
        hitl=args.hitl,
        construction_executor=construction_executor,
    )
    print(f"run complete: {out}")
    return 0


def cmd_compare(args) -> int:
    # TODO: implement run comparison (diff manifests + per-node outputs).
    raise SystemExit("cadora compare: not implemented yet")


def cmd_eval(args) -> int:
    # TODO: implement the eval pipeline (LLM-as-judge graders).
    raise SystemExit("cadora eval: not implemented yet")


def cmd_mcp(args) -> int:
    from cadora.mcp.server import serve

    serve(transport=args.transport, host=args.host, port=args.port)
    return 0


def cmd_dashboard(args) -> int:
    from cadora.dashboard.server import serve_dashboard

    serve_dashboard(args.archive_dir, host=args.host, port=args.port)
    return 0


def cmd_integrity(args) -> int:
    from cadora.integrity import scan_toolchain_integrity

    report = scan_toolchain_integrity(args.workspace)
    if args.json:
        print(json.dumps(asdict(report), indent=2))
    elif not report.findings:
        print(f"✓ toolchain integrity ok: {args.workspace}")
    else:
        for finding in report.findings:
            print(
                f"{'✗' if finding.severity == 'blocking' else '!'} "
                f"{finding.severity:<8} {finding.rule}: {finding.path}"
            )
            print(f"    {finding.detail}")
            if finding.evidence:
                print(f"    evidence: {finding.evidence}")
        print(
            f"{report.blocking_count} blocking, {report.warning_count} warning "
            f"finding(s) in {args.workspace}"
        )
    return 0 if report.passed else 1


def cmd_aidlc_init(args) -> int:
    from cadora.workspace import (
        rules_version,
        setup_aidlc_workspace,
        workspace_instruction_file,
    )

    ws = setup_aidlc_workspace(
        args.workspace,
        vision=args.vision,
        tech_env=args.tech_env,
        executor=args.executor,
    )
    laid = [workspace_instruction_file(args.executor), ".aidlc-rule-details/"]
    if args.vision:
        laid.append("vision.md")
    if args.tech_env:
        laid.append("tech-env.md")
    print(f"AI-DLC workspace ready at {ws} (rules {rules_version()})")
    print("  installed: " + ", ".join(laid))
    print(
        "  next: cadora run examples/aidlc.topology.yaml "
        f"--executor {args.executor} --cwd {ws}"
    )
    return 0


def _total_cost(manifest: dict) -> float:
    return sum((n.get("cost_usd") or 0.0) for n in manifest.get("nodes", []))


def cmd_archive_ls(args) -> int:
    runs = list_runs(args.archive_dir)
    if not runs:
        print(f"no runs in {args.archive_dir}/")
        return 0
    for m in runs:
        ok = m.get("ok")
        mark = "✓" if ok else ("✗" if ok is False else "?")
        n = len(m.get("nodes", []))
        print(
            f"{mark} {m.get('run_id', '?'):<22} {m.get('executor', '?'):<7} "
            f"{m.get('topology', '?'):<14} {n}n  ${_total_cost(m):.4f}"
        )
    return 0


def cmd_archive_show(args) -> int:
    try:
        m = read_manifest(args.archive_dir, args.run_id)
    except FileNotFoundError:
        raise SystemExit(f"no such run {args.run_id!r} in {args.archive_dir}/")
    print(
        f"run {m.get('run_id')}  ·  executor={m.get('executor')}  ·  "
        f"topology={m.get('topology')}  ·  ok={m.get('ok')}"
    )
    for node in m.get("nodes", []):
        meta = node.get("meta", {})
        parts = [f"  {'✓' if node.get('ok') else '✗'} {node.get('node_id')}"]
        if node.get("model"):
            parts.append(node["model"])
        if node.get("cost_usd") is not None:
            parts.append(f"${node['cost_usd']:.4f}")
        if meta.get("funding_resolved"):
            parts.append(f"funding={meta['funding_resolved']}")
        if meta.get("num_turns") is not None:
            parts.append(f"turns={meta['num_turns']}")
        gate = node.get("gate")
        if gate:
            gate_mark = "ok" if gate["passed"] else gate.get("status", "BLOCKED").upper()
            parts.append(f"gate:{gate['name']} {gate_mark}")
        integrity = node.get("integrity")
        if integrity:
            blocking = sum(
                f.get("severity") == "blocking" for f in integrity.get("findings", [])
            )
            parts.append(f"integrity:{'ok' if not blocking else f'{blocking} BLOCKING'}")
        if node.get("repair"):
            parts.append(f"repair:{'ok' if node['repair'].get('ok') else 'FAILED'}")
        reviews = node.get("human_reviews") or []
        if reviews:
            parts.append(f"review:{reviews[-1].get('decision', '?')}")
        if len(node.get("attempts") or []) > 1:
            parts.append(f"attempts={len(node['attempts'])}")
        print("   ".join(parts))
        node_dir = Path(args.archive_dir) / str(m.get("run_id")) / str(node.get("node_id"))
        arts = [
            a
            for a in (
                "output.txt",
                "events.jsonl",
                "integrity.json",
                "integrity-repair.txt",
                "integrity-repair.events.jsonl",
                "human-review.md",
            )
            if (node_dir / a).is_file()
        ]
        if node.get("aidlc_docs"):
            arts.append("aidlc-docs/")
        if node.get("attempts"):
            arts.append("attempts/")
        if arts:
            print(f"      {node_dir}/{{{','.join(arts)}}}")
    print(f"  total: ${_total_cost(m):.4f}")
    return 0


def _fmt_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def cmd_usage(args) -> int:
    try:
        summary = summarize_usage(args.archive_dir, since=args.since)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
        return 0
    window = f" since {summary.since}" if summary.since else ""
    print(f"usage{window}: {summary.run_count} run(s), {summary.node_count} node(s)")
    print(
        "  tokens: "
        f"input={_fmt_tokens(summary.input_tokens)}  "
        f"output={_fmt_tokens(summary.output_tokens)}  "
        f"cache_create={_fmt_tokens(summary.cache_creation_input_tokens)}  "
        f"cache_read={_fmt_tokens(summary.cache_read_input_tokens)}"
    )
    print(
        f"  totals: generation={_fmt_tokens(summary.generation_tokens)}  "
        f"context={_fmt_tokens(summary.context_tokens)}  "
        f"cost=${summary.cost_usd:.4f}"
    )
    if summary.by_model:
        print("  by model:")
        for item in summary.by_model:
            print(
                f"    {item['model']:<24} "
                f"{_fmt_tokens(item['context_tokens']):>8} context  "
                f"${item['cost_usd']:.4f}"
            )
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cadora", description="AI-DLC workflow conductor")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a topology")
    r.add_argument("topology")
    r.add_argument("--executor", default="claude", help="claude | codex | kiro | antigravity")
    r.add_argument("--cwd", default=".", help="working dir for nodes / the AI-DLC workspace")
    r.add_argument("--archive-dir", default="runs")
    r.add_argument("--run-id", default=None)
    r.add_argument("--model", default=None, help="optional backend model override")
    r.add_argument(
        "--construction-executor",
        default=None,
        help="route construction-phase nodes to this executor (e.g. codex); "
        "inception/operations nodes stay on --executor",
    )
    r.add_argument(
        "--construction-model",
        default=None,
        help="optional model for --construction-executor (e.g. gpt-5.5)",
    )
    r.add_argument(
        "--vision",
        default=None,
        help="path to vision.md (or inline text); installs the AI-DLC workspace into --cwd",
    )
    r.add_argument("--tech-env", default=None, help="optional tech-env.md (path or inline text)")
    r.add_argument(
        "--funding",
        default="subscription",
        choices=["subscription", "api"],
        help="claude funding source (default: subscription)",
    )
    r.add_argument(
        "--gate-cmd",
        default="ruff check . && pytest -q",
        help="command the gate(s) run; non-zero exit blocks the run",
    )
    r.add_argument(
        "--gate-setup",
        default="auto",
        choices=["off", "auto"],
        help=(
            "prepare an isolated Python gate environment from requirements-dev.txt "
            "(default: auto)"
        ),
    )
    r.add_argument(
        "--gate-wheelhouse",
        default=None,
        help="offline Python wheel directory used by automatic gate setup",
    )
    r.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="per-node executor timeout in seconds (default: 1800)",
    )
    r.add_argument(
        "--integrity-mode",
        default="audit",
        choices=["off", "audit", "enforce", "repair"],
        help=(
            "toolchain integrity handling: audit records findings; enforce blocks; "
            "repair allows one fresh repair session (default: audit)"
        ),
    )
    r.add_argument(
        "--hitl",
        action="store_true",
        help="activate explicit `review: true` topology gates; approve, request a same-stage "
        "revision, or abort before downstream work starts",
    )
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("compare", help="diff two runs")
    c.add_argument("run_a")
    c.add_argument("run_b")
    c.set_defaults(func=cmd_compare)

    e = sub.add_parser("eval", help="evaluate a run")
    e.add_argument("run_id")
    e.set_defaults(func=cmd_eval)

    m = sub.add_parser("mcp", help="run Cadora as an MCP server (HITL review + run control)")
    m.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "http"],
        help="MCP transport: stdio (local: Claude Desktop/Code, Codex CLI) or http (remote)",
    )
    m.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host for --transport http (default: localhost; expose remotely behind TLS+auth)",
    )
    m.add_argument("--port", type=int, default=8000, help="bind port for --transport http")
    m.set_defaults(func=cmd_mcp)

    dash = sub.add_parser("dashboard", help="serve a lightweight local run dashboard")
    dash.add_argument("--archive-dir", default="runs")
    dash.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host (default: 127.0.0.1)",
    )
    dash.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")
    dash.set_defaults(func=cmd_dashboard)

    integrity = sub.add_parser(
        "integrity",
        help="scan a workspace for counterfeit or substituted build/test tooling",
    )
    integrity.add_argument("workspace", nargs="?", default=".")
    integrity.add_argument("--json", action="store_true", help="emit the structured report")
    integrity.set_defaults(func=cmd_integrity)

    arch = sub.add_parser("archive", help="inspect captured runs")
    arch_sub = arch.add_subparsers(dest="archive_cmd", required=True)
    als = arch_sub.add_parser("ls", help="list runs")
    als.add_argument("--archive-dir", default="runs")
    als.set_defaults(func=cmd_archive_ls)
    ash = arch_sub.add_parser("show", help="show one run")
    ash.add_argument("run_id")
    ash.add_argument("--archive-dir", default="runs")
    ash.set_defaults(func=cmd_archive_show)

    usage = sub.add_parser("usage", help="summarize token and cost usage from run archives")
    usage.add_argument("--archive-dir", default="runs")
    usage.add_argument(
        "--since",
        default=None,
        help="optional cutoff: ISO timestamp, Nd (days), or Nh (hours)",
    )
    usage.add_argument("--json", action="store_true", help="emit the structured summary")
    usage.set_defaults(func=cmd_usage)

    a = sub.add_parser("aidlc-init", help="set up an AI-DLC workspace (rules + inputs)")
    a.add_argument("workspace")
    a.add_argument(
        "--executor",
        default="claude",
        choices=["claude", "codex", "kiro"],
        help="install project memory for this backend (default: claude)",
    )
    a.add_argument("--vision", default=None, help="path to vision.md, or inline vision text")
    a.add_argument("--tech-env", default=None, help="path to tech-env.md, or inline text")
    a.set_defaults(func=cmd_aidlc_init)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

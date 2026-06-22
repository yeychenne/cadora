"""Cadora CLI — ``cadora run | compare | eval``."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from cadora.archive import list_runs, read_manifest
from cadora.executors import get_executor
from cadora.gates import ShellGate
from cadora.runner import run_topology
from cadora.topology import load_topology


def _default_run_id() -> str:
    # Caller may override with --run-id.
    return time.strftime("run-%Y%m%d-%H%M%S")


def cmd_run(args) -> int:
    topology = load_topology(args.topology)
    if args.vision is not None:
        from cadora.workspace import setup_aidlc_workspace

        setup_aidlc_workspace(args.cwd, vision=args.vision, tech_env=args.tech_env)
    executor = get_executor(args.executor, funding=args.funding, timeout=args.timeout)
    # Register every gate the topology references, all running the configured command.
    gate_names = {n.gate for n in topology.nodes if n.gate}
    gates = {g: ShellGate(g, args.gate_cmd) for g in gate_names}
    run_id = args.run_id or _default_run_id()
    out = run_topology(
        topology,
        executor,
        run_id=run_id,
        cwd=args.cwd,
        archive_root=args.archive_dir,
        gates=gates,
    )
    print(f"run complete: {out}")
    return 0


def cmd_compare(args) -> int:
    # TODO: implement run comparison (diff manifests + per-node outputs).
    raise SystemExit("cadora compare: not implemented yet")


def cmd_eval(args) -> int:
    # TODO: implement the eval pipeline (LLM-as-judge graders).
    raise SystemExit("cadora eval: not implemented yet")


def cmd_aidlc_init(args) -> int:
    from cadora.workspace import rules_version, setup_aidlc_workspace

    ws = setup_aidlc_workspace(args.workspace, vision=args.vision, tech_env=args.tech_env)
    laid = ["CLAUDE.md", ".aidlc-rule-details/"]
    if args.vision:
        laid.append("vision.md")
    if args.tech_env:
        laid.append("tech-env.md")
    print(f"AI-DLC workspace ready at {ws} (rules {rules_version()})")
    print("  installed: " + ", ".join(laid))
    print(f"  next: cadora run examples/aidlc.topology.yaml --executor claude --cwd {ws}")
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
            parts.append(f"gate:{gate['name']} {'ok' if gate['passed'] else 'BLOCKED'}")
        print("   ".join(parts))
        node_dir = Path(args.archive_dir) / str(m.get("run_id")) / str(node.get("node_id"))
        arts = [a for a in ("output.txt", "events.jsonl") if (node_dir / a).is_file()]
        if node.get("aidlc_docs"):
            arts.append("aidlc-docs/")
        if arts:
            print(f"      {node_dir}/{{{','.join(arts)}}}")
    print(f"  total: ${_total_cost(m):.4f}")
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
        "--timeout",
        type=int,
        default=1800,
        help="per-node executor timeout in seconds (default: 1800)",
    )
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("compare", help="diff two runs")
    c.add_argument("run_a")
    c.add_argument("run_b")
    c.set_defaults(func=cmd_compare)

    e = sub.add_parser("eval", help="evaluate a run")
    e.add_argument("run_id")
    e.set_defaults(func=cmd_eval)

    arch = sub.add_parser("archive", help="inspect captured runs")
    arch_sub = arch.add_subparsers(dest="archive_cmd", required=True)
    als = arch_sub.add_parser("ls", help="list runs")
    als.add_argument("--archive-dir", default="runs")
    als.set_defaults(func=cmd_archive_ls)
    ash = arch_sub.add_parser("show", help="show one run")
    ash.add_argument("run_id")
    ash.add_argument("--archive-dir", default="runs")
    ash.set_defaults(func=cmd_archive_show)

    a = sub.add_parser("aidlc-init", help="set up an AI-DLC workspace (rules + inputs)")
    a.add_argument("workspace")
    a.add_argument("--vision", default=None, help="path to vision.md, or inline vision text")
    a.add_argument("--tech-env", default=None, help="path to tech-env.md, or inline text")
    a.set_defaults(func=cmd_aidlc_init)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

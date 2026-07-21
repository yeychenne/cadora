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


def _build_gates(topology, default_cmd, default_setup, default_wheelhouse):
    """Register every gate the topology references.

    A gate named in the topology's top-level ``gates:`` map runs its own command / setup /
    wheelhouse; every other gate falls back to the run-level ``--gate-cmd`` / ``--gate-setup`` /
    ``--gate-wheelhouse``. This lets a `build-test` gate run ``ruff && pytest`` while an inception
    `artifact-check` gate runs a cheap ``test -f <deliverable>`` with ``setup: off`` — instead of
    one global command crashing on markdown-only phases.
    """
    gates = {}
    for name in {n.gate for n in topology.nodes if n.gate}:
        spec = topology.gates.get(name)
        gates[name] = ShellGate(
            name,
            spec.cmd if spec and spec.cmd else default_cmd,
            setup_mode=spec.setup if spec and spec.setup else default_setup,
            wheelhouse=spec.wheelhouse if spec and spec.wheelhouse else default_wheelhouse,
        )
    return gates


def cmd_run(args) -> int:
    topology = load_topology(args.topology)

    # Honest trust gate: autonomous runs drive skip-permissions agents in --cwd. Show the
    # blast radius; let a human abort; never block CI (--yes / CADORA_ASSUME_YES / no TTY).
    from cadora.preflight import preflight_autonomous

    if not preflight_autonomous(
        cwd=args.cwd,
        executor=args.executor,
        autonomous=not getattr(args, "no_autonomous", False),
        assume_yes=getattr(args, "yes", False),
    ):
        return 1

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
    gates = _build_gates(topology, args.gate_cmd, args.gate_setup, args.gate_wheelhouse)
    remediation_policy = None
    if getattr(args, "remediate", 0):
        from cadora.remediation import RemediationPolicy

        remediation_policy = RemediationPolicy(
            max_attempts=args.remediate,
            max_cost_usd=getattr(args, "remediate_max_cost", None),
        )
    review_fn = None
    if getattr(args, "review_file", False):
        # Headless HITL: no TTY (Quick Desktop / CI). Write a request file, poll for a decision.
        from cadora.review import file_review_fn

        # Pass the executor so the parked gate can also answer conversational review (ask / revise).
        review_fn = file_review_fn(timeout=args.review_timeout, executor=executor)
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
        review_fn=review_fn,
        construction_executor=construction_executor,
        remediation_policy=remediation_policy,
        max_parallel=args.max_parallel,
        resume_from=getattr(args, "resume_from", None),
        skip=_split_csv(getattr(args, "skip", None)),
        allow_drift=getattr(args, "allow_drift", False),
    )
    print(f"run complete: {out}")
    return 0


def _split_csv(value: str | None) -> list[str] | None:
    """Parse a comma-separated CLI value into a clean list (``None`` when unset/empty)."""
    if not value:
        return None
    items = [part.strip() for part in value.split(",") if part.strip()]
    return items or None


def run_gate_check(topology, cwd, gates):
    """Run every gate the topology references against ``cwd`` — no executor.

    Returns ``[(node_id, gate_name, GateResult)]``. Results are cached per (gate, cwd) so a gate
    shared by several nodes runs once.
    """
    results = []
    cache = {}
    for node in topology.nodes:
        if not node.gate:
            continue
        node_cwd = node.cwd or cwd
        key = (node.gate, node_cwd)
        if key not in cache:
            cache[key] = gates[node.gate].check(node_cwd)
        results.append((node.id, node.gate, cache[key]))
    return results


def cmd_gate_check(args) -> int:
    topology = load_topology(args.topology)
    gates = _build_gates(topology, args.gate_cmd, args.gate_setup, args.gate_wheelhouse)
    if not gates:
        print("no gates referenced by this topology")
        return 0
    failed = False
    for node_id, gate_name, result in run_gate_check(topology, args.cwd, gates):
        mark = "✓" if result.passed else "✗"
        suffix = (
            f" (exit {result.exit_code})"
            if not result.passed and result.exit_code is not None
            else ""
        )
        print(f"{mark} {node_id} · gate:{gate_name} {result.status}{suffix}")
        if not result.passed:
            failed = True
            lines = (result.detail or "").strip().splitlines()
            if lines:
                print(f"    {lines[-1][:200]}")
    return 1 if failed else 0


def cmd_compare(args) -> int:
    from cadora.compare import compare_runs, format_comparison

    try:
        a = read_manifest(args.archive_dir, args.run_a)
        b = read_manifest(args.archive_dir, args.run_b)
    except FileNotFoundError as e:
        raise SystemExit(f"no such run: {e}")
    diff = compare_runs(a, b)
    print(json.dumps(diff, indent=2) if getattr(args, "json", False)
          else format_comparison(diff))
    return 0


def cmd_eval(args) -> int:
    from cadora.evaluate import evaluate_run, format_evaluation

    try:
        m = read_manifest(args.archive_dir, args.run_id)
    except FileNotFoundError:
        raise SystemExit(f"no such run {args.run_id!r} in {args.archive_dir}/")
    result = evaluate_run(m, run_dir=Path(args.archive_dir) / args.run_id)
    print(json.dumps(result, indent=2) if getattr(args, "json", False)
          else format_evaluation(result))
    return 0 if result["verdict"] == "pass" else 1


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _guard_bind(host: str, surface: str, acknowledged: bool, *, authenticated: bool = False) -> None:
    """Refuse a non-loopback bind of an unauthenticated surface unless acknowledged.

    Both the MCP server and the dashboard are localhost-only with NO authentication by default.
    Binding either to a routable interface exposes it (the MCP tools read/drive runs; the dashboard
    serves the archive). Fail closed with an explicit escape hatch — or, when the surface actually
    carries auth (``authenticated``: e.g. the MCP server started with an ``--auth-token``), allow it.
    """
    if host in _LOOPBACK_HOSTS or acknowledged or authenticated:
        return
    raise SystemExit(
        f"refusing to bind the {surface} to {host!r}: it has NO authentication and would be "
        f"reachable from the network. Front it with TLS + auth, or pass "
        f"--i-understand-no-auth to bind anyway (do this only on a trusted network)."
    )


def cmd_mcp(args) -> int:
    from cadora.mcp.auth import resolve_token
    from cadora.mcp.server import serve

    token = resolve_token(getattr(args, "auth_token", None))
    _guard_bind(args.host, "MCP server", args.i_understand_no_auth, authenticated=bool(token))
    serve(transport=args.transport, host=args.host, port=args.port, auth_token=token)
    return 0


def cmd_dashboard(args) -> int:
    from cadora.dashboard.server import serve_dashboard

    _guard_bind(args.host, "dashboard", args.i_understand_no_auth)
    serve_dashboard(args.archive_dir or ["runs"], host=args.host, port=args.port)
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

    if getattr(args, "method", "aidlc") == "aidlc-v2":
        return _aidlc_v2_init(args)

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


def _aidlc_v2_init(args) -> int:
    from cadora.aidlc_v2 import PINNED_REF, InstallError, install_v2

    if args.executor != "claude":
        raise SystemExit("the aidlc-v2 pack currently supports --executor claude only")
    try:
        record = install_v2(
            args.workspace,
            ref=args.ref or PINNED_REF,
            keep_provider_pins=args.keep_provider_pins,
            keep_mcp=args.keep_mcp,
            force=args.force,
        )
    except InstallError as exc:
        raise SystemExit(str(exc)) from exc
    if args.vision:
        from cadora.workspace import _resolve_input

        (Path(args.workspace) / "vision.md").write_text(_resolve_input(args.vision))

    commit = (record.get("commit") or "")[:12]
    print(f"aidlc-v2 pack (EXPERIMENTAL) installed at {args.workspace}")
    print(f"  upstream: {record['upstream']}@{record['ref']} ({commit or 'local source'})")
    if record["provider_pins_stripped"]:
        pins = record["provider_pins_stripped"]
        names = [k for k in pins if k != "env"] + list(pins.get("env", {}))
        print(f"  provider/cost pins stripped (recorded): {', '.join(names)}")
        print("    (upstream default silently switches sessions to Bedrock at opus[1m]/xhigh;")
        print("     funding stays yours — restore with --keep-provider-pins)")
    if record["mcp_servers_available"] and not record["mcp_installed"]:
        print(
            "  remote MCP servers NOT installed (opt in with --keep-mcp): "
            + ", ".join(record["mcp_servers_available"])
        )
    if not record["bun_found"]:
        print("  WARNING: bun not found — v2's 11 hooks (incl. the audit logger) will not fire.")
        print("           install: brew install bun   (or see bun.sh)")
    print(f"  install record: {args.workspace}/.cadora-aidlc-v2.json")
    print(f"  next: cd {args.workspace} && claude   then run /aidlc — inspect any time with:")
    print(f"        cadora aidlc-audit {args.workspace}")
    return 0


def cmd_aidlc_audit(args) -> int:
    from cadora.aidlc_v2 import find_intents, ingest_intent

    intents = find_intents(args.workspace)
    if not intents:
        raise SystemExit(f"no aidlc v2 intents under {args.workspace} (aidlc/spaces/*/intents/*)")
    if args.intent:
        matches = [p for p in intents if p.name == args.intent]
        if not matches:
            raise SystemExit(
                f"intent {args.intent!r} not found; have: {', '.join(p.name for p in intents)}"
            )
        intent_dir = matches[0]
    else:
        intent_dir = intents[-1]  # newest (date-prefixed names)

    report = ingest_intent(intent_dir)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    state = report["state"]
    print(f"aidlc-v2 intent: {report['intent']}  (space: {report['space']})")
    print(
        f"  phase={state.get('phase')}  current={state.get('current_stage')}  "
        f"next={state.get('next_stage')}  status={state.get('status')}"
    )
    rollup = state.get("stage_rollup", {})
    label = {"x": "done", "-": "in-progress", "?": "awaiting-approval", "R": "revising",
             "S": "skipped", " ": "not-started"}
    print(
        "  stages: "
        + "  ".join(f"{label.get(k, k)}={v}" for k, v in sorted(rollup.items(), reverse=True))
    )
    print(
        f"  audit: {report['event_count']} events  ·  human_turns={report['human_turns']}  ·  "
        f"sensors fired/passed/failed={report['sensors']['fired']}/"
        f"{report['sensors']['passed']}/{report['sensors']['failed']}"
    )
    for gate in report["gates"]:
        print(f"    {gate.get('timestamp', '?'):<22} {gate['event']:<14} {gate.get('stage', '')}")
    return 0


def _manifest_costs(manifest: dict):
    """Per-node normalized cost/credits — the SINGLE source (matches usage/report/eval/compare).

    Raw ``node.cost_usd`` is $0/None for token-only backends (Codex/GLM report tokens; Kiro
    reports credits); the usage layer prices those from the rate table. Summing raw manifest
    cost would show $0.00 for a run that `cadora usage` prices at real dollars — this keeps the
    CLI honest and consistent with every other surface.
    """
    from cadora.usage import normalize_manifest_usage

    return {u.node_id: u for u in normalize_manifest_usage(manifest)}


def _total_cost(manifest: dict) -> float:
    return sum((u.cost_usd or 0.0) for u in _manifest_costs(manifest).values())


def _total_credits(manifest: dict) -> float:
    return sum((u.credits or 0.0) for u in _manifest_costs(manifest).values())


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
    costs = _manifest_costs(m)
    for node in m.get("nodes", []):
        meta = node.get("meta", {})
        parts = [f"  {'✓' if node.get('ok') else '✗'} {node.get('node_id')}"]
        if node.get("model"):
            parts.append(node["model"])
        usage = costs.get(str(node.get("node_id")))
        if usage and usage.cost_usd is not None:
            parts.append(f"${usage.cost_usd:.4f}" + (" est." if usage.cost_estimated else ""))
        if usage and usage.credits is not None:
            parts.append(f"credits={usage.credits:.2f}")
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
        remediation = node.get("remediation")
        if remediation:
            reason = f"({remediation['blocked_reason']})" if remediation.get("blocked_reason") else ""
            parts.append(f"remediate:{remediation['state']}{reason} x{remediation['attempts']}")
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
        if remediation:
            arts.append("remediation/")
        if arts:
            print(f"      {node_dir}/{{{','.join(arts)}}}")
    total_credits = _total_credits(m)
    total_line = f"  total: ${_total_cost(m):.4f}"
    if any(u.cost_estimated for u in costs.values()):
        total_line += " (incl. est.)"
    if total_credits:
        total_line += f"  credits={total_credits:.2f}"
    print(total_line)
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
    totals = (
        f"  totals: generation={_fmt_tokens(summary.generation_tokens)}  "
        f"context={_fmt_tokens(summary.context_tokens)}  "
        f"cost=${summary.cost_usd:.4f}"
    )
    if summary.credits:
        totals += f"  credits={summary.credits:.2f}"
    print(totals)
    if summary.estimated_cost_nodes:
        print(
            f"  ({summary.estimated_cost_nodes} node cost(s) estimated from the public "
            "price table — backend reported tokens but no dollars)"
        )
    if summary.by_model:
        print("  by model:")
        for item in summary.by_model:
            line = (
                f"    {item['model']:<24} "
                f"{_fmt_tokens(item['context_tokens']):>8} context  "
                f"${item['cost_usd']:.4f}"
            )
            if item.get("credits"):
                line += f"  credits={item['credits']:.2f}"
            print(line)
    return 0


def cmd_report(args) -> int:
    from cadora.report import write_report

    try:
        paths = write_report(args.archive_dir, args.run_id, out=args.out)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    run_dir = Path(args.archive_dir) / args.run_id
    print(f"evidence pack for {args.run_id}:")
    for kind, path in paths.items():
        print(f"  {kind:<9} {path}")
    print(f"  verify:   cd {run_dir} && shasum -a 256 -c {paths['checksums'].resolve()}")
    return 0


def cmd_sign(args) -> int:
    from cadora.signing import sign_pack

    meta = sign_pack(
        args.archive_dir, args.run_id, key=args.key, signer=args.signer, identity=args.identity
    )
    print(f"signed evidence pack for {args.run_id}:")
    print(f"  signature  {meta['signature']}")
    print(f"  signer     {meta['tool']}" + (f" · {meta['identity']}" if meta.get("identity") else ""))
    if meta.get("fingerprint"):
        print(f"  key        {meta['fingerprint']}")
    print(f"  verify:    cadora verify {args.run_id} --archive-dir {args.archive_dir}")
    return 0


def cmd_verify(args) -> int:
    from cadora.signing import verify_pack

    try:
        res = verify_pack(
            args.archive_dir, args.run_id,
            allowed_signers=args.allowed_signers, verifier=args.verifier,
        )
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    if res.hashes_ok:
        hashes = f"hashes    {res.checked} file(s) OK"
    else:
        bad = ", ".join(res.mismatched + [f"{m} (missing)" for m in res.missing])
        hashes = f"hashes    MISMATCH: {bad[:240]}"
    sig = {
        "absent": "signature none (checksummed, not signed)",
        "valid": f"signature VALID — {res.signer or 'signer'} · {res.detail}",
        "invalid": f"signature INVALID — {res.detail}",
        "unverified": f"signature present, unverified — {res.detail}",
    }[res.signature]
    print(f"evidence pack {args.run_id}:")
    print(f"  {hashes}")
    print(f"  {sig}")
    print(f"  => {'VERIFIED' if res.ok else 'NOT VERIFIED'}")
    return 0 if res.ok else 1


def cmd_deliverable(args) -> int:
    from cadora.deliverable import write_deliverable

    run_dir = Path(args.archive_dir) / args.run_id
    if not (run_dir / "manifest.json").exists():
        raise SystemExit(f"no such run {args.run_id!r} in {args.archive_dir}/")
    paths = write_deliverable(run_dir, out=args.out, docx=args.docx)
    print(f"delivery pack for {args.run_id}:")
    for kind, path in paths.items():
        print(f"  {kind:<9} {path}")
    return 0


def cmd_doctor(args) -> int:
    from cadora.doctor import SUPPORT, live_backends_ok, run_doctor

    checks = run_doctor()
    if args.json:
        print(json.dumps([c.to_dict() for c in checks], indent=2))
    else:
        print("cadora doctor — backend CLI contract checks")
        for c in checks:
            version = f" {c.version}" if c.version else ""
            detail = f"  ({c.detail})" if c.detail else ""
            label = c.backend + (f" ({c.tier})" if c.tier else "")
            print(f"  {c.status:<10} {label:<26}{version}{detail}")
        verified = sorted(b for b, t in SUPPORT.items() if t == "verified")
        experimental = sorted(b for b, t in SUPPORT.items() if t == "experimental")
        print(
            f"  support: {len(verified)} verified ({', '.join(verified)}) · "
            f"{len(experimental)} experimental ({', '.join(experimental)})"
        )
        print("  (fixture needs no check — offline, no external contract)")
    # Exit 0 while at least one live backend is usable; 1 when none is (nothing can run).
    return 0 if live_backends_ok(checks) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="cadora",
        description="Audit-grade conductor for coding-agent CLIs: drive a gated workflow, "
        "prove what the agent built, and attribute cost per node across backends.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a topology")
    r.add_argument("topology")
    r.add_argument(
        "--executor", default="claude", help="claude | codex | kiro | glm | antigravity"
    )
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
    r.add_argument(
        "--review-file",
        action="store_true",
        help="headless HITL: instead of prompting on stdin (which aborts with no TTY), write "
        "`cadora-review-request.json` into the node workspace and poll for a "
        "`cadora-review-decision.json` — any tool or human can drop the decision. Fails closed "
        "on timeout",
    )
    r.add_argument(
        "--review-timeout",
        type=float,
        default=3600.0,
        metavar="SECONDS",
        help="how long `--review-file` waits for a decision before aborting (default: 3600)",
    )
    r.add_argument(
        "--max-parallel",
        type=int,
        default=1,
        metavar="N",
        help="run up to N independent nodes in a dependency wave concurrently (default: 1 = "
        "sequential). Only the agent execution is parallelized; gates, integrity, review, and "
        "archiving stay sequential and deterministic",
    )
    r.add_argument(
        "--resume-from",
        default=None,
        metavar="NODE",
        help="resume an interrupted run: skip every node upstream of NODE (trust their artifacts "
        "already in --cwd), then run NODE and everything downstream. Re-runs NODE itself.",
    )
    r.add_argument(
        "--skip",
        default=None,
        metavar="NODE[,NODE...]",
        help="comma-separated node ids to skip, trusting their existing workspace artifacts "
        "(fine-grained alternative to --resume-from)",
    )
    r.add_argument(
        "--allow-drift",
        action="store_true",
        help="on --resume-from/--skip, proceed even if the workspace has drifted since the run "
        "being resumed (default: refuse). The drift is recorded in the evidence pack either way",
    )
    r.add_argument(
        "--remediate",
        type=int,
        default=0,
        metavar="N",
        help="on a failed/vacuous gate (or a blocking integrity finding), run up to N "
        "remediation attempts in a fresh constrained session before giving up "
        "(default: 0 = off)",
    )
    r.add_argument(
        "--remediate-max-cost",
        type=float,
        default=None,
        metavar="USD",
        help="stop remediation honestly (honest-blocked) if its attempts' summed cost "
        "would exceed this ceiling",
    )
    r.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="skip the autonomous-run confirmation (also via CADORA_ASSUME_YES=1). Cadora drives "
        "skip-permissions agents in --cwd and audits their output, not their execution — point "
        "it only at a trusted or throwaway workspace.",
    )
    r.set_defaults(func=cmd_run)

    gc = sub.add_parser(
        "gate-check",
        help="run a topology's gates against an existing workspace — no executor, no LLM cost",
    )
    gc.add_argument("topology", help="path to the topology YAML")
    gc.add_argument("--cwd", default=".", help="workspace to check the gates against")
    gc.add_argument(
        "--gate-cmd",
        default="ruff check . && pytest -q",
        help="default command for gates not overridden in the topology `gates:` map",
    )
    gc.add_argument(
        "--gate-setup", default="auto", choices=["off", "auto"],
        help="prepare an isolated Python gate env from requirements-dev.txt (default: auto)",
    )
    gc.add_argument(
        "--gate-wheelhouse", default=None,
        help="offline Python wheel directory used by automatic gate setup",
    )
    gc.set_defaults(func=cmd_gate_check)

    c = sub.add_parser("compare", help="diff two runs (cross-backend / over time)")
    c.add_argument("run_a")
    c.add_argument("run_b")
    c.add_argument("--archive-dir", default="runs")
    c.add_argument("--json", action="store_true", help="emit the structured diff as JSON")
    c.set_defaults(func=cmd_compare)

    e = sub.add_parser("eval", help="evaluate a run (deterministic AI-DLC checks)")
    e.add_argument("run_id")
    e.add_argument("--archive-dir", default="runs")
    e.add_argument("--json", action="store_true", help="emit the structured result as JSON")
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
    m.add_argument(
        "--auth-token",
        default=None,
        help="require 'Authorization: Bearer <token>' on every HTTP request (or set "
        "CADORA_MCP_TOKEN); enables safe --transport http exposure. Still front it with TLS.",
    )
    m.add_argument(
        "--i-understand-no-auth",
        action="store_true",
        help="allow binding this unauthenticated surface to a non-loopback host",
    )
    m.set_defaults(func=cmd_mcp)

    dash = sub.add_parser("dashboard", help="serve a lightweight local run dashboard")
    dash.add_argument(
        "--archive-dir",
        action="append",
        default=None,
        help="run archive to serve; repeat to serve several projects on one dashboard "
        "(default: runs)",
    )
    dash.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host (default: 127.0.0.1)",
    )
    dash.add_argument("--port", type=int, default=8765, help="bind port (default: 8765)")
    dash.add_argument(
        "--i-understand-no-auth",
        action="store_true",
        help="allow binding this unauthenticated surface to a non-loopback host",
    )
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

    rep = sub.add_parser(
        "report",
        help="write a portable evidence pack for one run (report.html + report.json + checksums)",
    )
    rep.add_argument("run_id")
    rep.add_argument("--archive-dir", default="runs")
    rep.add_argument(
        "--out", default=None, help="output dir (default: <archive-dir>/<run-id>/report/)"
    )
    rep.set_defaults(func=cmd_report)

    sgn = sub.add_parser(
        "sign",
        help="sign a run's evidence pack — a detached signature over its checksums (attributable)",
    )
    sgn.add_argument("run_id")
    sgn.add_argument("--archive-dir", default="runs")
    sgn.add_argument("--key", default=None, help="SSH private key to sign with (the default signer)")
    sgn.add_argument(
        "--identity", default=None, help="signer identity to record (e.g. you@example.com)"
    )
    sgn.add_argument(
        "--signer", default=None,
        help="external signer command instead of ssh-keygen; {file}=checksums, must write {sig}",
    )
    sgn.set_defaults(func=cmd_sign)

    vfy = sub.add_parser(
        "verify",
        help="verify a run's evidence pack — recompute every hash, then check any signature",
    )
    vfy.add_argument("run_id")
    vfy.add_argument("--archive-dir", default="runs")
    vfy.add_argument(
        "--allowed-signers", default=None,
        help="OpenSSH allowed_signers file to authenticate the signer (default: self-attest)",
    )
    vfy.add_argument(
        "--verifier", default=None,
        help="external verifier command instead of ssh-keygen; {file}=checksums, {sig}=signature",
    )
    vfy.set_defaults(func=cmd_verify)

    dlv = sub.add_parser(
        "deliverable",
        help="write a client-facing delivery report for one run (markdown; --docx optional)",
    )
    dlv.add_argument("run_id")
    dlv.add_argument("--archive-dir", default="runs")
    dlv.add_argument("--out", default=None, help="output dir (default: <archive-dir>/<run-id>/)")
    dlv.add_argument(
        "--docx", action="store_true", help="also render .docx (needs: pip install 'cadora[deliverable]')"
    )
    dlv.set_defaults(func=cmd_deliverable)

    doc = sub.add_parser(
        "doctor",
        help="validate backend CLIs against the tested contract ranges (offline, no model calls)",
    )
    doc.add_argument("--json", action="store_true", help="emit the structured report")
    doc.set_defaults(func=cmd_doctor)

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
    a.add_argument(
        "--method",
        default="aidlc",
        choices=["aidlc", "aidlc-v2"],
        help="method pack: aidlc (v1 rules, stable, default) or aidlc-v2 (EXPERIMENTAL, "
        "pinned upstream dist; strips provider/cost pins by default)",
    )
    a.add_argument("--ref", default=None, help="aidlc-v2 only: upstream ref (default: pinned tag)")
    a.add_argument(
        "--keep-provider-pins",
        action="store_true",
        help="aidlc-v2 only: keep upstream's Bedrock/model/effort settings pins",
    )
    a.add_argument(
        "--keep-mcp",
        action="store_true",
        help="aidlc-v2 only: also install upstream's remote MCP server wiring",
    )
    a.add_argument("--force", action="store_true", help="aidlc-v2 only: overwrite existing pack files")
    a.set_defaults(func=cmd_aidlc_init)

    aa = sub.add_parser(
        "aidlc-audit",
        help="read-only: summarize an aidlc-v2 workspace's state + 68-event audit trail",
    )
    aa.add_argument("workspace", nargs="?", default=".")
    aa.add_argument("--intent", default=None, help="intent dir name (default: newest)")
    aa.add_argument("--json", action="store_true", help="emit the full structured report")
    aa.set_defaults(func=cmd_aidlc_audit)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

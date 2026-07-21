"""Backend account health — present · credentialed · live · budget.

Four layers, cheapest first, each of which answers a failure actually observed (2026-07):

1. **present** — the CLI exists and its version is in the tested range (``cadora doctor``'s
   check). Necessary, nowhere near sufficient: it reported ``claude ok`` while every call
   failed on an expired token.
2. **credentialed** — credentials are *stored* (``claude auth status``, Codex's ``auth.json``).
   Still not sufficient: ``auth status`` said ``loggedIn: true`` on an expired OAuth token.
   Stored is not valid, and the wording here never claims otherwise.
3. **live** (opt-in, costs a few tokens) — a tiny real call. The only layer that catches an
   expired or revoked token before a run burns a node on it.
4. **budget** — neither CLI exposes remaining quota, so the threshold is computed from a
   **user-declared budget** against **Cadora's own recorded consumption** (the run archives,
   priced by the same read-time normalization as ``cadora usage``). Deterministic and auditable:
   the number comes from evidence, not a vendor's opinion.

Read-only: this module reports; it never changes a run. The enforcement half (stopping at a
node boundary at N% and offering the other backend) builds on these numbers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cadora.doctor import check_backend
from cadora.usage import summarize_usage

DEFAULT_BACKENDS = ["claude", "codex"]

# The tiny live probe per backend: cheapest real call each CLI can make.
_PROBE_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p", "reply with exactly: OK"],
    "codex": ["codex", "exec", "reply with exactly: OK"],
}
_PROBE_TIMEOUT_SECONDS = 120


@dataclass
class AccountHealth:
    backend: str
    present: str = "unknown"  # ok | missing | unparsable | untested (doctor vocabulary)
    present_detail: str = ""
    credentials: str = "unknown"  # stored (…) | none | n/a — never "valid" (stored ≠ valid)
    probe: str = "off"  # off | ok | failed: <reason>
    spent_usd: float = 0.0
    spent_estimated: bool = False  # any node priced from the rate table rather than reported
    credits: float = 0.0  # Kiro-style subscription credits, when present
    budget_usd: float | None = None
    used_fraction: float | None = None  # spent / budget, when a budget is declared
    flags: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """Usable as far as the layers that RAN can tell (an off probe asserts nothing)."""
        return self.present == "ok" and not self.probe.startswith("failed") and not self.flags


def parse_budgets(pairs: list[str] | None) -> dict[str, float]:
    """``["claude=200", "codex=80"]`` -> ``{"claude": 200.0, "codex": 80.0}`` — loud on nonsense."""
    budgets: dict[str, float] = {}
    for pair in pairs or []:
        name, sep, value = pair.partition("=")
        try:
            amount = float(value) if sep else float("nan")
        except ValueError:
            amount = float("nan")
        if not name.strip() or not sep or amount != amount or amount <= 0:
            raise SystemExit(
                f"invalid --budget {pair!r}: expected BACKEND=USD with USD > 0 (e.g. claude=200)"
            )
        budgets[name.strip()] = amount
    return budgets


def _claude_credentials(run=subprocess.run) -> str:
    """What ``claude auth status`` claims. Reported as *stored*, never *valid* — it returned
    ``loggedIn: true`` on an expired token; only the live probe settles validity."""
    try:
        proc = run(
            ["claude", "auth", "status"], capture_output=True, text=True, timeout=30
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return "unknown"
    if not data.get("loggedIn"):
        return "none"
    plan = data.get("subscriptionType") or data.get("authMethod") or "?"
    return f"stored ({plan})"


def _codex_credentials(home: Path | None = None) -> str:
    """Codex stores auth in ``~/.codex/auth.json`` (mode + tokens). Presence only."""
    auth = (home or Path.home()) / ".codex" / "auth.json"
    if not auth.is_file():
        return "none"
    try:
        data = json.loads(auth.read_text())
    except (OSError, ValueError):
        return "stored (unreadable)"
    mode = "chatgpt" if data.get("tokens") else ("api-key" if data.get("OPENAI_API_KEY") else "?")
    return f"stored ({mode})"


def _live_probe(backend: str, run=subprocess.run) -> str:
    command = _PROBE_COMMANDS.get(backend)
    if command is None:
        return "off"
    if not shutil.which(command[0]):
        return f"failed: {command[0]!r} not on PATH"
    try:
        proc = run(command, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return f"failed: probe timed out after {_PROBE_TIMEOUT_SECONDS}s"
    except OSError as exc:
        return f"failed: {exc}"
    output = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode == 0 and "OK" in output:
        return "ok"
    reason = next(
        (line.strip() for line in output.splitlines() if line.strip()), "no output"
    )
    return f"failed: {reason[:160]}"


def gather_accounts(
    backends: list[str] | None = None,
    *,
    archive_roots: list[str | Path] | None = None,
    since: str | None = None,
    budgets: dict[str, float] | None = None,
    probe: bool = False,
    warn_at: float = 0.9,
    check_backend_fn=None,
    credential_fns: dict | None = None,
    probe_fn=None,
) -> list[AccountHealth]:
    """Assemble the four layers per backend. Injectable checkers keep this unit-testable.

    The checker defaults resolve at call time (module globals), not at definition time, so a
    test can monkeypatch ``cadora.accounts.check_backend`` and be honored.
    """
    check_backend_fn = check_backend_fn or check_backend
    probe_fn = probe_fn or _live_probe
    backends = backends or list(DEFAULT_BACKENDS)
    budgets = budgets or {}
    creds = {"claude": _claude_credentials, "codex": _codex_credentials}
    creds.update(credential_fns or {})

    summary = summarize_usage(list(archive_roots or ["runs"]), since=since)
    spent: dict[str, dict] = {}
    for node in summary.nodes:
        bucket = spent.setdefault(
            node.executor, {"cost_usd": 0.0, "credits": 0.0, "estimated": False}
        )
        bucket["cost_usd"] += node.cost_usd or 0.0
        bucket["credits"] += node.credits or 0.0
        bucket["estimated"] = bucket["estimated"] or node.cost_estimated

    accounts: list[AccountHealth] = []
    for backend in backends:
        checked = check_backend_fn(backend)
        account = AccountHealth(
            backend=backend,
            present=checked.status,
            present_detail=(checked.version or checked.detail or ""),
            credentials=creds[backend]() if backend in creds else "n/a",
            probe=probe_fn(backend) if probe else "off",
            spent_usd=round(spent.get(backend, {}).get("cost_usd", 0.0), 4),
            spent_estimated=spent.get(backend, {}).get("estimated", False),
            credits=round(spent.get(backend, {}).get("credits", 0.0), 2),
            budget_usd=budgets.get(backend),
        )
        if account.budget_usd:
            account.used_fraction = round(account.spent_usd / account.budget_usd, 4)
            if account.used_fraction >= warn_at:
                account.flags.append(
                    f"at {account.used_fraction:.0%} of its ${account.budget_usd:.2f} budget "
                    f"(threshold {warn_at:.0%})"
                )
        if account.present != "ok":
            account.flags.append(f"CLI {account.present}: {account.present_detail}")
        if account.probe.startswith("failed"):
            account.flags.append(f"live probe {account.probe}")
        accounts.append(account)
    return accounts


def format_accounts(
    accounts: list[AccountHealth], *, since: str | None, archives: list[str]
) -> str:
    window = since or "all time"
    lines = [f"cadora accounts — window: {window} · archives: {', '.join(archives)}"]
    header = f"  {'backend':<12} {'present':<14} {'credentials':<20} {'probe':<10} {'spent':<14} {'budget':<10} used"
    lines.append(header)
    for a in accounts:
        present = f"{a.present} {a.present_detail}".strip()[:14]
        spent = f"${a.spent_usd:.4f}" + (" est." if a.spent_estimated else "")
        if a.credits:
            spent = f"{spent} +{a.credits}cr"
        budget = f"${a.budget_usd:.2f}" if a.budget_usd else "—"
        used = f"{a.used_fraction:.0%}" if a.used_fraction is not None else "—"
        probe = a.probe if len(a.probe) <= 10 else "failed"
        lines.append(
            f"  {a.backend:<12} {present:<14} {a.credentials:<20} {probe:<10} {spent:<14} {budget:<10} {used}"
        )
    for a in accounts:
        for flag in a.flags:
            lines.append(f"  ⚠ {a.backend} {flag}")
        if a.probe.startswith("failed") and len(a.probe) > 10:
            lines.append(f"    ({a.probe})")
    if not any(a.probe != "off" for a in accounts):
        lines.append(
            "  note: credentials 'stored' is not 'valid' — run with --probe for the live check"
        )
    return "\n".join(lines)


def accounts_to_dict(accounts: list[AccountHealth]) -> list[dict]:
    return [asdict(a) | {"healthy": a.healthy} for a in accounts]

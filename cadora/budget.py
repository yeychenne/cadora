"""Budget enforcement at node boundaries — stop or move a run before a backend runs dry.

Neither the Claude nor the Codex CLI exposes remaining quota (spike, 2026-07-20), so the
threshold is computed the only honest way available: a user-**declared** budget measured against
Cadora's **own recorded consumption** — the same per-node cost the archives already carry, priced
by the same read-time normalization as ``cadora usage`` and ``cadora accounts``. Nothing here
guesses at a vendor's remaining allowance, because nothing can.

Enforced at a **node boundary** only. A check in the middle of a node would either kill work in
flight or arrive too late to matter. Between nodes, a stop costs nothing: the workspace holds
every completed artifact and ``--resume-from`` continues on whichever backend still has room
(resume is backend-agnostic — the workspace is the state, not the executor).

Three policies, from least to most invasive:

* ``warn``     — log once per backend and keep going. The default, so adding a budget can never
                 change what a run *does*.
* ``stop``     — halt cleanly at the boundary and print the exact resume command. The run is
                 recorded incomplete (never "passed"), because it did not finish.
* ``failover`` — move the remaining nodes to another backend, provided that one is itself under
                 threshold. If it is not, this degrades to ``stop`` rather than quietly burning
                 a second account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

BUDGET_WARN = "warn"
BUDGET_STOP = "stop"
BUDGET_FAILOVER = "failover"
BUDGET_ACTIONS = (BUDGET_WARN, BUDGET_STOP, BUDGET_FAILOVER)


@dataclass
class BudgetPolicy:
    """A declared ceiling per backend, and what to do when a run approaches it."""

    budgets: dict[str, float] = field(default_factory=dict)
    warn_at: float = 0.9
    action: str = BUDGET_WARN
    failover_to: str | None = None

    def __post_init__(self) -> None:
        if self.action not in BUDGET_ACTIONS:
            raise ValueError(
                f"invalid budget action: {self.action!r} (expected one of {list(BUDGET_ACTIONS)})"
            )
        if not 0 < self.warn_at <= 1:
            raise ValueError(f"warn_at must be in (0, 1], got {self.warn_at!r}")
        for backend, amount in self.budgets.items():
            if amount <= 0:
                raise ValueError(f"budget for {backend!r} must be > 0, got {amount!r}")
        if self.action == BUDGET_FAILOVER and not self.failover_to:
            raise ValueError("budget action 'failover' requires failover_to")

    def budget_for(self, backend: str) -> float | None:
        return self.budgets.get(backend)


@dataclass
class BudgetLedger:
    """Spend per backend: what the archives already recorded, plus what THIS run has burned.

    The split matters. ``baseline`` is read once at run start, so a resumed run counts its own
    earlier nodes (they are in the archive) without double-counting them as live spend.
    """

    baseline: dict[str, float] = field(default_factory=dict)
    live: dict[str, float] = field(default_factory=dict)

    def record(self, backend: str, cost_usd: float | None) -> None:
        if cost_usd:
            self.live[backend] = self.live.get(backend, 0.0) + cost_usd

    def spent(self, backend: str) -> float:
        return self.baseline.get(backend, 0.0) + self.live.get(backend, 0.0)


@dataclass
class BudgetVerdict:
    """What the ledger says about one backend, right now."""

    backend: str
    tripped: bool
    spent_usd: float
    budget_usd: float | None = None
    fraction: float | None = None

    @property
    def summary(self) -> str:
        if self.budget_usd is None:
            return f"{self.backend}: no budget declared"
        return (
            f"{self.backend} has used ${self.spent_usd:.2f} of its ${self.budget_usd:.2f} "
            f"budget ({self.fraction:.0%})"
        )


def evaluate(ledger: BudgetLedger, policy: BudgetPolicy | None, backend: str) -> BudgetVerdict:
    """Where ``backend`` stands. An undeclared budget never trips — silence is not a threshold."""
    budget = policy.budget_for(backend) if policy else None
    spent = ledger.spent(backend)
    if not budget:
        return BudgetVerdict(backend=backend, tripped=False, spent_usd=spent)
    fraction = spent / budget
    return BudgetVerdict(
        backend=backend,
        tripped=fraction >= policy.warn_at,
        spent_usd=spent,
        budget_usd=budget,
        fraction=fraction,
    )


def load_baseline(archive_root: str | Path, since: str | None = None) -> dict[str, float]:
    """Per-backend spend already on disk. A missing/empty archive is a legitimate zero."""
    from cadora.usage import summarize_usage

    baseline: dict[str, float] = {}
    try:
        summary = summarize_usage(archive_root, since=since)
    except (OSError, ValueError):
        return baseline
    for node in summary.nodes:
        if node.cost_usd:
            baseline[node.executor] = baseline.get(node.executor, 0.0) + node.cost_usd
    return baseline


def parse_budgets(pairs: list[str] | None) -> dict[str, float]:
    """``["claude=200"]`` -> ``{"claude": 200.0}``. Loud on nonsense — a mistyped ceiling that
    silently parsed to zero would either never trip or trip on the first node."""
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


def resume_hint(node_id: str, *, topology: str, run_id: str, failover_to: str | None = None) -> str:
    """The exact command that continues this run — optionally on the other backend."""
    executor = f" --executor {failover_to}" if failover_to else ""
    return (
        f"cadora run {topology} --resume-from {node_id} --run-id {run_id}{executor} ..."
        "  (re-pass the flags this run used)"
    )

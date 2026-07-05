"""The gate-remediation loop — generalizes the single-shot integrity repair to bounded N.

A failing gate (or a blocking integrity finding) feeds its own output back into a fresh,
constrained session and re-runs — bounded by attempt count and (optionally) cost. "Green" is
never the agent's claim: it means the *same* ``ShellGate.check`` passes AND integrity passes
when enforced. On any bound hit the run stops ``honest-blocked`` with the full attempt trail —
never a fabricated pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.gates import GATE_BLOCKED_PREREQUISITE, GATE_FAILED, GATE_VACUOUS, GateResult, ShellGate
from cadora.integrity import IntegrityReport, scan_toolchain_integrity
from cadora.topology import Node

STATE_COMPLETED_GREEN = "completed-green"
STATE_HONEST_BLOCKED = "honest-blocked"

BLOCKED_MAX_ATTEMPTS = "max_attempts"
BLOCKED_EXECUTOR_FAILED = "executor_failed"
BLOCKED_INTEGRITY = "integrity_blocked"
BLOCKED_COST_CEILING = "cost_ceiling"

_INTEGRITY_ENFORCED_MODES = {"enforce", "repair"}


@dataclass
class RemediationPolicy:
    max_attempts: int = 0  # 0 = disabled
    max_cost_usd: float | None = None
    enabled_statuses: tuple[str, ...] = (GATE_FAILED, GATE_VACUOUS)


@dataclass
class RemediationAttempt:
    number: int
    prompt: str
    execution: ExecutionResult
    gate: GateResult | None
    integrity: IntegrityReport | None = None
    cost_usd: float | None = None


@dataclass
class RemediationOutcome:
    state: str  # "completed-green" | "honest-blocked"
    attempts: list[RemediationAttempt] = field(default_factory=list)
    final_gate: GateResult | None = None
    final_integrity: IntegrityReport | None = None
    blocked_reason: str = ""  # "max_attempts" | "executor_failed" | "integrity_blocked" | "cost_ceiling"

    @property
    def cost_usd(self) -> float | None:
        costs = [a.cost_usd for a in self.attempts if a.cost_usd is not None]
        return sum(costs) if costs else None


def needs_remediation(
    gate_result: GateResult | None,
    integrity: IntegrityReport | None,
    integrity_mode: str,
    policy: RemediationPolicy | None,
) -> bool:
    """True when the bounded remediation loop should engage for this node's failure.

    ``blocked_prerequisite`` never triggers remediation — missing tooling is not
    agent-repairable, regardless of how many attempts remain.
    """
    if policy is None or policy.max_attempts <= 0:
        return False
    if gate_result is not None and gate_result.status == GATE_BLOCKED_PREREQUISITE:
        return False
    gate_repairable = (
        gate_result is not None
        and not gate_result.passed
        and gate_result.status in policy.enabled_statuses
    )
    integrity_blocking = (
        integrity_mode in _INTEGRITY_ENFORCED_MODES
        and integrity is not None
        and not integrity.passed
    )
    return gate_repairable or integrity_blocking


def build_remediation_prompt(
    node: Node,
    gate_command: str,
    gate: GateResult | None,
    integrity: IntegrityReport | None,
    attempt_number: int,
) -> str:
    """Build a constrained prompt for one fresh remediation attempt.

    Mirrors the hard rules of ``integrity.repair_prompt``: the exact gate failure detail is
    fed back verbatim, and the agent is told, in no uncertain terms, not to weaken the gate.
    """
    status = gate.status if gate is not None else "unknown"
    detail = gate.detail if gate is not None else ""
    finding_text = "\n".join(
        f"- [{f.severity}] {f.rule} at {f.path}: {f.detail}"
        + (f" Evidence: {f.evidence}" if f.evidence else "")
        for f in (integrity.findings if integrity is not None else [])
    )
    return f"""You are a fresh remediation session (attempt {attempt_number}) for stage {node.id!r}.

The verification gate for this stage did not pass. Fix the REAL problem in the workspace so
that the exact same gate genuinely passes — do not touch or weaken the gate itself.

Gate command:
{gate_command or "(no gate configured)"}

Gate status: {status}

Gate output:
{detail or "(no gate output)"}

Integrity findings:
{finding_text or "(none)"}

Hard requirements:
- Do not weaken, delete, skip, or bypass the gate command or the tests it runs.
- Do not create or retain local packages/scripts that impersonate pytest, pip, setuptools,
  TypeScript, tsc, npm, or another declared tool.
- Write real, substantive code and tests — a gate that passes having run zero tests is a
  vacuous pass, not a fix.
- If a genuine blocker (missing tooling, ambiguous spec) prevents a fix, leave the project
  truthfully blocked and document the blocker; never claim success you did not achieve.
- Preserve existing application behavior and the project's security baseline.
- Re-run the gate command yourself before finishing, to confirm the fix actually holds.
"""


def run_remediation(
    node: Node,
    node_executor: NodeExecutor,
    node_cwd: str,
    gate: ShellGate | None,
    gate_result: GateResult | None,
    integrity: IntegrityReport | None,
    integrity_mode: str,
    policy: RemediationPolicy,
) -> RemediationOutcome:
    """Run up to ``policy.max_attempts`` fresh remediation sessions for one node.

    Each attempt gets its own synthetic node id ``{node.id}-remediate-{k}`` and a prompt built
    from the current gate/integrity detail — never the agent's own claim of success. The same
    gate is re-run and integrity rescanned after every attempt; "green" requires both to hold.
    """
    attempts: list[RemediationAttempt] = []
    current_gate = gate_result
    current_integrity = integrity
    total_cost = 0.0

    for attempt_number in range(1, policy.max_attempts + 1):
        if policy.max_cost_usd is not None and total_cost >= policy.max_cost_usd:
            return RemediationOutcome(
                state=STATE_HONEST_BLOCKED,
                attempts=attempts,
                final_gate=current_gate,
                final_integrity=current_integrity,
                blocked_reason=BLOCKED_COST_CEILING,
            )

        prompt = build_remediation_prompt(
            node, gate.command if gate is not None else "", current_gate, current_integrity,
            attempt_number,
        )
        remediate_node = Node(
            id=f"{node.id}-remediate-{attempt_number}",
            role="remediate",
            phase=node.phase,
            prompt="",
            tools=node.tools,
            model=node.model,
        )
        execution = node_executor.run(remediate_node, prompt, cwd=node_cwd)
        execution.executor = node_executor.name

        current_gate = gate.check(node_cwd) if gate is not None else None
        current_integrity = (
            scan_toolchain_integrity(node_cwd) if integrity_mode != "off" else None
        )
        total_cost += execution.cost_usd or 0.0
        attempts.append(
            RemediationAttempt(
                number=attempt_number,
                prompt=prompt,
                execution=execution,
                gate=current_gate,
                integrity=current_integrity,
                cost_usd=execution.cost_usd,
            )
        )

        if not execution.ok:
            return RemediationOutcome(
                state=STATE_HONEST_BLOCKED,
                attempts=attempts,
                final_gate=current_gate,
                final_integrity=current_integrity,
                blocked_reason=BLOCKED_EXECUTOR_FAILED,
            )

        gate_ok = current_gate is None or current_gate.passed
        integrity_ok = (
            integrity_mode not in _INTEGRITY_ENFORCED_MODES
            or current_integrity is None
            or current_integrity.passed
        )
        # A false claim of success from the executor never substitutes for the gate: green
        # is decided here, by re-running the SAME deterministic check, not by execution.ok.
        if gate_ok and integrity_ok:
            return RemediationOutcome(
                state=STATE_COMPLETED_GREEN,
                attempts=attempts,
                final_gate=current_gate,
                final_integrity=current_integrity,
            )

    gate_ok = current_gate is None or current_gate.passed
    reason = BLOCKED_MAX_ATTEMPTS if not gate_ok else BLOCKED_INTEGRITY
    return RemediationOutcome(
        state=STATE_HONEST_BLOCKED,
        attempts=attempts,
        final_gate=current_gate,
        final_integrity=current_integrity,
        blocked_reason=reason,
    )

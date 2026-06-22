"""Post-step gates — the security / quality checks that run after a node.

Deterministic-first, per Anthropic's own verification ranking (rules-based
checks > visual > LLM-judge). A ``ShellGate`` runs a real command (linter,
tests, secret scan) and BLOCKS the run on non-zero exit. A reviewer-subagent
(LLM-judge) gate is the last resort — left as a stub.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class ShellGate:
    name: str
    command: str  # e.g. "ruff check . && pytest -q"

    def check(self, cwd: str) -> GateResult:
        proc = subprocess.run(
            self.command, cwd=cwd, shell=True, capture_output=True, text=True
        )
        return GateResult(
            name=self.name,
            passed=proc.returncode == 0,
            detail=(proc.stdout + proc.stderr)[-2000:],
        )


# TODO: ReviewerGate — spawn a reviewer subagent (/security-review style) for
# semantic checks the shell can't express. Demoted below the deterministic gates.

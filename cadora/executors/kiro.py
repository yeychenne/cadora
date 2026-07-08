"""Kiro CLI backend — the AWS-native executor.

Drives ``kiro-cli chat --no-interactive`` headlessly. Kiro emits plain text to
stdout (with ANSI escape codes) and credits/status to stderr — there is no
structured JSON event stream, so ``events`` stays empty and we normalize on
exit code + parsed text + credits from stderr.

Funding: Kiro credits (included with subscription). No ambient key-stripping
needed — Kiro's auth is its own login/token.

Differences from Claude Code:
  - No ``stream-json`` — we strip ANSI and capture plain text.
  - Credits reported in stderr (``Credits: N.NN``), not in a structured event.
  - ``--trust-tools`` (or ``-a``) for autonomous headless operation.
  - ``--wrap never`` for clean piped capture.
  - ``--effort`` controls reasoning depth (low/medium/high/xhigh/max).
"""

from __future__ import annotations

import os
import re
import subprocess

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.topology import Node

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\[\?[0-9]*[hlm]")
_CREDITS_RE = re.compile(r"Credits:\s*([\d.]+)")
_TIME_RE = re.compile(r"Time:\s*(\d+)s")
_PROMPT_PREFIX_RE = re.compile(r"^>\s*", re.MULTILINE)


class KiroExecutor(NodeExecutor):
    name = "kiro"
    funding = "kiro/credits"  # Kiro bills subscription credits, not tokens/dollars

    def __init__(
        self,
        binary: str = "kiro-cli",
        timeout: int = 1800,
        effort: str | None = None,
        trust_all: bool = True,
    ):
        self.binary = binary
        self.timeout = timeout
        self.effort = effort
        self.trust_all = trust_all

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        cmd = [self.binary, "chat", "--no-interactive", "--wrap", "never"]
        if self.trust_all:
            cmd.append("--trust-all-tools")
        elif node.tools:
            cmd += ["--trust-tools", ",".join(node.tools)]
        if node.model:
            cmd += ["--model", node.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        cmd.append(prompt)

        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env={**os.environ, **(env or {})},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            text = _strip_ansi(_as_text(exc.stdout))
            return ExecutionResult(
                node_id=node.id,
                ok=False,
                exit_code=124,
                text=text,
                model=node.model,
                meta={"timed_out": True, "timeout_seconds": self.timeout},
            )

        text = _strip_ansi(proc.stdout)
        meta = _parse_stderr(proc.stderr)
        meta["effort"] = self.effort
        if proc.returncode != 0:
            # Surface WHY Kiro failed (auth? credits? crash?) instead of a bare "executor failed".
            tail = _strip_ansi(proc.stderr).strip()
            if tail:
                meta["stderr_tail"] = tail[-500:]
        return ExecutionResult(
            node_id=node.id,
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            text=text,
            model=node.model,
            usage={"credits": meta.pop("credits", None)},
            meta={k: v for k, v in meta.items() if v is not None},
        )


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes and the leading '> ' prompt marker from Kiro output."""
    clean = _ANSI_RE.sub("", text)
    clean = _PROMPT_PREFIX_RE.sub("", clean)
    # Collapse blank lines left by stripped control sequences
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def _parse_stderr(stderr: str) -> dict:
    """Extract credits and timing from Kiro's stderr status line."""
    clean = _ANSI_RE.sub("", stderr)
    meta: dict = {}
    m = _CREDITS_RE.search(clean)
    if m:
        meta["credits"] = float(m.group(1))
    m = _TIME_RE.search(clean)
    if m:
        meta["duration_seconds"] = int(m.group(1))
    return meta


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""

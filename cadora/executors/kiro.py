"""Kiro CLI backend — the AWS / demo executor.

Drives ``kiro-cli chat --no-interactive``. Two deliberate differences from the
Claude Code backend: Kiro's headless turn emits
PLAIN TEXT (no ``stream-json``), so ``events`` stays empty and we key on exit
code + text; and funding is a Kiro credit license via ``KIRO_API_KEY``. Kiro's
spec/tasks "waves" engine is GUI-only, so Cadora owns the DAG regardless.
"""

from __future__ import annotations

import os
import subprocess

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.topology import Node


class KiroExecutor(NodeExecutor):
    name = "kiro"

    def __init__(self, binary: str = "kiro-cli", timeout: int = 1800):
        self.binary = binary
        self.timeout = timeout

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        cmd = [self.binary, "chat", "--no-interactive"]
        # Headless Kiro blocks on a tool-approval prompt unless tools are trusted.
        cmd += ["--trust-tools", ",".join(node.tools) if node.tools else "*"]
        cmd.append(prompt)
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        return ExecutionResult(
            node_id=node.id,
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            text=proc.stdout,  # plain text — no structured event stream
        )

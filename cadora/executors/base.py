"""The runner-agnostic execution boundary.

Cadora's core decision: it does NOT implement an agent loop. Each topology node
is executed by a ``NodeExecutor`` backend that drives an external headless
coding agent (Claude Code, Kiro CLI, ...). Swapping backends is a one-class
change, so Cadora is not locked to any vendor and the same topology can run on
either backend — which makes A/B-ing runners a first-class capability.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from cadora.topology import Node


@dataclass
class ExecutionResult:
    node_id: str
    ok: bool  # NORMALIZED success: process exited 0 AND no terminal/semantic error
    exit_code: int
    text: str = ""  # final assistant text (always available)
    events: list[dict] = field(default_factory=list)  # structured events if the backend emits them
    usage: dict = field(default_factory=dict)  # token/credit usage if reported
    cost_usd: float | None = None  # per-node cost when the backend reports it; else None
    model: str | None = None  # model actually used, when reported
    meta: dict = field(default_factory=dict)  # backend extras (session_id, funding, num_turns, ...)
    artifacts_dir: str | None = None


class NodeExecutor(abc.ABC):
    """Drives one node to completion via an external headless agent CLI."""

    name: str = "base"

    @abc.abstractmethod
    def run(
        self,
        node: Node,
        prompt: str,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute ``prompt`` for ``node``, restricted to ``node.tools``, in ``cwd``."""
        raise NotImplementedError

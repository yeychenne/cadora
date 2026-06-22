"""Executor backends + a small registry to resolve one by name."""

from __future__ import annotations

import inspect

from cadora.executors.antigravity import AntigravityExecutor
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.executors.claude_code import ClaudeCodeExecutor
from cadora.executors.codex import CodexExecutor
from cadora.executors.kiro import KiroExecutor

_REGISTRY: dict[str, type[NodeExecutor]] = {
    ClaudeCodeExecutor.name: ClaudeCodeExecutor,    # claude — default; structured stream-json
    KiroExecutor.name: KiroExecutor,                # kiro — AWS/demo; plain text
    CodexExecutor.name: CodexExecutor,              # codex — OpenAI; structured JSONL
    AntigravityExecutor.name: AntigravityExecutor,  # antigravity — Google; EXPERIMENTAL (agy)
}


def get_executor(name: str, **kwargs) -> NodeExecutor:
    """Resolve an executor by name, forwarding only the kwargs its constructor accepts.

    Lets the CLI pass e.g. ``funding=...`` uniformly; a backend that doesn't accept a
    given kwarg simply ignores it.
    """
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise SystemExit(f"unknown executor {name!r}; choose from {sorted(_REGISTRY)}")
    params = inspect.signature(cls).parameters
    accepted = {k: v for k, v in kwargs.items() if k in params}
    return cls(**accepted)


__all__ = ["NodeExecutor", "ExecutionResult", "get_executor"]

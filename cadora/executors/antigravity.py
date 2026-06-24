"""Google Antigravity CLI (``agy``) backend — EXPERIMENTAL.

Drives ``agy -p`` headless. Two current defects make this rougher than the other
backends (researched 2026-06-19):

  1. **No JSON output** — ``--output-format`` is not implemented on ``agy``.
  2. **``agy -p`` stdout is broken** — it writes the response to the controlling
     terminal, NOT stdout, so piping/capture yields nothing. The workaround is to
     read agy's own transcript JSONL under
     ``~/.gemini/antigravity-cli/brain/<conv-id>/.system_generated/logs/transcript.jsonl``
     and recover the ``PLANNER_RESPONSE`` content (see ``_read_transcript_fallback``).

Auth: Google sign-in (free tier incl. Gemini 3) or an API key. NOTE: the legacy
``gemini`` CLI reached EOL for free tiers on 2026-06-18 — target ``agy``, not ``gemini``.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import time

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.topology import Node


class AntigravityExecutor(NodeExecutor):
    name = "antigravity"

    def __init__(self, binary: str = "agy", timeout: int = 1800):
        self.binary = binary
        self.timeout = timeout

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        cmd = [self.binary, "-p", prompt, "--dangerously-skip-permissions"]
        if node.model:
            cmd += ["--model", node.model]
        started = time.time()
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        # agy -p stdout is unreliable; fall back to the transcript written DURING this run.
        text = proc.stdout or _read_transcript_fallback(cwd, since=started)
        return ExecutionResult(
            node_id=node.id,
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            text=text,  # plain text; no structured events on agy today
        )


def _default_brain_dirs() -> list[str]:
    """The known ``agy`` brain roots (explicit layouts first, then any sibling install)."""
    roots = [
        os.path.expanduser("~/.gemini/antigravity/brain"),
        os.path.expanduser("~/.gemini/antigravity-cli/brain"),
    ]
    roots.extend(sorted(glob.glob(os.path.expanduser("~/.gemini/antigravity*/brain"))))
    return roots


def _read_transcript_fallback(
    cwd: str,
    *,
    since: float | None = None,
    brain_dirs: list[str] | None = None,
) -> str:
    """Recover an ``agy -p`` response from its transcript JSONL (agy's stdout is broken).

    Picks the conversation whose transcript was written during THIS run: ``since`` is the run's
    start time (``time.time()`` taken before launching ``agy``); only transcripts modified at/after
    it are considered, so a *stale or concurrent* conversation is never returned. Returns the joined
    ``PLANNER_RESPONSE`` content ("" if none found). ``brain_dirs`` overrides the search roots (tests).

    Residual limitation: two agy runs overlapping within the same ``since`` window can still be
    ambiguous — agy exposes no per-invocation conversation id to correlate precisely.
    """
    roots = brain_dirs if brain_dirs is not None else _default_brain_dirs()
    brain_dir = next((d for d in roots if os.path.isdir(d)), None)
    if not brain_dir:
        return ""

    # (transcript_mtime, transcript_path) for every conversation that has a transcript.
    candidates: list[tuple[float, str]] = []
    for name in os.listdir(brain_dir):
        logs = os.path.join(brain_dir, name, ".system_generated", "logs")
        for fname in ("transcript_full.jsonl", "transcript.jsonl"):
            tpath = os.path.join(logs, fname)
            if os.path.isfile(tpath):
                candidates.append((os.path.getmtime(tpath), tpath))
                break

    if since is not None:
        # Only transcripts touched during this run — never a stale/other conversation's output.
        # Small grace for filesystem mtime granularity / clock skew.
        candidates = [c for c in candidates if c[0] >= since - 2.0]
    if not candidates:
        return ""

    _, transcript_path = max(candidates)
    responses: list[str] = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get("type") == "PLANNER_RESPONSE" and data.get("content"):
                responses.append(data["content"])
    return "\n".join(responses)

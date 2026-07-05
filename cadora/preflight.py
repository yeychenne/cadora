"""Autonomous-run preflight — the honest trust gate.

Cadora drives backends with ``--dangerously-skip-permissions`` (autonomous mode) inside the
``--cwd`` you give it: agents read, write, and run commands there with the full permissions of
the calling user, and gates may install dependencies (executing agent-authored build hooks).
Cadora **audits the output** (deterministic gates, integrity, evidence) — it does **not sandbox
the execution**. That distinction has to be visible, not buried.

So every autonomous run prints a blast-radius banner, and an interactive first run asks for a
one-time acknowledgment. Automation is never blocked: a TTY-less stdin, ``--yes``, or
``CADORA_ASSUME_YES=1`` proceeds without prompting.
"""

from __future__ import annotations

import os
import sys

ASSUME_YES_ENV = "CADORA_ASSUME_YES"


def preflight_autonomous(
    *, cwd: str, executor: str, autonomous: bool, assume_yes: bool = False, stream=None
) -> bool:
    """Show the blast-radius banner and gate an autonomous run. Return True to proceed.

    Non-interactive contexts (no TTY, ``--yes``, or ``CADORA_ASSUME_YES=1``) proceed after the
    banner without prompting, so CI and scripted runs are never blocked.
    """
    out = stream or sys.stderr
    if not autonomous:
        return True

    workspace = os.path.abspath(cwd)
    print(
        "\n".join(
            [
                "",
                "  ┌─ cadora · autonomous run ─────────────────────────────────────────",
                f"  │  backend    : {executor} (--dangerously-skip-permissions)",
                f"  │  workspace  : {workspace}",
                "  │  Cadora audits the agent's OUTPUT (gates · integrity · evidence),",
                "  │  it does NOT sandbox EXECUTION. The agent can read/write/run there",
                "  │  with your permissions. Point it only at a trusted or throwaway",
                "  │  workspace (a fresh dir, worktree, or container). Keep credentials",
                "  │  out of that environment.",
                "  └───────────────────────────────────────────────────────────────────",
            ]
        ),
        file=out,
    )

    if assume_yes or os.environ.get(ASSUME_YES_ENV):
        return True
    if not sys.stdin or not sys.stdin.isatty():
        # Headless/CI: the banner is the record; do not block automation.
        return True

    try:
        answer = input(f"  Proceed with an autonomous run in {workspace}? [y/N] ").strip().lower()
    except EOFError:
        return True
    if answer in ("y", "yes"):
        return True
    print("  aborted — no run started.", file=out)
    return False

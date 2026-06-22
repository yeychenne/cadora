"""AI-DLC workspace setup — install the vendored rules + inputs for a run.

Driving the AI-DLC method on Claude Code needs the workflow installed in the
target workspace (the upstream "Project Root" setup):

  - ``CLAUDE.md``            := the AI-DLC core workflow (auto-loaded project memory)
  - ``.aidlc-rule-details/`` := the per-stage rule detail files (read on demand)
  - ``vision.md``            := the product vision (required input for a real run)
  - ``tech-env.md``          := optional technical-environment input

The rules are vendored under ``cadora/aidlc_rules/`` (MIT-0; refreshed by
``scripts/refresh-aidlc-rules.sh``) so setup works offline and pins a known
rule-set version.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_RULES_DIR = Path(__file__).resolve().parent / "aidlc_rules"
_CORE_WORKFLOW = _RULES_DIR / "core-workflow.md"
_RULE_DETAILS = _RULES_DIR / "rule-details"


def rules_version() -> str:
    """Return the vendored AI-DLC rule-set version (or ``"unknown"``)."""
    vf = _RULES_DIR / "RULES_VERSION"
    if vf.exists():
        for line in vf.read_text().splitlines():
            if line.startswith("ai-dlc-rules:"):
                return line.split(":", 1)[1].strip()
    return "unknown"


def setup_aidlc_workspace(
    workspace: str | Path,
    *,
    vision: str | Path | None = None,
    tech_env: str | Path | None = None,
) -> Path:
    """Install the vendored AI-DLC rules + inputs into ``workspace``.

    ``vision`` / ``tech_env`` may be a path to a file (copied) or inline text
    (written as-is). Returns the workspace path. Raises ``FileNotFoundError`` if
    the vendored rules are missing (run ``scripts/refresh-aidlc-rules.sh``).
    """
    if not _CORE_WORKFLOW.exists() or not _RULE_DETAILS.is_dir():
        raise FileNotFoundError(
            f"vendored AI-DLC rules missing under {_RULES_DIR}; "
            "run scripts/refresh-aidlc-rules.sh"
        )

    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    # CLAUDE.md — the core workflow, auto-loaded by Claude Code as project memory.
    shutil.copyfile(_CORE_WORKFLOW, ws / "CLAUDE.md")

    # .aidlc-rule-details/ — the on-demand per-stage rule files (fully replaced).
    dest_details = ws / ".aidlc-rule-details"
    if dest_details.exists():
        shutil.rmtree(dest_details)
    shutil.copytree(_RULE_DETAILS, dest_details)

    if vision is not None:
        (ws / "vision.md").write_text(_resolve_input(vision))
    if tech_env is not None:
        (ws / "tech-env.md").write_text(_resolve_input(tech_env))

    return ws


def _resolve_input(src: str | Path) -> str:
    """Read ``src`` as a file if it points at one, else treat it as inline text."""
    try:
        p = Path(src)
        if p.is_file():
            return p.read_text()
    except OSError:
        pass  # e.g. inline content too long to be a valid path
    return str(src)

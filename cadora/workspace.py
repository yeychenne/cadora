"""AI-DLC workspace setup — install the vendored rules + inputs for a run.

Driving the AI-DLC method needs the workflow installed as project memory for
the selected coding agent:

  - ``CLAUDE.md`` / ``AGENTS.md`` := AI-DLC core workflow (agent-native project memory)
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
_INSTRUCTION_FILES = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
}
_MANAGED_START = "<!-- cadora:aidlc:start -->"
_MANAGED_END = "<!-- cadora:aidlc:end -->"

_SECURITY_BASELINE = """

# Cadora security baseline — hard constraints

- Every AWS Cognito User Pool created or changed by this project MUST disable
  self-registration. Set `AllowAdminCreateUserOnly=True` (Terraform:
  `admin_create_user_config { allow_admin_create_user_only = true }`). An
  ungated pool is a blocking security finding.
- NEVER create or depend on long-term Amazon Bedrock API keys or other
  long-term static AWS credentials. Do not call
  `CreateServiceSpecificCredential` for `bedrock.amazonaws.com`.
- Prefer IAM roles and SigV4 with STS short-term credentials. If a Bedrock
  bearer token is genuinely required, use an auto-refreshed short-term token
  (maximum lifetime 12 hours) and never persist it.
""".strip()


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
    executor: str = "claude",
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

    instruction_file = workspace_instruction_file(executor)
    instructions = _CORE_WORKFLOW.read_text().rstrip() + "\n\n" + _SECURITY_BASELINE
    _install_project_memory(ws / instruction_file, instructions)

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


def workspace_instruction_file(executor: str) -> str:
    """Return the project-memory filename used by a supported executor."""
    try:
        return _INSTRUCTION_FILES[executor]
    except KeyError:
        # Text/experimental backends do not have a Cadora-managed memory file yet.
        # Keep the historical Claude-compatible setup for those adapters.
        return "CLAUDE.md"


def _install_project_memory(path: Path, instructions: str) -> None:
    """Install or refresh Cadora's managed block without erasing local rules."""
    managed = f"{_MANAGED_START}\n{instructions.rstrip()}\n{_MANAGED_END}"
    if not path.exists():
        path.write_text(managed + "\n")
        return

    existing = path.read_text()
    start = existing.find(_MANAGED_START)
    end = existing.find(_MANAGED_END)
    if start >= 0 and end >= start:
        end += len(_MANAGED_END)
        updated = existing[:start] + managed + existing[end:]
    else:
        updated = managed + "\n\n# Existing project instructions\n\n" + existing.lstrip()
    path.write_text(updated.rstrip() + "\n")


def _resolve_input(src: str | Path) -> str:
    """Read ``src`` as a file if it points at one, else treat it as inline text."""
    try:
        p = Path(src)
        if p.is_file():
            return p.read_text()
    except OSError:
        pass  # e.g. inline content too long to be a valid path
    return str(src)

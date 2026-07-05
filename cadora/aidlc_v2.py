"""EXPERIMENTAL aidlc-workflows 2.0 method pack — guarded install + audit/state ingestion.

Upstream v2 ("one core, many harnesses") installs by copying its ``dist/claude`` tree into a
workspace. Two things make a *guarded* installer necessary rather than a bare ``cp -r``:

1. The shipped ``.claude/settings.json`` silently re-points every Claude Code session in the
   workspace at **AWS Bedrock us-east-1 with ``model: opus[1m]`` at ``effortLevel: xhigh``** —
   switching funding from subscription to metered on the most expensive configuration. This
   installer strips those provider/cost pins by default and **records exactly what it stripped**.
2. The shipped ``.mcp.json`` wires five remote MCP servers (``uvx …@latest`` pulls). Remote
   tooling should be an explicit opt-in, so it is not installed unless asked.

The pack is **pinned**: the default ref is a tag whose commit hash is verified after fetch — a
moved tag fails the install rather than silently shipping different code.

Ingestion is read-only: v2 writes a per-intent state file (``aidlc-state.md``, six-state
checkboxes) and an append-only audit trail (``audit/<host>-<clone>.md`` shards, 68-event
taxonomy, ISO timestamps). ``ingest_intent`` parses both into one archive-shaped dict — whether
the workflow was driven by Cadora or by a human interactively in their IDE.

Upstream is a GA preview with weekly churn; the full external *driver* (session-per-segment
conduction with human-relayed gates) is deliberately deferred until its gate surface stabilizes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM_URL = "https://github.com/awslabs/aidlc-workflows"
PINNED_REF = "v2.1.7"
PINNED_COMMIT = "fde1e1af7aae16f4c4defc991abaa3877ee2ac26"
INSTALL_RECORD = ".cadora-aidlc-v2.json"

# Provider/cost pins stripped from settings.json by default. AWS_AIDLC_DEFAULT_SCOPE is method
# behavior, not a provider pin — it stays.
_PROVIDER_ENV_KEYS = ("CLAUDE_CODE_USE_BEDROCK", "AWS_REGION")
_PROVIDER_ENV_PREFIXES = ("ANTHROPIC_DEFAULT_",)
_PROVIDER_TOP_KEYS = ("model", "effortLevel")


class InstallError(RuntimeError):
    pass


def install_v2(
    workspace: str | Path,
    *,
    source: str | Path | None = None,
    ref: str = PINNED_REF,
    keep_provider_pins: bool = False,
    keep_mcp: bool = False,
    force: bool = False,
) -> dict:
    """Install the v2 pack into ``workspace`` and return the install record.

    ``source`` (a local checkout of the upstream repo) skips the network fetch — used by tests
    and offline workflows; otherwise the pinned ref is cloned and its commit verified.
    """
    ws = Path(workspace).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)
    if (ws / ".claude").exists() and not force:
        raise InstallError(
            f"{ws}/.claude already exists — pass force=True (--force) to overwrite the pack files"
        )

    tmp: tempfile.TemporaryDirectory | None = None
    try:
        if source is not None:
            root, commit = Path(source), _local_commit(Path(source))
        else:
            tmp = tempfile.TemporaryDirectory(prefix="cadora-aidlc-v2-")
            root, commit = _fetch(ref, Path(tmp.name))
        dist = root / "dist" / "claude"
        if not dist.is_dir():
            raise InstallError(f"no dist/claude in the aidlc-workflows source at {root}")

        shutil.copytree(dist / ".claude", ws / ".claude", dirs_exist_ok=force)
        if (dist / "aidlc").is_dir():
            shutil.copytree(dist / "aidlc", ws / "aidlc", dirs_exist_ok=True)

        stripped: dict = {}
        if not keep_provider_pins:
            stripped = _strip_provider_pins(ws / ".claude" / "settings.json")

        mcp_servers = _mcp_server_names(dist / ".mcp.json")
        if keep_mcp and (dist / ".mcp.json").exists():
            shutil.copy2(dist / ".mcp.json", ws / ".mcp.json")

        record = {
            "pack": "aidlc-v2",
            "upstream": UPSTREAM_URL,
            "ref": ref,
            "commit": commit,
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "provider_pins_stripped": stripped,
            "mcp_installed": bool(keep_mcp and mcp_servers),
            "mcp_servers_available": mcp_servers,
            "bun_found": shutil.which("bun") is not None,
        }
        (ws / INSTALL_RECORD).write_text(json.dumps(record, indent=2) + "\n")
        return record
    finally:
        if tmp is not None:
            tmp.cleanup()


def _fetch(ref: str, into: Path) -> tuple[Path, str]:
    """Shallow-clone the pinned ref and verify its commit — a moved tag is a hard failure."""
    dest = into / "aidlc-workflows"
    proc = subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "-b", ref, UPSTREAM_URL, str(dest)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise InstallError(f"fetching {UPSTREAM_URL}@{ref} failed: {proc.stderr.strip()[:200]}")
    commit = _local_commit(dest) or ""
    if ref == PINNED_REF and commit != PINNED_COMMIT:
        raise InstallError(
            f"pin verification FAILED: {ref} now points at {commit[:12]}, expected "
            f"{PINNED_COMMIT[:12]} — the upstream tag moved; refusing to install unverified code"
        )
    return dest, commit


def _local_commit(root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.stdout.strip() or None if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _strip_provider_pins(settings_path: Path) -> dict:
    if not settings_path.exists():
        return {}
    settings = json.loads(settings_path.read_text())
    stripped: dict = {}
    for key in _PROVIDER_TOP_KEYS:
        if key in settings:
            stripped[key] = settings.pop(key)
    env = settings.get("env") or {}
    removed_env = {
        k: env.pop(k)
        for k in list(env)
        if k in _PROVIDER_ENV_KEYS or k.startswith(_PROVIDER_ENV_PREFIXES)
    }
    if removed_env:
        stripped["env"] = removed_env
    if not env:
        settings.pop("env", None)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return stripped


def _mcp_server_names(mcp_path: Path) -> list[str]:
    if not mcp_path.exists():
        return []
    try:
        return sorted((json.loads(mcp_path.read_text()).get("mcpServers") or {}).keys())
    except (json.JSONDecodeError, OSError):
        return []


# --- ingestion (read-only) ---------------------------------------------------------------


def find_intents(workspace: str | Path) -> list[Path]:
    """All intent record dirs under the workspace, oldest → newest (dirs are date-prefixed)."""
    ws = Path(workspace)
    return sorted(p for p in ws.glob("aidlc/spaces/*/intents/*") if p.is_dir())


def ingest_intent(intent_dir: str | Path) -> dict:
    """Parse one intent's state file + audit shards into an archive-shaped dict."""
    intent = Path(intent_dir)
    events = sorted(
        (e for p in sorted((intent / "audit").glob("*.md")) for e in _parse_shard(p)),
        key=lambda e: e.get("timestamp", ""),
    )
    counts: dict[str, int] = {}
    for e in events:
        counts[e["event"]] = counts.get(e["event"], 0) + 1
    return {
        "source": "aidlc-workflows-v2",
        "intent": intent.name,
        "space": intent.parent.parent.name,
        "state": _parse_state(intent / "aidlc-state.md"),
        "event_count": len(events),
        "event_counts": dict(sorted(counts.items())),
        "gates": [e for e in events if e["event"].startswith("GATE_")],
        "human_turns": counts.get("HUMAN_TURN", 0),
        "sensors": {
            "fired": counts.get("SENSOR_FIRED", 0),
            "passed": counts.get("SENSOR_PASSED", 0),
            "failed": counts.get("SENSOR_FAILED", 0),
        },
        "events": events,
    }


def _parse_shard(path: Path) -> list[dict]:
    """One audit shard: '## Title' blocks with '**Field**: value' lines; keep event entries."""
    events: list[dict] = []
    current: dict | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## "):
            if current and "event" in current:
                events.append(current)
            current = {"title": line[3:].strip(), "shard": path.name}
            continue
        match = re.match(r"\*\*([\w][\w /-]*)\*\*: (.*)", line.strip())
        if match and current is not None:
            current[match.group(1).strip().lower().replace(" ", "_")] = match.group(2)
    if current and "event" in current:
        events.append(current)
    return events


def _parse_state(path: Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")

    def field(label: str) -> str | None:
        match = re.search(rf"\*\*{label}\*\*: (.+)", text)
        return match.group(1).strip() if match else None

    stages = {name: box for box, name in re.findall(r"^- \[(.)\] ([\w-]+)", text, re.M)}
    rollup: dict[str, int] = {}
    for box in stages.values():
        rollup[box] = rollup.get(box, 0) + 1
    return {
        "phase": field("Lifecycle Phase"),
        "current_stage": field("Current Stage"),
        "next_stage": field("Next Stage"),
        "status": field("Status"),
        "stages": stages,
        "stage_rollup": rollup,  # keys: ' ' not-started, '-', '?', 'R', 'x', 'S'
    }

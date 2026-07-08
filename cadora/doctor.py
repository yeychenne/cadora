"""Backend-CLI contract checks — catch the adapter treadmill before a run does.

Backend CLIs ship weekly (Codex publishes near-daily) with no machine-output stability
guarantee, so the riskiest failure mode is silent contract drift at a user's machine.
``cadora doctor`` verifies, deterministically and offline (no model calls, no network):
the Python floor, each backend binary's presence, and whether its version falls inside
the range the adapter contract was last verified against. Outside-range is a WARNING
(``untested``) — it usually still works; missing/unparsable is the hard signal.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass

# Version ranges each adapter contract was last live-verified against (release testing +
# scripts/live-smoke.sh). None = unbounded on that side. Bump when a live smoke re-verifies.
_TESTED: dict[str, tuple[str | None, str | None]] = {
    "claude": ("2.1.0", None),  # stream-json result contract verified on 2.1.128+
    "codex": ("0.130.0", None),  # exec --json contract verified on 0.130.0–0.142.x
    "kiro": ("2.10.0", None),  # kiro-cli contract live-verified on 2.10.0+
    "glm": (None, None),  # uses claude CLI against Z.ai; readiness is env + claude present
}

# Support tier — the single source of truth for how mature each backend adapter is.
# "verified": live-smoke-verified each release and carries a tested version range above.
# "experimental": works, but is NOT in the release smoke — promotion needs a live smoke.
# (The fixture backend is test-only and intentionally unlisted.)
SUPPORT: dict[str, str] = {
    "claude": "verified",
    "codex": "verified",
    "kiro": "verified",
    "glm": "experimental",
    "antigravity": "experimental",
}


@dataclass
class BackendCheck:
    backend: str
    binary: str
    status: str  # ok | missing | unparsable | untested
    version: str | None = None
    detail: str = ""
    tier: str = ""  # verified | experimental | "" (non-backend checks: python, bun)

    def __post_init__(self) -> None:
        if not self.tier:
            self.tier = SUPPORT.get(self.backend, "")

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_version(text: str) -> str | None:
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", text or "")
    return match.group(1) if match else None


def _vtuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def check_backend(backend: str, binary: str | None = None) -> BackendCheck:
    binary = binary or backend
    if not shutil.which(binary):
        return BackendCheck(backend, binary, "missing", detail=f"'{binary}' not on PATH")
    try:
        proc = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=20
        )
        raw = (proc.stdout or proc.stderr or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return BackendCheck(backend, binary, "unparsable", detail=f"--version failed: {exc}")
    if proc.returncode != 0:
        # A failing probe often prints stack traces containing digits — never parse a
        # "version" out of one (seen live: a broken npm codex wrapper's ENOENT trace).
        return BackendCheck(
            backend,
            binary,
            "unparsable",
            detail=f"--version exited {proc.returncode}: {raw[:80]!r}",
        )
    version = _parse_version(raw)
    if not version:
        return BackendCheck(
            backend, binary, "unparsable", detail=f"no version in {raw[:60]!r}"
        )
    minimum, maximum = _TESTED.get(backend, (None, None))
    if minimum and _vtuple(version) < _vtuple(minimum):
        return BackendCheck(
            backend, binary, "untested", version, f"below tested minimum {minimum}"
        )
    if maximum and _vtuple(version) > _vtuple(maximum):
        return BackendCheck(
            backend, binary, "untested", version, f"above tested maximum {maximum}"
        )
    return BackendCheck(backend, binary, "ok", version)


def check_glm() -> BackendCheck:
    """GLM runs through Claude Code against Z.ai; no separate binary is required."""
    binary = "claude"
    if not shutil.which(binary):
        return BackendCheck("glm", binary, "missing", detail="'claude' not on PATH")
    if not os.environ.get("ZAI_API_KEY"):
        return BackendCheck(
            "glm",
            binary,
            "missing",
            detail="ZAI_API_KEY not set; glm needs it for Z.ai",
        )
    return BackendCheck("glm", binary, "ok")


def run_doctor() -> list[BackendCheck]:
    """All checks, environment first. Fixture needs no check — it has no external contract."""
    py = sys.version_info
    python_ok = py >= (3, 10)
    checks = [
        BackendCheck(
            "python",
            sys.executable,
            "ok" if python_ok else "untested",
            f"{py.major}.{py.minor}.{py.micro}",
            "" if python_ok else "cadora requires Python >= 3.10",
        )
    ]
    checks.append(check_backend("claude"))
    checks.append(check_backend("codex"))
    checks.append(check_backend("kiro", "kiro-cli"))
    checks.append(check_glm())
    checks.append(check_backend("antigravity", "agy"))
    bun = check_backend("bun")
    if not bun.detail:
        bun.detail = "runtime for the aidlc-v2 method pack's hooks (optional otherwise)"
    checks.append(bun)
    return checks


def live_backends_ok(checks: list[BackendCheck]) -> int:
    """How many live backends are usable (ok or merely untested-version)."""
    return sum(
        1
        for c in checks
        if c.backend in _TESTED and c.status in ("ok", "untested")
    )

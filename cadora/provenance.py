"""Workspace provenance + drift detection — make ``--resume-from`` *honest*.

A resumed run skips every node upstream of the resume point and trusts their artifacts already
sitting in ``--cwd``. If that workspace has drifted since the run being resumed — a file edited,
the tree cleaned, a build regenerated — the skipped nodes' outputs are no longer what the resume
assumes. Today that goes undetected: a resumed run will re-run gates over source that never passed
the earlier stages and certify it green. For an audit-grade tool that is exactly the wrong failure
mode.

This module records a content fingerprint of the workspace with every run (provenance), and lets a
resume verify the *current* workspace against the most recent prior run's fingerprint — failing
loudly on drift instead of trusting it silently. It does not yet *restore* drifted state (that is
the heavier "snapshot the bytes into the pack" step); it makes the trust assumption **checked**
rather than silent, which is the cheaper and more on-brand half of the fix.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_NAME = "workspace-manifest.json"

# Directories whose contents are transient / regenerable / not source, so a change inside them is
# not real workspace drift. This is deliberately NOT the integrity scan's exclude set: that one
# also skips ``aidlc-docs``, which is exactly where AI-DLC node artifacts land — the files a resume
# trusts — so they MUST be fingerprinted here. ``__pycache__`` is critical to exclude: ``.pyc``
# files are rewritten on every interpreter run and would otherwise read as perpetual drift.
_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".uv-cache",
    ".gocache",
    ".cadora",
    ".aidlc-rule-details",
    "runs",  # the default archive dir name; the explicit archive_root is excluded separately
}


def fingerprint_workspace(
    cwd: str | Path, *, archive_root: str | Path | None = None
) -> dict[str, str]:
    """Return ``{relpath: sha256}`` for every real file under ``cwd``.

    Excludes VCS/build/cache directories (:data:`_IGNORE_DIRS`), symlinks (which can point outside
    the tree or form loops), and the run archive itself when it lives under ``cwd`` — as it does
    whenever ``--archive-dir`` is a subdirectory of ``--cwd`` — so a run never fingerprints its own
    archive. Deterministic: keyed by POSIX-style relative path.
    """
    root = Path(cwd).resolve()
    archive = Path(archive_root).resolve() if archive_root else None
    out: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        if set(rel.parts) & _IGNORE_DIRS:
            continue
        if archive is not None and (archive == path or archive in path.parents):
            continue
        try:
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def tree_sha256(fingerprint: dict[str, str]) -> str:
    """A single content identity for the whole workspace — hash of the sorted ``path:sha`` lines."""
    joined = "\n".join(f"{p}:{fingerprint[p]}" for p in sorted(fingerprint))
    return hashlib.sha256(joined.encode()).hexdigest()


@dataclass
class WorkspaceDrift:
    """The difference between a recorded workspace fingerprint and the current one."""

    baseline_run: str | None
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.modified)

    @property
    def count(self) -> int:
        return len(self.added) + len(self.removed) + len(self.modified)

    def summary(self) -> str:
        return (
            f"{len(self.modified)} modified, {len(self.removed)} removed, {len(self.added)} added"
        )

    def as_dict(self) -> dict:
        return {
            "baseline_run": self.baseline_run,
            "drift": self.has_drift,
            "modified": self.modified,
            "removed": self.removed,
            "added": self.added,
        }


def diff_fingerprints(
    baseline: dict[str, str], current: dict[str, str], *, baseline_run: str | None = None
) -> WorkspaceDrift:
    """Compare a baseline fingerprint against the current one."""
    added = sorted(current.keys() - baseline.keys())
    removed = sorted(baseline.keys() - current.keys())
    modified = sorted(p for p in baseline.keys() & current.keys() if baseline[p] != current[p])
    return WorkspaceDrift(
        baseline_run=baseline_run, added=added, removed=removed, modified=modified
    )


def write_workspace_manifest(run_dir: str | Path, fingerprint: dict[str, str]) -> Path:
    """Persist a workspace fingerprint into a run's archive dir.

    Provenance first (the pack now records *exactly* what source the run's gates ran over) and the
    resume baseline second. Best-effort at the call site: a failure here must never break a run.
    """
    out = Path(run_dir) / MANIFEST_NAME
    out.write_text(
        json.dumps(
            {
                "file_count": len(fingerprint),
                "tree_sha256": tree_sha256(fingerprint),
                "files": dict(sorted(fingerprint.items())),
            },
            indent=2,
        )
    )
    return out


def read_workspace_fingerprint(run_dir: str | Path) -> dict[str, str] | None:
    """The fingerprint a run recorded for ITS OWN workspace, or ``None`` if it has none.

    This is the correct resume baseline for a run resumed under its own id (a parked run writes
    its fingerprint at park time): it says exactly what source THIS run's earlier stages ran over.
    ``latest_prior_fingerprint`` — which deliberately excludes the current run — is for the other
    case, a fresh run verifying against a *different* prior run.
    """
    mf = Path(run_dir) / MANIFEST_NAME
    if not mf.is_file():
        return None
    try:
        files = json.loads(mf.read_text()).get("files")
    except (OSError, json.JSONDecodeError):
        return None
    return files if isinstance(files, dict) else None


def latest_prior_fingerprint(
    archive_root: str | Path, *, exclude_run_id: str
) -> tuple[str, dict[str, str]] | None:
    """Load the most recent prior run's workspace fingerprint, if any.

    Run ids are timestamped, so lexicographic-descending order is newest-first. Returns
    ``(run_id, fingerprint)`` for the newest run that is not ``exclude_run_id`` and carries a
    workspace manifest, or ``None`` when there is nothing to verify against.
    """
    base = Path(archive_root)
    if not base.is_dir():
        return None
    for d in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        if d.name == exclude_run_id:
            continue
        mf = d / MANIFEST_NAME
        if not mf.is_file():
            continue
        try:
            files = json.loads(mf.read_text()).get("files")
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(files, dict):
            return d.name, files
    return None


def conductor_fingerprint() -> dict:
    """Identify the conductor that produced a run: version + (best-effort) git state.

    The workspace fingerprint above answers "what source did the gates run over?"; this answers
    "which Cadora ran them?". Without it, evidence can't show when the conductor itself changed
    mid-flight — an editable install hot-swaps its own code on a `git checkout`, and the archive
    would never know (observed live: a run whose review module changed underneath it while parked
    at a gate).

    ``git_sha``/``git_dirty`` are populated only when the package runs from a git checkout (the
    editable-install case — exactly the one where drift is possible); on a wheel install both are
    ``None`` and the pinned ``cadora_version`` is the whole story. Best-effort: never raises.
    """
    from cadora import __version__

    info: dict = {"cadora_version": __version__, "git_sha": None, "git_dirty": None}
    repo = Path(__file__).resolve().parent.parent
    if not (repo / ".git").exists():
        return info
    try:
        sha = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if sha.returncode == 0:
            info["git_sha"] = sha.stdout.strip()
            dirty = subprocess.run(
                ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=no", "--", "cadora"],
                capture_output=True, text=True, timeout=5,
            )
            if dirty.returncode == 0:
                info["git_dirty"] = bool(dirty.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return info

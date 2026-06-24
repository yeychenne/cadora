"""Post-step gates — the security / quality checks that run after a node.

Deterministic-first, per Anthropic's own verification ranking (rules-based
checks > visual > LLM-judge). A ``ShellGate`` runs a real command (linter,
tests, secret scan) and BLOCKS the run on non-zero exit. A reviewer-subagent
(LLM-judge) gate is the last resort — left as a stub.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


GATE_PASSED = "passed"
GATE_FAILED = "failed"
GATE_BLOCKED_PREREQUISITE = "blocked_prerequisite"


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str = ""
    status: str = ""
    exit_code: int | None = None
    missing_prerequisites: list[str] = field(default_factory=list)
    setup_detail: str = ""

    def __post_init__(self) -> None:
        if not self.status:
            self.status = GATE_PASSED if self.passed else GATE_FAILED


@dataclass
class ShellGate:
    name: str
    command: str  # e.g. "ruff check . && pytest -q"
    setup_mode: str = "off"
    wheelhouse: str | None = None

    def check(self, cwd: str) -> GateResult:
        if self.setup_mode not in {"off", "auto"}:
            raise ValueError(f"invalid gate setup mode: {self.setup_mode!r}")

        env = os.environ.copy()
        setup_detail = ""
        if self.setup_mode == "auto" and _is_python_gate(self.command):
            prepared = _prepare_python_gate(Path(cwd), self.wheelhouse)
            if prepared is not None:
                env, setup_detail, setup_error = prepared
                if setup_error:
                    return GateResult(
                        name=self.name,
                        passed=False,
                        detail=setup_error,
                        status=GATE_BLOCKED_PREREQUISITE,
                        missing_prerequisites=_missing_prerequisites(setup_error),
                        setup_detail=setup_detail,
                    )

        proc = subprocess.run(
            self.command,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            env=env,
        )
        detail = (proc.stdout + proc.stderr)[-4000:]
        missing = _missing_prerequisites(detail)
        status = (
            GATE_PASSED
            if proc.returncode == 0
            else GATE_BLOCKED_PREREQUISITE
            if missing
            else GATE_FAILED
        )
        return GateResult(
            name=self.name,
            passed=proc.returncode == 0,
            detail=detail,
            status=status,
            exit_code=proc.returncode,
            missing_prerequisites=missing,
            setup_detail=setup_detail,
        )


def _prepare_python_gate(
    cwd: Path, wheelhouse: str | None
) -> tuple[dict[str, str], str, str] | None:
    """Provision a cached isolated Python gate environment when the project declares one."""
    requirements = _dev_requirements(cwd)
    if requirements is None:
        return None

    cadora_dir = cwd / ".cadora"
    venv = cadora_dir / "gate-venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    bin_dir = python.parent
    stamp = cadora_dir / "gate-requirements.sha256"
    fingerprint = _requirements_fingerprint(cwd, requirements, wheelhouse)
    setup_lines = [f"gate environment: {venv}", f"requirements: {requirements.name}"]

    if not python.is_file():
        created = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            error = (created.stdout + created.stderr)[-4000:]
            return os.environ.copy(), "\n".join(setup_lines), error

    if not stamp.is_file() or stamp.read_text() != fingerprint:
        install = [str(python), "-m", "pip", "install"]
        if wheelhouse:
            install.extend(["--no-index", "--find-links", str(Path(wheelhouse).resolve())])
            setup_lines.append(f"wheelhouse: {Path(wheelhouse).resolve()}")
        if (cwd / "pyproject.toml").is_file():
            install.extend(["-e", "."])
        install.extend(["-r", str(requirements)])
        provisioned = subprocess.run(
            install,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        output = (provisioned.stdout + provisioned.stderr)[-4000:]
        setup_lines.append("provision: " + " ".join(install))
        if provisioned.returncode != 0:
            return os.environ.copy(), "\n".join(setup_lines), output
        cadora_dir.mkdir(parents=True, exist_ok=True)
        stamp.write_text(fingerprint)
    else:
        setup_lines.append("provision: cached")

    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env, "\n".join(setup_lines), ""


def _dev_requirements(cwd: Path) -> Path | None:
    for relative in ("requirements-dev.txt", "dev-requirements.txt", "requirements/dev.txt"):
        candidate = cwd / relative
        if candidate.is_file():
            return candidate
    return None


def _is_python_gate(command: str) -> bool:
    return bool(
        re.search(
            r"(?:^|[\s/&|])(?:python(?:3(?:\.\d+)?)?|pytest|py\.test|ruff|tox|nox)"
            r"(?:$|[\s/&|])",
            command,
        )
    )


def _requirements_fingerprint(cwd: Path, requirements: Path, wheelhouse: str | None) -> str:
    digest = hashlib.sha256()
    digest.update(requirements.read_bytes())
    pyproject = cwd / "pyproject.toml"
    if pyproject.is_file():
        digest.update(pyproject.read_bytes())
    digest.update((wheelhouse or "").encode())
    digest.update(sys.version.encode())
    return digest.hexdigest()


_MISSING_PATTERNS = (
    re.compile(r"No module named ['\"](?P<name>[^'\"]+)"),
    re.compile(r"(?:command not found|not found):\s*(?P<name>[\w.-]+)", re.IGNORECASE),
    re.compile(r"(?P<name>[\w.-]+):\s*command not found", re.IGNORECASE),
    re.compile(r"Could not find a version that satisfies the requirement (?P<name>[\w.-]+)"),
    re.compile(r"No matching distribution found for (?P<name>[\w.-]+)"),
)


def _missing_prerequisites(detail: str) -> list[str]:
    missing: set[str] = set()
    if "unrecognized arguments:" in detail and any(
        option in detail for option in ("--cov", "--cov-report", "--cov-fail-under")
    ):
        missing.add("pytest-cov")
    for pattern in _MISSING_PATTERNS:
        for match in pattern.finditer(detail):
            name = match.group("name").split("==", 1)[0]
            missing.add(name.replace("_", "-"))
    return sorted(missing)


# TODO: ReviewerGate — spawn a reviewer subagent (/security-review style) for
# semantic checks the shell can't express. Demoted below the deterministic gates.

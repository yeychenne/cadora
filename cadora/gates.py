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
GATE_VACUOUS = "vacuous"
GATE_PACKAGING = "packaging_failed"


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
                env, setup_detail, setup_error, build_failure = prepared
                if setup_error:
                    return GateResult(
                        name=self.name,
                        passed=False,
                        detail=setup_error,
                        status=GATE_BLOCKED_PREREQUISITE,
                        missing_prerequisites=_missing_prerequisites(setup_error),
                        setup_detail=setup_detail,
                    )
                if build_failure:
                    # The workspace declares an installable package that does NOT build
                    # (setuptools flat-layout auto-discovery refused). Dev tooling was
                    # provisioned so the gate *could* run, but a green here would certify a
                    # package `pip install .` / `python -m build` cannot produce — a false
                    # pass. Fail as a remediable packaging defect instead of running the
                    # command and greening on tests that happened to import from the cwd.
                    return GateResult(
                        name=self.name,
                        passed=False,
                        detail=build_failure,
                        status=GATE_PACKAGING,
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
        missing = _missing_prerequisites(detail, Path(cwd))
        if proc.returncode == 0:
            # Substance, not presence: a gate that invoked a test runner but executed ZERO
            # tests (e.g. `go test` / `cargo test` / `jest --passWithNoTests` all exit 0 with
            # no tests) verified nothing — refuse to pass it.
            if _invokes_test_runner(self.command) and _ran_zero_tests(detail):
                passed, status = False, GATE_VACUOUS
            else:
                passed, status = True, GATE_PASSED
        elif missing:
            passed, status = False, GATE_BLOCKED_PREREQUISITE
        else:
            passed, status = False, GATE_FAILED
        return GateResult(
            name=self.name,
            passed=passed,
            detail=detail,
            status=status,
            exit_code=proc.returncode,
            missing_prerequisites=missing,
            setup_detail=setup_detail,
        )


def _prepare_python_gate(
    cwd: Path, wheelhouse: str | None
) -> tuple[dict[str, str], str, str, str] | None:
    """Provision a cached isolated Python gate environment when the project declares one."""
    cwd = cwd.resolve()  # absolute: these paths are passed to subprocess(cwd=cwd); relative ones double
    requirements = _dev_requirements(cwd)
    if requirements is None:
        return None

    cadora_dir = _gate_env_root(cwd)
    venv = cadora_dir / "gate-venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    bin_dir = python.parent
    stamp = cadora_dir / "gate-requirements.sha256"
    fingerprint = _requirements_fingerprint(cwd, requirements, wheelhouse)
    setup_lines = [f"gate environment: {venv}", f"requirements: {requirements.name}"]
    build_failure = ""

    if not python.is_file():
        created = subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            error = (created.stdout + created.stderr)[-4000:]
            return os.environ.copy(), "\n".join(setup_lines), error, ""

    if not stamp.is_file() or stamp.read_text() != fingerprint:
        base = [str(python), "-m", "pip", "install"]
        index_args: list[str] = []
        if wheelhouse:
            index_args = ["--no-index", "--find-links", str(Path(wheelhouse).resolve())]
            setup_lines.append(f"wheelhouse: {Path(wheelhouse).resolve()}")
        editable = _project_is_installable(cwd)
        install = base + index_args + (["-e", "."] if editable else []) + ["-r", str(requirements)]
        provisioned = subprocess.run(install, cwd=cwd, capture_output=True, text=True)
        output = (provisioned.stdout + provisioned.stderr)[-4000:]
        setup_lines.append("provision: " + " ".join(install))
        if provisioned.returncode != 0 and editable:
            # The tree declares an installable package but `pip install -e .` failed. Retry
            # with the dev requirements alone so the tooling still lands and the gate can run.
            # But distinguish WHY it failed: the setuptools flat-layout auto-discovery panic
            # (several top-level packages, no explicit `packages` config) means the package
            # genuinely does NOT build — record it as a remediable *packaging defect* so the
            # gate FAILS (and --remediate fixes the pyproject) instead of false-greening the
            # moment the tooling-only fallback lets tests that import from cwd pass.
            if _is_flat_layout_failure(output):
                build_failure = _PACKAGING_HINT + "\n\n--- pip install -e . ---\n" + output
            setup_lines.append("editable install failed; retrying with requirements only")
            install = base + index_args + ["-r", str(requirements)]
            provisioned = subprocess.run(install, cwd=cwd, capture_output=True, text=True)
            output = (output + "\n--- fallback ---\n" + provisioned.stdout + provisioned.stderr)[-4000:]
            setup_lines.append("provision: " + " ".join(install))
        if provisioned.returncode != 0:
            return os.environ.copy(), "\n".join(setup_lines), output, ""
        if not build_failure:
            # Cache only a HEALTHY provision. On a packaging defect, skip the stamp so every
            # run re-derives the failure (until the pyproject is fixed and its fingerprint
            # changes) — a cached stamp must never resurrect the false-green.
            cadora_dir.mkdir(parents=True, exist_ok=True)
            stamp.write_text(fingerprint)
    else:
        setup_lines.append("provision: cached")

    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    return env, "\n".join(setup_lines), "", build_failure


def _dev_requirements(cwd: Path) -> Path | None:
    for relative in ("requirements-dev.txt", "dev-requirements.txt", "requirements/dev.txt"):
        candidate = cwd / relative
        if candidate.is_file():
            return candidate
    return None


def _gate_env_root(cwd: Path) -> Path:
    """Directory holding the cached gate virtualenv — deliberately OUTSIDE the workspace.

    If the venv lived inside ``cwd`` (e.g. ``cwd/.cadora/gate-venv``), any gate that globs
    the tree — ``ruff check .``, ``mypy .``, ``coverage`` — would scan Cadora's own
    provisioned third-party code and false-fail on it. Keyed by the resolved workspace path
    so the cache stays stable across runs of the same workspace. Override the base location
    with ``$CADORA_GATE_CACHE``.
    """
    key = hashlib.sha256(str(cwd).encode()).hexdigest()[:16]
    override = os.environ.get("CADORA_GATE_CACHE")
    base = Path(override) if override else Path.home() / ".cache" / "cadora" / "gate-venvs"
    return base / key


def _project_is_installable(cwd: Path) -> bool:
    """True when the workspace declares an installable Python package.

    A ``pyproject.toml`` that only carries ``[tool.*]`` config (very common — agents
    write one just for pytest/ruff settings) is NOT installable: ``pip install -e .``
    would trigger setuptools flat-layout auto-discovery and abort on a multi-package
    tree, taking the whole gate environment down with it. Only attempt the editable
    install when there's a real build declaration (``[build-system]``/``[project]``,
    or a setup.py/setup.cfg).
    """
    if (cwd / "setup.py").is_file() or (cwd / "setup.cfg").is_file():
        return True
    pyproject = cwd / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"(?m)^[ \t]*\[(?:build-system|project)\b", text))


# The setuptools flat-layout auto-discovery panic — "Multiple top-level packages (or modules)
# discovered in a flat-layout: [...]". A declared package that trips this genuinely does not build.
_FLAT_LAYOUT_RE = re.compile(r"discovered in a flat-layout", re.IGNORECASE)

_PACKAGING_HINT = (
    "packaging: `pip install -e .` failed — setuptools flat-layout auto-discovery found "
    "multiple top-level packages and refused to guess which to ship. The project declares an "
    "installable package but does not build (`pip install .` / `python -m build` fail the same "
    "way). Declare the packages explicitly — e.g. `[tool.setuptools.packages.find]` (with "
    "`where`/`include`/`exclude`), `[tool.setuptools] packages`/`py-modules`, or move the code "
    "under `src/`. Dev tooling was still provisioned so the gate could run; this is a packaging "
    "defect, not a passing build."
)


def _is_flat_layout_failure(output: str) -> bool:
    """True when a ``pip install -e .`` failure is the setuptools flat-layout auto-discovery panic."""
    return bool(_FLAT_LAYOUT_RE.search(output))


def _is_python_gate(command: str) -> bool:
    return bool(
        re.search(
            r"(?:^|[\s/&|])(?:python(?:3(?:\.\d+)?)?|pytest|py\.test|ruff|tox|nox)"
            r"(?:$|[\s/&|])",
            command,
        )
    )


_TEST_RUNNER = re.compile(
    r"(?:^|[\s/&|=])(?:pytest|py\.test|jest|vitest|mocha|"
    r"go\s+test|cargo\s+test|swift\s+test|deno\s+test|rspec|"
    r"npm\s+(?:run\s+)?test|yarn\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test)(?:$|[\s/&|])",
    re.IGNORECASE,
)
# At least one test actually executed — guards the vacuous check against false positives
# (e.g. a multi-package `go test ./...` where only some packages carry tests).
_TESTS_RAN = re.compile(
    r"\b[1-9]\d*\s+(?:passed|passing|failed)\b"
    r"|^ok\s+\S+\s"
    r"|^---\s+(?:PASS|FAIL)\b"
    r"|^test\s+.+\.\.\.\s+ok\b",
    re.IGNORECASE | re.MULTILINE,
)
# Explicit "no tests executed" signals across runners.
_NO_TESTS = re.compile(
    r"no tests ran"
    r"|collected 0 items"
    r"|\bno test files\b"
    r"|running 0 tests"
    r"|no tests found"
    r"|tests:\s+0 total"
    r"|\b0 passing\b"
    r"|executed 0 of 0",
    re.IGNORECASE,
)


def _invokes_test_runner(command: str) -> bool:
    return bool(_TEST_RUNNER.search(command))


def _ran_zero_tests(detail: str) -> bool:
    """True when a passing test gate executed no tests at all (a vacuous pass)."""
    if _TESTS_RAN.search(detail):
        return False
    return bool(_NO_TESTS.search(detail))


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
    re.compile(r"Cannot find module ['\"](?P<name>[^'\"]+)"),  # node
    re.compile(r"no required module provides package (?P<name>[^\s;:]+)"),  # go modules
    re.compile(r"can't find crate for `(?P<name>[\w-]+)`"),  # rust
)


def _missing_prerequisites(detail: str, cwd: Path | None = None) -> list[str]:
    missing: set[str] = set()
    if "unrecognized arguments:" in detail and any(
        option in detail for option in ("--cov", "--cov-report", "--cov-fail-under")
    ):
        missing.add("pytest-cov")
    for pattern in _MISSING_PATTERNS:
        for match in pattern.finditer(detail):
            name = match.group("name").split("==", 1)[0]
            missing.add(name.replace("_", "-"))
    if cwd is not None:
        # An unimportable package that actually lives in the workspace is a fixable
        # packaging/config bug (e.g. no `pythonpath`/install so `import pkg` fails under the
        # bare `pytest` console script) — a *remediable* gate failure, not a terminal
        # missing external prerequisite. Only genuinely-external names stay prerequisites.
        missing = {name for name in missing if not _is_local_module(cwd, name)}
    return sorted(missing)


def _is_local_module(cwd: Path, name: str) -> bool:
    """True when ``name`` names a top-level package/module living in the workspace."""
    top = name.replace("-", "_").split(".")[0]
    if not top:
        return False
    return (
        (cwd / top).is_dir()
        or (cwd / f"{top}.py").is_file()
        or (cwd / "src" / top).is_dir()
    )


# TODO: ReviewerGate — spawn a reviewer subagent (/security-review style) for
# semantic checks the shell can't express. Demoted below the deterministic gates.

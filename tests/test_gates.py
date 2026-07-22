"""Deterministic shell-gate classification and prerequisite setup tests."""

import shlex
import subprocess
import sys

from cadora.gates import (
    GATE_BLOCKED_PREREQUISITE,
    GATE_FAILED,
    GATE_PACKAGING,
    GATE_PASSED,
    GATE_VACUOUS,
    ShellGate,
    _is_flat_layout_failure,
)


def test_missing_pytest_cov_is_a_prerequisite_block(tmp_path):
    python = shlex.quote(sys.executable)
    command = (
        f"{python} -c \"import sys; "
        "sys.stderr.write('error: unrecognized arguments: --cov=src --cov-fail-under=95'); "
        "sys.exit(4)\""
    )
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.passed is False
    assert result.status == GATE_BLOCKED_PREREQUISITE
    assert result.missing_prerequisites == ["pytest-cov"]
    assert result.exit_code == 4


def test_executed_test_failure_is_not_a_prerequisite_block(tmp_path):
    python = shlex.quote(sys.executable)
    result = ShellGate("test", f"{python} -c \"raise AssertionError('boom')\"").check(str(tmp_path))

    assert result.passed is False
    assert result.status == GATE_FAILED
    assert result.missing_prerequisites == []


def test_auto_setup_uses_cached_isolated_python_environment(tmp_path, monkeypatch):
    cache = tmp_path / "gate-cache"
    monkeypatch.setenv("CADORA_GATE_CACHE", str(cache))
    (tmp_path / "requirements-dev.txt").write_text("")
    command = (
        "python -c \"import os, pathlib; "
        "assert pathlib.Path(os.environ['VIRTUAL_ENV']).name == 'gate-venv'\""
    )
    gate = ShellGate("test", command, setup_mode="auto")

    first = gate.check(str(tmp_path))
    second = gate.check(str(tmp_path))

    assert first.status == GATE_PASSED
    # The gate venv lives OUTSIDE the workspace so `.`-globbing gates never scan it.
    assert not (tmp_path / ".cadora").exists()
    assert any(cache.glob("*/gate-venv"))
    assert second.status == GATE_PASSED
    assert "provision: cached" in second.setup_detail


def test_auto_setup_handles_relative_cwd(tmp_path, monkeypatch):
    # Regression: a relative cwd must not double the provisioning paths —
    # `pip install -r <cwd>/requirements-dev.txt` must resolve against the absolute cwd.
    cache = tmp_path / "gate-cache"
    monkeypatch.setenv("CADORA_GATE_CACHE", str(cache))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "requirements-dev.txt").write_text("")
    command = (
        "python -c \"import os, pathlib; "
        "assert pathlib.Path(os.environ['VIRTUAL_ENV']).name == 'gate-venv'\""
    )
    monkeypatch.chdir(tmp_path)

    result = ShellGate("test", command, setup_mode="auto").check("ws")  # RELATIVE cwd — the repro

    assert result.status == GATE_PASSED
    assert "Could not open requirements file" not in result.detail
    assert any(cache.glob("*/gate-venv"))  # provisioned (outside the workspace)
    assert not (ws / "ws").exists()  # no doubled path


def test_tool_only_pyproject_skips_editable_install(tmp_path, monkeypatch):
    # A pyproject carrying only [tool.*] config (agents write one just for pytest/ruff) is NOT
    # an installable package: `pip install -e .` would trip setuptools flat-layout discovery on
    # a multi-package tree and abort the WHOLE provision. Cadora must skip the editable install
    # and still land the tooling.
    monkeypatch.setenv("CADORA_GATE_CACHE", str(tmp_path / "gate-cache"))
    (tmp_path / "requirements-dev.txt").write_text("")
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    for pkg in ("pkg_a", "pkg_b"):  # two top-level packages — the flat-layout landmine
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "__init__.py").write_text("")

    result = ShellGate("test", "python -c \"print('ok')\"", setup_mode="auto").check(str(tmp_path))

    assert result.status == GATE_PASSED
    assert "-e ." not in result.setup_detail  # editable install was not attempted


def test_local_package_import_error_is_remediable_not_terminal(tmp_path):
    # An unimportable package that lives in the workspace is a fixable packaging/config bug
    # (remediable GATE_FAILED), not a terminal missing external prerequisite.
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    python = shlex.quote(sys.executable)
    command = (
        f"{python} -c \"import sys; "
        "sys.stderr.write('No module named ' + chr(39) + 'mypkg' + chr(39)); sys.exit(1)\""
    )
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.status == GATE_FAILED
    assert result.missing_prerequisites == []


def test_external_missing_module_stays_a_prerequisite_block(tmp_path):
    # A missing EXTERNAL dependency (no such package in the workspace) stays terminal —
    # remediation shouldn't burn attempts trying to author a third-party library.
    python = shlex.quote(sys.executable)
    command = (
        f"{python} -c \"import sys; "
        "sys.stderr.write('No module named ' + chr(39) + 'boto3' + chr(39)); sys.exit(1)\""
    )
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.status == GATE_BLOCKED_PREREQUISITE
    assert result.missing_prerequisites == ["boto3"]


def test_auto_setup_failure_is_reported_without_running_gate(tmp_path):
    (tmp_path / "requirements-dev.txt").write_text("definitely-missing-cadora-package==0\n")
    gate = ShellGate(
        "test",
        "python -c \"from pathlib import Path; Path('gate-ran').touch()\"",
        setup_mode="auto",
        wheelhouse=str(tmp_path / "empty-wheelhouse"),
    )

    result = gate.check(str(tmp_path))

    assert result.status == GATE_BLOCKED_PREREQUISITE
    assert result.missing_prerequisites == ["definitely-missing-cadora-package"]
    assert not (tmp_path / "gate-ran").exists()


def test_auto_setup_does_not_provision_for_non_python_gate(tmp_path):
    (tmp_path / "requirements-dev.txt").write_text("definitely-missing-cadora-package==0\n")

    result = ShellGate("build", "true", setup_mode="auto").check(str(tmp_path))

    assert result.status == GATE_PASSED
    assert not (tmp_path / ".cadora").exists()


def test_test_runner_that_ran_zero_tests_is_vacuous(tmp_path):
    # `cargo test` exits 0 even when it runs no tests — that verified nothing, so it must not pass.
    command = "cargo() { echo 'running 0 tests'; }; cargo test"
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.passed is False
    assert result.status == GATE_VACUOUS
    assert result.exit_code == 0


def test_swift_test_that_printed_no_summary_is_vacuous(tmp_path):
    """`swift test` over a target with no tests exits 0 having printed only "Build complete!".

    There is no "no tests" string to match, so silence is the only signal there is — and this
    exact output was certified `passed` before the silent-runner rule. Captured from a real
    Swift 6.3 run, not invented.
    """
    command = "swift() { echo 'Building for debugging...'; echo 'Build complete! (0.42s)'; }; swift test"
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.passed is False
    assert result.status == GATE_VACUOUS
    assert result.exit_code == 0


def test_swift_test_with_real_tests_passes(tmp_path):
    """The other side of the silent-runner rule: a genuine run must not be called vacuous.

    Both Swift test frameworks' summary lines, so the strictness cannot misfire on real work.
    """
    xctest = (
        "swift() { echo \"Test Suite 'All tests' passed\"; "
        "echo 'Executed 3 tests, with 0 failures (0 unexpected) in 0.004 seconds'; }; swift test"
    )
    assert ShellGate("test", xctest).check(str(tmp_path)).status == GATE_PASSED

    swift_testing = (
        "swift() { echo 'Test run with 3 tests passed after 0.021 seconds.'; }; swift test"
    )
    assert ShellGate("test", swift_testing).check(str(tmp_path)).status == GATE_PASSED


def test_swift_zero_test_summaries_are_vacuous(tmp_path):
    """When either framework DOES print a zero summary, it is caught explicitly too."""
    for output in ("Executed 0 tests, with 0 failures", "Test run with 0 tests passed"):
        command = f"swift() {{ echo '{output}'; }}; swift test"
        assert ShellGate("test", command).check(str(tmp_path)).status == GATE_VACUOUS


def test_silent_runner_rule_does_not_leak_to_other_runners(tmp_path):
    """pytest and friends print an explicit zero signal, so silence must stay benign for them —
    a quiet non-test command must never be reclassified as a vacuous test run."""
    quiet_pytest = "pytest() { echo 'some unrelated chatter'; }; pytest -q"
    assert ShellGate("test", quiet_pytest).check(str(tmp_path)).status == GATE_PASSED


def test_go_with_no_test_files_is_vacuous(tmp_path):
    command = "go() { echo '? ./x [no test files]'; }; go test ./..."
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.status == GATE_VACUOUS


def test_real_passing_tests_are_not_vacuous(tmp_path):
    command = "cargo() { echo 'test result: ok. 3 passed; 0 failed'; }; cargo test"
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.passed is True
    assert result.status == GATE_PASSED


def test_some_packages_with_tests_are_not_vacuous(tmp_path):
    # Mixed `go test ./...`: one package ran tests, another had none — overall NOT vacuous.
    command = "go() { echo 'ok ./a 0.4s'; echo '? ./b [no test files]'; }; go test ./..."
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.passed is True
    assert result.status == GATE_PASSED


def test_non_test_gate_is_exempt_from_the_vacuous_check(tmp_path):
    # A lint/build gate doesn't invoke a test runner, so its output never triggers the check.
    result = ShellGate("lint", "echo 'no test files'").check(str(tmp_path))

    assert result.passed is True
    assert result.status == GATE_PASSED


def test_node_missing_module_is_a_prerequisite_block(tmp_path):
    command = "echo \"Error: Cannot find module 'jest'\" 1>&2 ; exit 1"
    result = ShellGate("test", command).check(str(tmp_path))

    assert result.status == GATE_BLOCKED_PREREQUISITE
    assert "jest" in result.missing_prerequisites


def test_flat_layout_failure_matcher():
    err = "error: Multiple top-level packages discovered in a flat-layout: ['pkg_a', 'pkg_b']."
    assert _is_flat_layout_failure(err) is True
    assert _is_flat_layout_failure("Multiple top-level modules discovered in a flat-layout: ['a']")
    # unrelated install failures must NOT be misclassified as a packaging defect
    assert _is_flat_layout_failure("ERROR: Could not find a version that satisfies boto3") is False
    assert _is_flat_layout_failure("SyntaxError: invalid syntax") is False


def test_declared_package_that_fails_to_build_is_packaging_failure_not_greened(tmp_path, monkeypatch):
    # A pyproject that DECLARES an installable package ([build-system]+[project]) in a flat layout
    # with several top-level packages and no explicit `packages` config: `pip install -e .` panics
    # on setuptools auto-discovery. The tooling-only fallback must NOT let the gate green on tests
    # that import from cwd — the package does not build, so this is a remediable packaging failure.
    monkeypatch.setenv("CADORA_GATE_CACHE", str(tmp_path / "gate-cache"))
    (tmp_path / "requirements-dev.txt").write_text("")
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        '[project]\nname = "app"\nversion = "0.1.0"\n'
    )
    for pkg in ("pkg_a", "pkg_b"):  # multiple top-level packages — the flat-layout landmine
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "__init__.py").write_text("")

    flat_layout_err = "error: Multiple top-level packages discovered in a flat-layout: ['pkg_a', 'pkg_b']."
    ran_gate_command = {"value": False}

    def fake_run(cmd, *args, **kwargs):
        parts = cmd if isinstance(cmd, list) else shlex.split(cmd)
        if "venv" in parts:  # `python -m venv <path>` — pretend it was created
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "-e" in parts:  # `pip install -e . -r ...` — the editable install that panics
            return subprocess.CompletedProcess(cmd, 1, "", flat_layout_err)
        if "pip" in parts and "install" in parts:  # fallback: tooling-only install succeeds
            return subprocess.CompletedProcess(cmd, 0, "installed tooling", "")
        ran_gate_command["value"] = True  # the gate command itself (should NOT run)
        return subprocess.CompletedProcess(cmd, 0, "1 passed", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ShellGate("test", "pytest -q", setup_mode="auto").check(str(tmp_path))

    assert result.passed is False
    assert result.status == GATE_PACKAGING
    assert "flat-layout" in result.detail
    assert "packages.find" in result.detail  # the actionable fix hint reaches remediation
    assert ran_gate_command["value"] is False  # failed fast; never greened on the cwd-imported tests
    # cache not poisoned: no stamp written, so a re-run re-derives the failure until pyproject is fixed
    assert not any((tmp_path / "gate-cache").glob("*/gate-requirements.sha256"))

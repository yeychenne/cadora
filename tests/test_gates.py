"""Deterministic shell-gate classification and prerequisite setup tests."""

import shlex
import sys

from cadora.gates import (
    GATE_BLOCKED_PREREQUISITE,
    GATE_FAILED,
    GATE_PASSED,
    ShellGate,
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


def test_auto_setup_uses_cached_isolated_python_environment(tmp_path):
    (tmp_path / "requirements-dev.txt").write_text("")
    command = (
        "python -c \"import os, pathlib; "
        "assert pathlib.Path(os.environ['VIRTUAL_ENV']).name == 'gate-venv'\""
    )
    gate = ShellGate("test", command, setup_mode="auto")

    first = gate.check(str(tmp_path))
    second = gate.check(str(tmp_path))

    assert first.status == GATE_PASSED
    assert ".cadora/gate-venv" in first.setup_detail
    assert second.status == GATE_PASSED
    assert "provision: cached" in second.setup_detail


def test_auto_setup_handles_relative_cwd(tmp_path, monkeypatch):
    # Regression: a relative cwd must not double the provisioning paths — the gate-venv was created
    # at <cwd>/<cwd>/.cadora and `pip install -r <cwd>/requirements-dev.txt` could not be opened.
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
    assert (ws / ".cadora" / "gate-venv").is_dir()  # created at the correct, non-doubled path
    assert not (ws / "ws").exists()  # no doubled path


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

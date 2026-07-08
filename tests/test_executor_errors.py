"""Executor failure reporting — exit code / timeout / stderr tail surface in the reason."""

from cadora.executors.base import ExecutionResult
from cadora.runner import _executor_error_detail, _failure_reason
from cadora.topology import Node


def _failed(exit_code=1, meta=None):
    return ExecutionResult(node_id="n", ok=False, exit_code=exit_code, meta=meta or {})


def test_detail_includes_exit_and_stderr_tail():
    detail = _executor_error_detail(_failed(1, {"stderr_tail": "boom\nAuthError: token expired"}))
    assert "exit 1" in detail
    assert "AuthError: token expired" in detail  # last stderr line surfaced


def test_detail_prefers_timeout_over_exit_sentinel():
    detail = _executor_error_detail(_failed(124, {"timed_out": True, "timeout_seconds": 600}))
    assert "timed out after 600s" in detail
    assert "exit 124" not in detail  # 124 is just the timeout sentinel — don't show it


def test_failure_reason_enriches_bare_executor_failed():
    node = Node(id="build", phase="construction")
    reason = _failure_reason(
        node, _failed(1, {"stderr_tail": "kiro: not authenticated"}),
        gate=None, integrity_blocked=False, repair_failed=False,
    )
    assert reason.startswith("executor failed")
    assert "exit 1" in reason
    assert "kiro: not authenticated" in reason

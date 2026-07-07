"""Wave concurrency: independent same-wave nodes run in parallel; DAG order still holds."""

import json
import threading
import time

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class SleepExecutor(NodeExecutor):
    """Records each node's execution span so tests can detect real overlap."""

    name = "sleep"

    def __init__(self, delay=0.3):
        self.delay = delay
        self.spans: list[tuple[str, float, float]] = []
        self._lock = threading.Lock()

    def run(self, node, prompt, *, cwd, env=None):
        start = time.monotonic()
        time.sleep(self.delay)
        end = time.monotonic()
        with self._lock:
            self.spans.append((node.id, start, end))
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=f"out-{node.id}",
            cost_usd=0.0,
            meta={"funding_resolved": "subscription"},
        )

    def any_overlap(self) -> bool:
        for i in range(len(self.spans)):
            for j in range(i + 1, len(self.spans)):
                _, s1, e1 = self.spans[i]
                _, s2, e2 = self.spans[j]
                if s1 < e2 and s2 < e1:
                    return True
        return False


def _two_independent() -> Topology:
    # No depends_on between a and b -> both land in the same wave.
    return Topology(name="par", nodes=[Node(id="a", prompt="A"), Node(id="b", prompt="B")])


def test_independent_wave_nodes_run_concurrently(tmp_path):
    ex = SleepExecutor(delay=0.3)
    run_topology(
        _two_independent(), ex, run_id="par", cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"), max_parallel=2,
    )
    assert ex.any_overlap(), "independent same-wave nodes did not overlap under --max-parallel 2"


def test_max_parallel_one_stays_sequential(tmp_path):
    ex = SleepExecutor(delay=0.15)
    run_topology(
        _two_independent(), ex, run_id="seq", cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"), max_parallel=1,
    )
    assert not ex.any_overlap(), "default (max_parallel=1) run must stay sequential"


def test_dependent_nodes_never_overlap_even_when_parallel(tmp_path):
    # a -> b are in different waves; concurrency must not violate the dependency.
    ex = SleepExecutor(delay=0.2)
    topo = Topology(
        name="chain",
        nodes=[Node(id="a", prompt="A"), Node(id="b", prompt="B", depends_on=["a"])],
    )
    out = run_topology(
        topo, ex, run_id="chain", cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"), max_parallel=4,
    )
    assert not ex.any_overlap()
    manifest = json.loads((out / "manifest.json").read_text())
    assert [n["node_id"] for n in manifest["nodes"]] == ["a", "b"]


def test_concurrency_preserves_declared_order_and_outputs(tmp_path):
    ex = SleepExecutor(delay=0.05)
    out = run_topology(
        _two_independent(), ex, run_id="ord", cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"), max_parallel=4,
    )
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["ok"] is True
    # Bookkeeping stays in declared order regardless of completion order.
    assert [n["node_id"] for n in manifest["nodes"]] == ["a", "b"]


def test_review_node_in_concurrent_wave_scopes_docs(tmp_path):
    # Integration of wave concurrency + HITL: a review node whose initial execution ran
    # concurrently must still receive its scoped documents (the pre-review snapshot is taken
    # in the concurrent path). Guards against the merge dropping pre_review_docs.
    from cadora.review import REVIEW_APPROVE, ReviewResult

    ex = SleepExecutor(delay=0.05)
    topo = Topology(
        name="par-review",
        nodes=[Node(id="a", prompt="A", review=True), Node(id="b", prompt="B", review=True)],
    )
    seen: list[tuple[str, object]] = []

    def review_fn(node, cwd, documents):  # 3-arg: must receive the scoped documents list
        seen.append((node.id, documents))
        return ReviewResult(REVIEW_APPROVE)

    out = run_topology(
        topo, ex, run_id="parrev", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
        hitl=True, review_fn=review_fn, max_parallel=2,
    )
    assert sorted(nid for nid, _ in seen) == ["a", "b"]  # both reviewed, no crash
    assert all(isinstance(docs, list) for _, docs in seen)  # scoped docs flowed through concurrently
    assert json.loads((out / "manifest.json").read_text())["ok"] is True

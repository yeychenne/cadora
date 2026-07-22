"""Reviewer identity — the evidence pack's weakest link, closed honestly.

Before this, every review surface was anonymous: the pack could say "something wrote approve",
never WHO. The decisions in force (2026-07-22): identity is **honestly self-asserted** (labelled
by surface, no fake authentication); with no allowlist **anyone may decide and the identity is
recorded**; formats are **strictly additive** — packs written before these fields verify
unchanged.
"""

import json
import threading
import time


from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.review import (
    REVIEW_APPROVE,
    REVIEW_REQUEST_CHANGES,
    ReviewResult,
    file_review_fn,
    write_review_decision,
)
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class Ex(NodeExecutor):
    name = "fake"

    def __init__(self):
        self.calls: list[str] = []

    def run(self, node, prompt, *, cwd, env=None):
        self.calls.append(node.id)
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0, text=f"out-{node.id}", cost_usd=1.0
        )


def _scripted(*results):
    queue = list(results)

    def review_fn(node, node_cwd, documents=None):
        return queue.pop(0)

    return review_fn


def _run(tmp_path, executor, review_fn, **kwargs):
    return run_topology(
        Topology(name="t", nodes=[Node(id="a", role="builder", prompt="p", review=True)]),
        executor,
        run_id="r",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=review_fn,
        **kwargs,
    )


def _reviews(tmp_path):
    manifest = json.loads((tmp_path / "runs" / "r" / "manifest.json").read_text())
    return manifest["nodes"][0]["human_reviews"], manifest


# --- identity lands in the evidence ---------------------------------------------------------


def test_identity_method_and_doc_shas_reach_the_manifest(tmp_path):
    class WritingEx(Ex):
        # The reviewed documents are what THIS STAGE produced — so the node must write it.
        def run(self, node, prompt, *, cwd, env=None):
            docs = tmp_path / "aidlc-docs"
            docs.mkdir(exist_ok=True)
            (docs / "design.md").write_text("# the design\n")
            return super().run(node, prompt, cwd=cwd, env=env)

    executor = WritingEx()
    _run(
        tmp_path,
        executor,
        _scripted(ReviewResult(REVIEW_APPROVE, "lgtm", reviewer="yves", method="local-shell")),
    )
    reviews, manifest = _reviews(tmp_path)
    assert reviews[0]["reviewer"] == "yves"
    assert reviews[0]["method"] == "local-shell"
    assert reviews[0]["timestamp"]
    # What the reviewer saw, hashed at decision time.
    (doc,) = reviews[0]["documents"]
    assert doc["path"] == "aidlc-docs/design.md"
    assert len(doc["sha256"]) == 64
    assert "review_policy" not in manifest  # no allowlist declared -> no policy claimed


def test_default_reviewer_backfills_an_anonymous_surface(tmp_path):
    executor = Ex()
    _run(
        tmp_path,
        executor,
        _scripted(ReviewResult(REVIEW_APPROVE, "ok")),  # surface carried no identity
        default_reviewer="yves",
    )
    reviews, _ = _reviews(tmp_path)
    assert reviews[0]["reviewer"] == "yves"


def test_anonymous_decision_without_allowlist_is_recorded_as_unattributed(tmp_path):
    """The 'anyone, identity recorded' default: old flows keep working, honestly null."""
    executor = Ex()
    _run(tmp_path, executor, _scripted(ReviewResult(REVIEW_APPROVE, "ok")))
    reviews, _ = _reviews(tmp_path)
    assert reviews[0]["reviewer"] is None


def test_file_surface_carries_identity_from_the_decision_file(tmp_path):
    executor = Ex()
    review_fn = file_review_fn(timeout=30, interval=0.02)

    def decide():
        request = tmp_path / "cadora-review-request.json"
        for _ in range(1500):
            if request.is_file():
                break
            time.sleep(0.01)
        write_review_decision(tmp_path, REVIEW_APPROVE, "ship", reviewer="alice", method="dashboard")

    thread = threading.Thread(target=decide, daemon=True)
    thread.start()
    _run(tmp_path, executor, review_fn)
    thread.join(timeout=30)

    reviews, _ = _reviews(tmp_path)
    assert reviews[0]["reviewer"] == "alice"
    assert reviews[0]["method"] == "dashboard"


def test_hand_dropped_decision_file_is_honestly_file_drop(tmp_path):
    executor = Ex()
    review_fn = file_review_fn(timeout=30, interval=0.02)

    def decide():
        request = tmp_path / "cadora-review-request.json"
        for _ in range(1500):
            if request.is_file():
                break
            time.sleep(0.01)
        (tmp_path / "cadora-review-decision.json").write_text(
            json.dumps({"decision": "approve", "comments": "by hand"})
        )

    thread = threading.Thread(target=decide, daemon=True)
    thread.start()
    _run(tmp_path, executor, review_fn)
    thread.join(timeout=30)

    reviews, _ = _reviews(tmp_path)
    assert reviews[0]["reviewer"] is None  # nobody claimed it
    assert reviews[0]["method"] == "file-drop"  # and the pack says exactly that


# --- the allowlist is enforced at decision time -----------------------------------------------


def test_unauthorized_decision_is_rejected_and_the_gate_reasks(tmp_path):
    executor = Ex()
    _run(
        tmp_path,
        executor,
        _scripted(
            ReviewResult(REVIEW_APPROVE, "let me in", reviewer="mallory", method="mcp"),
            ReviewResult(REVIEW_APPROVE, "ok", reviewer="alice", method="mcp"),
        ),
        reviewers=["alice"],
    )
    assert executor.calls == ["a"]  # the agent never re-ran between decisions
    reviews, manifest = _reviews(tmp_path)
    assert [r["reviewer"] for r in reviews] == ["alice"]  # mallory's never entered the record
    assert manifest["review_policy"] == {"reviewers": ["alice"]}  # the policy in force, recorded
    events = (tmp_path / "runs" / "r" / "run-events.jsonl").read_text()
    assert "review_rejected" in events and "mallory" in events  # …but the attempt is in the log


def test_unauthorized_abort_cannot_kill_the_run(tmp_path):
    """The dangerous case: abort is also a decision. Unlisted identities cannot use it."""
    executor = Ex()
    _run(
        tmp_path,
        executor,
        _scripted(
            ReviewResult("abort", "die", reviewer="mallory", method="file-drop"),
            ReviewResult(REVIEW_APPROVE, "ok", reviewer="alice", method="local-shell"),
        ),
        reviewers=["alice"],
    )
    _, manifest = _reviews(tmp_path)
    assert manifest["ok"] is True  # the run survived mallory's abort


def test_anonymous_decision_is_rejected_when_a_policy_is_declared(tmp_path):
    executor = Ex()
    _run(
        tmp_path,
        executor,
        _scripted(
            ReviewResult(REVIEW_APPROVE, "ok"),  # no identity
            ReviewResult(REVIEW_APPROVE, "ok", reviewer="alice"),
        ),
        reviewers=["alice"],
        default_reviewer=None,
    )
    reviews, _ = _reviews(tmp_path)
    assert [r["reviewer"] for r in reviews] == ["alice"]


def test_rejection_does_not_consume_a_revision(tmp_path):
    """Three rejected impostors then a real request_changes: the revision budget is intact."""
    executor = Ex()
    impostor = ReviewResult(REVIEW_REQUEST_CHANGES, "sabotage", reviewer="mallory")
    _run(
        tmp_path,
        executor,
        _scripted(
            impostor, impostor, impostor,
            ReviewResult(REVIEW_REQUEST_CHANGES, "real change", reviewer="alice"),
            ReviewResult(REVIEW_APPROVE, "ok", reviewer="alice"),
        ),
        reviewers=["alice"],
    )
    assert executor.calls == ["a", "a"]  # exactly one revision re-run, not four
    _, manifest = _reviews(tmp_path)
    assert manifest["ok"] is True


# --- additivity ----------------------------------------------------------------------------


def test_old_style_review_results_still_flow_end_to_end(tmp_path):
    """Strictly additive: a ReviewResult built the old way (no new fields) works unchanged."""
    executor = Ex()
    _run(tmp_path, executor, _scripted(ReviewResult(REVIEW_APPROVE)))
    reviews, manifest = _reviews(tmp_path)
    assert manifest["ok"] is True
    assert reviews[0]["reviewer"] is None and reviews[0]["method"] is None


def test_identity_survives_a_park_round_trip(tmp_path):
    """PR-1 integration: identity attaches to the durable park record, not a live process."""
    from tests.test_park_and_exit import RecordingExecutor, _park, _resume

    parker = RecordingExecutor()
    _park(tmp_path, parker)
    resumer = RecordingExecutor()
    _resume(
        tmp_path,
        resumer,
        _scripted(ReviewResult(REVIEW_APPROVE, "ok", reviewer="yves", method="local-shell")),
    )
    manifest = json.loads((tmp_path / "runs" / "r" / "manifest.json").read_text())
    b = next(n for n in manifest["nodes"] if n["node_id"] == "b")
    assert b["human_reviews"][0]["reviewer"] == "yves"


def test_cli_wires_reviewer_and_reviewers(tmp_path, monkeypatch):
    import cadora.cli as cli

    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: a\n    prompt: hi\n    review: true\n")
    executor = Ex()
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: executor)

    def decide():
        request = tmp_path / "cadora-review-request.json"
        for _ in range(1500):
            if request.is_file():
                break
            time.sleep(0.01)
        write_review_decision(tmp_path, REVIEW_APPROVE, "ok", reviewer="alice")

    thread = threading.Thread(target=decide, daemon=True)
    thread.start()
    rc = cli.main(
        [
            "run", str(topo),
            "--cwd", str(tmp_path),
            "--archive-dir", str(tmp_path / "runs"),
            "--run-id", "r",
            "--hitl", "--review-file", "--review-timeout", "30",
            "--reviewers", "alice,bob",
            "--yes",
        ]
    )
    thread.join(timeout=30)
    assert rc == 0
    reviews, manifest = _reviews(tmp_path)
    assert reviews[0]["reviewer"] == "alice"
    assert manifest["review_policy"] == {"reviewers": ["alice", "bob"]}


def test_env_identity_reaches_the_record(tmp_path, monkeypatch):
    import cadora.cli as cli

    monkeypatch.setenv("CADORA_REVIEWER", "yves-from-env")
    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: a\n    prompt: hi\n    review: true\n")
    executor = Ex()
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: executor)

    def decide():
        request = tmp_path / "cadora-review-request.json"
        for _ in range(1500):
            if request.is_file():
                break
            time.sleep(0.01)
        # A dashboard decision with NO name typed: the run-level identity backfills.
        write_review_decision(tmp_path, REVIEW_APPROVE, "ok")

    thread = threading.Thread(target=decide, daemon=True)
    thread.start()
    rc = cli.main(
        [
            "run", str(topo),
            "--cwd", str(tmp_path),
            "--archive-dir", str(tmp_path / "runs"),
            "--run-id", "r",
            "--hitl", "--review-file", "--review-timeout", "30",
            "--yes",
        ]
    )
    thread.join(timeout=30)
    assert rc == 0
    reviews, _ = _reviews(tmp_path)
    assert reviews[0]["reviewer"] == "yves-from-env"
    assert reviews[0]["method"] == "file-drop"


def test_human_review_md_names_the_reviewer(tmp_path):
    executor = Ex()
    _run(
        tmp_path,
        executor,
        _scripted(ReviewResult(REVIEW_APPROVE, "lgtm", reviewer="yves", method="dashboard")),
    )
    text = (tmp_path / "runs" / "r" / "a" / "human-review.md").read_text()
    assert "`yves` via `dashboard`" in text

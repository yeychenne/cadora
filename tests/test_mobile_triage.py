"""PR-3 — mobile triage: decide while parked, notify when away.

Mobile = triage + decide; desktop = review. Transport stays a tunnel to a loopback dashboard —
no auth layer grew here, and `_guard_bind` is untouched. What this adds:

1. **Decide while parked.** A parked run has no live process, so decisions are stored in the
   ARCHIVE (not the workspace), bound to one node, to the SHA-256 of the exact bytes the
   reviewer saw, and to a declared identity — then honored at resume through the SAME allowlist
   seam as any live decision. That binding is why the workspace stale-clear rule survives:
   a loose file could approve tomorrow's gate with yesterday's verdict; this cannot.
2. **Notify.** One fire-and-forget webhook POST on review_waiting / run_parked. A dead endpoint
   never delays, corrupts, or fails a run.
"""

import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

import pytest

from cadora.park import (
    load_park_record,
    read_parked_decisions,
    store_parked_decision,
    take_parked_decision,
)
from cadora.review import REVIEW_APPROVE, ReviewResult
from cadora.runner import run_topology
from cadora.topology import Node, Topology

from tests.test_park_and_exit import RecordingExecutor, _park, _resume


def _refusing_review_fn(node, node_cwd, documents=None):
    raise AssertionError("the live review surface must not be consulted — a decision is stored")


def _scripted(*results):
    queue = list(results)

    def review_fn(node, node_cwd, documents=None):
        return queue.pop(0)

    return review_fn


def _store(tmp_path, node_id="b", run_id="r", **overrides):
    payload = {
        "decision": "approve",
        "comments": "triaged from the phone",
        "reviewer": "yves",
        "method": "dashboard",
        "decided_at": "2026-07-22T09:00:00+00:00",
        "documents": [],
    }
    payload.update(overrides)
    store_parked_decision(tmp_path / "runs" / run_id, node_id, payload)
    return payload


def _manifest(tmp_path, run_id="r"):
    return json.loads((tmp_path / "runs" / run_id / "manifest.json").read_text())


# --- the parked-decision store -------------------------------------------------------------------


def test_store_take_is_consume_once_and_node_scoped(tmp_path):
    run_dir = tmp_path / "runs" / "r"
    run_dir.mkdir(parents=True)
    store_parked_decision(run_dir, "a", {"decision": "approve"})
    store_parked_decision(run_dir, "b", {"decision": "abort"})
    assert take_parked_decision(run_dir, "a")["decision"] == "approve"
    assert take_parked_decision(run_dir, "a") is None  # consumed
    assert read_parked_decisions(run_dir) == {"b": {"decision": "abort"}}  # b untouched
    assert take_parked_decision(run_dir, "b")["decision"] == "abort"
    assert not (run_dir / "parked-decisions.json").exists()  # empty file removed


# --- honored at resume ---------------------------------------------------------------------------


def test_parked_decision_is_honored_without_consulting_a_live_reviewer(tmp_path):
    parker = RecordingExecutor()
    _park(tmp_path, parker)
    stored = _store(tmp_path)

    resumer = RecordingExecutor()
    _resume(tmp_path, resumer, _refusing_review_fn)  # raises if ever called

    manifest = _manifest(tmp_path)
    assert manifest["ok"] is True
    b = next(n for n in manifest["nodes"] if n["node_id"] == "b")
    review = b["human_reviews"][0]
    assert review["reviewer"] == "yves"
    assert review["method"] == "dashboard"
    # The decision happened when the HUMAN made it, not when the resume applied it.
    assert review["timestamp"] == stored["decided_at"]
    assert [n for n, _ in resumer.calls] == ["c"]  # only downstream ran
    assert read_parked_decisions(tmp_path / "runs" / "r") == {}  # consumed


def test_drifted_document_discards_the_stored_decision(tmp_path):
    """The SHA binding with teeth: change the bytes after the decision and it must NOT apply."""
    doc = tmp_path / "aidlc-docs"
    doc.mkdir()
    (doc / "design.md").write_text("what the reviewer saw\n")
    parker = RecordingExecutor()
    _park(tmp_path, parker)

    import hashlib

    sha = hashlib.sha256((doc / "design.md").read_bytes()).hexdigest()
    _store(tmp_path, documents=[{"path": "aidlc-docs/design.md", "sha256": sha}])
    (doc / "design.md").write_text("something else entirely\n")  # drift after the decision

    resumer = RecordingExecutor()
    _resume(tmp_path, resumer, _scripted(ReviewResult(REVIEW_APPROVE, "live decision")))

    b = next(n for n in _manifest(tmp_path)["nodes"] if n["node_id"] == "b")
    assert b["human_reviews"][0]["comments"] == "live decision"  # the stored one never applied
    events = (tmp_path / "runs" / "r" / "run-events.jsonl").read_text()
    assert "parked_decision_discarded" in events
    assert read_parked_decisions(tmp_path / "runs" / "r") == {}  # consumed even when discarded


def test_allowlisted_run_rejects_a_stored_impostor(tmp_path):
    """A parked decision faces the SAME allowlist as a live one — storage is not a bypass."""
    parker = RecordingExecutor()
    _park(tmp_path, parker)
    _store(tmp_path, reviewer="mallory")

    resumer = RecordingExecutor()
    _resume(
        tmp_path,
        resumer,
        _scripted(ReviewResult(REVIEW_APPROVE, "ok", reviewer="alice")),
        reviewers=["alice"],
    )
    b = next(n for n in _manifest(tmp_path)["nodes"] if n["node_id"] == "b")
    assert b["human_reviews"][0]["reviewer"] == "alice"
    events = (tmp_path / "runs" / "r" / "run-events.jsonl").read_text()
    assert "review_rejected" in events and "mallory" in events


def test_stored_request_changes_drives_the_revision_at_resume(tmp_path):
    parker = RecordingExecutor()
    _park(tmp_path, parker)
    _store(tmp_path, decision="request_changes", comments="tighten the invariants")

    resumer = RecordingExecutor()
    _resume(tmp_path, resumer, _scripted(ReviewResult(REVIEW_APPROVE, "ok")))

    b_prompts = [p for n, p in resumer.calls if n == "b"]
    assert len(b_prompts) == 1  # the revision run
    assert "tighten the invariants" in b_prompts[0]


def test_invalid_stored_decision_falls_to_live_review(tmp_path):
    parker = RecordingExecutor()
    _park(tmp_path, parker)
    _store(tmp_path, decision="request_changes", comments="")  # invalid: no comments

    resumer = RecordingExecutor()
    _resume(tmp_path, resumer, _scripted(ReviewResult(REVIEW_APPROVE, "live")))
    b = next(n for n in _manifest(tmp_path)["nodes"] if n["node_id"] == "b")
    assert b["human_reviews"][0]["comments"] == "live"


def test_triage_sweep_applies_decided_gates_and_reparks_the_rest(tmp_path):
    """`cadora resume --on-review park` = apply the phone's decisions, re-park what remains."""
    from cadora.park import PARK_EXIT_CODE

    executor = RecordingExecutor()
    topo = Topology(
        name="t",
        nodes=[
            Node(id="x", role="builder", prompt="px", review=True),
            Node(id="y", role="builder", prompt="py", review=True),
        ],
    )
    with pytest.raises(SystemExit):
        run_topology(
            topo, executor, run_id="w", cwd=str(tmp_path),
            archive_root=str(tmp_path / "runs"), hitl=True, review_fn=_scripted(),
            on_review="park", park_contract={}, max_parallel=2,
        )
    _store(tmp_path, node_id="x", run_id="w")  # the phone decided x; y still waits

    resumer = RecordingExecutor()
    with pytest.raises(SystemExit) as excinfo:
        _resume(tmp_path, resumer, _refusing_review_fn, run_id="w", on_review="park")
    assert excinfo.value.code == PARK_EXIT_CODE

    manifest = _manifest(tmp_path, "w")
    recorded = {n["node_id"] for n in manifest["nodes"]}
    assert "x" in recorded  # applied
    record = load_park_record(tmp_path / "runs" / "w")
    assert [p["node_id"] for p in record["pending"]] == ["y"]  # y re-parked alone


# --- the dashboard endpoints ----------------------------------------------------------------------


def _serve(archive):
    from cadora.dashboard.server import make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(archive))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


def _post(url, body):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read() or b"{}"
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:  # stdlib send_error() pages are HTML, not JSON
            return error.code, {}


import urllib.error  # noqa: E402


def test_dashboard_park_queue_and_decision_roundtrip(tmp_path):
    (tmp_path / "aidlc-docs").mkdir()
    parker = RecordingExecutor()

    class WritingParker(RecordingExecutor):
        def run(self, node, prompt, *, cwd, env=None):
            (tmp_path / "aidlc-docs" / f"{node.id}.md").write_text(f"# {node.id}\n")
            return super().run(node, prompt, cwd=cwd, env=env)

    parker = WritingParker()
    _park(tmp_path, parker)

    httpd, base = _serve(tmp_path / "runs")
    try:
        with urllib.request.urlopen(f"{base}/api/runs/r") as response:
            payload = json.loads(response.read())
        park = payload["park"]
        (pending,) = [p for p in park["pending"] if p["node_id"] == "b"]
        (doc,) = [d for d in pending["documents"] if d["path"] == "aidlc-docs/b.md"]
        assert len(doc["sha256"]) == 64 and doc["kind"] == "new"

        status, result = _post(
            f"{base}/api/runs/r/park/decision",
            {"node_id": "b", "decision": "approve", "comments": "", "reviewer": "Yves E."},
        )
        assert status == 200 and result["stored"] == "approve"

        stored = read_parked_decisions(tmp_path / "runs" / "r")["b"]
        assert stored["reviewer"] == "Yves E."
        assert stored["method"] == "dashboard"
        assert stored["documents"][0]["sha256"] == doc["sha256"]  # bound to the served bytes

        # Refusals: double-decide, unknown node, invalid decision.
        assert _post(f"{base}/api/runs/r/park/decision", {"node_id": "b", "decision": "abort"})[0] == 409
        assert _post(f"{base}/api/runs/r/park/decision", {"node_id": "zz", "decision": "approve"})[0] == 400
        status, result = _post(
            f"{base}/api/runs/r/park/decision", {"node_id": "b", "decision": "maybe"}
        )
        assert status in (400, 409)
    finally:
        httpd.shutdown()


def test_dashboard_refuses_parked_decision_for_unparked_run(tmp_path):
    run_dir = tmp_path / "runs" / "not-parked"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(json.dumps({"run_id": "not-parked", "status": "running"}))
    httpd, base = _serve(tmp_path / "runs")
    try:
        status, _ = _post(
            f"{base}/api/runs/not-parked/park/decision", {"node_id": "a", "decision": "approve"}
        )
        assert status == 404
    finally:
        httpd.shutdown()


# --- notify ---------------------------------------------------------------------------------------


class _Capture(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        type(self).received.append(self.rfile.read(length).decode())
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # quiet
        pass


def test_notify_fires_on_review_waiting_and_park(tmp_path):
    _Capture.received = []
    httpd = HTTPServer(("127.0.0.1", 0), _Capture)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_port}/cadora"
    try:
        executor = RecordingExecutor()
        with pytest.raises(SystemExit):
            run_topology(
                Topology(name="t", nodes=[Node(id="a", role="b", prompt="p", review=True)]),
                executor,
                run_id="n1",
                cwd=str(tmp_path),
                archive_root=str(tmp_path / "runs"),
                hitl=True,
                review_fn=_scripted(),
                on_review="park",
                park_contract={},
                notify_url=url,
            )
        deadline = time.monotonic() + 5
        while len(_Capture.received) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        text = "\n".join(_Capture.received)
        assert "awaits your review" in text  # review_waiting
        assert "parked" in text  # run_parked
    finally:
        httpd.shutdown()


def test_dead_notify_endpoint_never_breaks_the_run(tmp_path):
    executor = RecordingExecutor()
    out = run_topology(
        Topology(name="t", nodes=[Node(id="a", role="b", prompt="p")]),
        executor,
        run_id="n2",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        notify_url="http://127.0.0.1:9/nothing-listens-here",
    )
    assert out  # completed; the webhook failure was swallowed


# --- the product moment, end to end through the CLI ------------------------------------------------


def test_cli_park_phone_decide_headless_resume(tmp_path, monkeypatch):
    """Park at the laptop → decide from the dashboard → `cadora resume --yes` fully headless:
    no TTY, no --review-file, no reviewer present. The stored decision IS the review."""
    import cadora.cli as cli

    topo = tmp_path / "t.yaml"
    topo.write_text(
        "name: t\nnodes:\n"
        "  - id: a\n    prompt: build a\n    review: true\n"
        "  - id: b\n    prompt: build b\n    depends_on: [a]\n"
    )
    executor = RecordingExecutor()
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: executor)
    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "run", str(topo), "--cwd", str(tmp_path),
                "--archive-dir", str(tmp_path / "runs"), "--run-id", "e2e",
                "--hitl", "--on-review", "park", "--reviewers", "yves", "--yes",
            ]
        )
    assert excinfo.value.code == 75

    httpd, base = _serve(tmp_path / "runs")
    try:
        status, _ = _post(
            f"{base}/api/runs/e2e/park/decision",
            {"node_id": "a", "decision": "approve", "reviewer": "yves"},
        )
        assert status == 200
    finally:
        httpd.shutdown()

    resumer = RecordingExecutor()
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: resumer)
    rc = cli.main(["resume", str(tmp_path / "runs" / "e2e"), "--allow-drift", "--yes"])
    assert rc == 0
    manifest = _manifest(tmp_path, "e2e")
    assert manifest["ok"] is True
    a = next(n for n in manifest["nodes"] if n["node_id"] == "a")
    assert a["human_reviews"][0]["reviewer"] == "yves"
    assert a["human_reviews"][0]["method"] == "dashboard"
    assert manifest["review_policy"] == {"reviewers": ["yves"]}  # allowlist held throughout

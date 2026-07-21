"""PR1 — interactive HITL review in the dashboard.

Covers the cwd bridge, the review GET/POST endpoints and live-doc serving (over real HTTP), the
review.py helpers the dashboard reuses, and the ``--review-timeout 0`` = indefinite fix.
"""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from cadora.dashboard.server import _review_payload, _run_cwd, _safe_cwd_path, make_handler
from cadora.review import (
    DECISION_FILE,
    REQUEST_FILE,
    REVIEW_APPROVE,
    file_review_fn,
    read_review_request,
    write_review_decision,
)
from cadora.topology import Node


def _stage_pending_review(cwd, node_id="requirements"):
    (cwd / REQUEST_FILE).write_text(
        json.dumps({"node_id": node_id, "documents": [{"path": "aidlc-docs/x.md", "kind": "new"}]})
    )
    (cwd / "aidlc-docs").mkdir(exist_ok=True)
    (cwd / "aidlc-docs" / "x.md").write_text("# hi\n")


def _serve(archive):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(archive))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_port}"


# --- review.py bridge helpers (shared with the file-drop reviewer) ------------------------------


def test_review_bridge_read_and_write(tmp_path):
    assert read_review_request(tmp_path) is None
    assert "error" in write_review_decision(tmp_path, "approve")  # nothing pending
    _stage_pending_review(tmp_path)
    assert read_review_request(tmp_path)["node_id"] == "requirements"
    assert "error" in write_review_decision(tmp_path, "maybe")  # invalid decision (fail-soft)
    assert "error" in write_review_decision(tmp_path, "request_changes")  # empty comments
    assert write_review_decision(tmp_path, "approve", "lgtm") == {"submitted": "approve"}
    assert json.loads((tmp_path / DECISION_FILE).read_text())["decision"] == "approve"


def test_file_review_timeout_zero_waits_indefinitely(tmp_path):
    """``--review-timeout 0`` must WAIT (async human / dashboard), not instant-abort as a 0s deadline
    would — matching the MCP channel's ``review_timeout=0`` semantics."""
    result = {}

    def run():
        result["r"] = file_review_fn(timeout=0, interval=0.02)(
            Node(id="n", review=True), str(tmp_path)
        )

    # daemon=True: this gate waits INDEFINITELY, so a failure before the decision write must not
    # leave a non-daemon thread polling and block interpreter shutdown (that hangs the whole run).
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.3)
    assert t.is_alive()  # still waiting after 0.3s — did NOT instantly abort
    (tmp_path / DECISION_FILE).write_text(json.dumps({"decision": "approve", "comments": "ok"}))
    t.join(timeout=3)
    assert result["r"].decision == REVIEW_APPROVE


# --- server helpers ----------------------------------------------------------------------------


def test_run_cwd_reads_the_recorded_workspace(tmp_path):
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    assert _run_cwd(tmp_path, "r1") is None  # no run-input.json yet
    ws = tmp_path / "ws"
    ws.mkdir()
    (run_dir / "run-input.json").write_text(json.dumps({"cwd": str(ws)}))
    assert _run_cwd(tmp_path, "r1") == str(ws)


def test_review_payload_shapes_documents_as_urls(tmp_path):
    assert _review_payload("r1", None) == {"pending": False}
    _stage_pending_review(tmp_path)
    payload = _review_payload("r1", str(tmp_path))
    assert payload["pending"] and payload["node_id"] == "requirements"
    assert payload["documents"][0]["url"].startswith("/api/runs/r1/review/doc?path=")


def test_safe_cwd_path_blocks_traversal(tmp_path):
    (tmp_path / "aidlc-docs").mkdir()
    (tmp_path / "aidlc-docs" / "x.md").write_text("hi")
    assert _safe_cwd_path(tmp_path, "aidlc-docs/x.md").read_text() == "hi"
    with pytest.raises(ValueError):
        _safe_cwd_path(tmp_path, "../../../etc/passwd")


# --- HTTP integration: the review journey over the wire ----------------------------------------


def _run_with_ws(tmp_path):
    archive = tmp_path / "runs"
    run_dir = archive / "r1"
    run_dir.mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    (run_dir / "run-input.json").write_text(json.dumps({"cwd": str(ws)}))
    (run_dir / "status.json").write_text(json.dumps({"run_id": "r1", "nodes": {}}))
    _stage_pending_review(ws)
    return archive, ws


def test_review_get_doc_and_post_over_http(tmp_path):
    archive, ws = _run_with_ws(tmp_path)
    httpd, base = _serve(archive)
    try:
        review = json.loads(urllib.request.urlopen(f"{base}/api/runs/r1/review").read())
        assert review["pending"] and review["node_id"] == "requirements"

        doc = urllib.request.urlopen(f"{base}{review['documents'][0]['url']}").read().decode()
        assert "hi" in doc  # the live under-review document served from the run workspace

        req = urllib.request.Request(
            f"{base}/api/runs/r1/review",
            method="POST",
            data=json.dumps({"decision": "approve", "comments": "ok"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(req).read()) == {"submitted": "approve"}
        assert json.loads((ws / DECISION_FILE).read_text())["decision"] == "approve"
    finally:
        httpd.shutdown()


def test_review_post_rejects_non_json_content_type(tmp_path):
    archive, _ = _run_with_ws(tmp_path)
    httpd, base = _serve(archive)
    try:
        req = urllib.request.Request(
            f"{base}/api/runs/r1/review", method="POST", data=b'{"decision":"approve"}'
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 415  # CSRF guard: cross-origin form POST can't reach the write path
    finally:
        httpd.shutdown()


def test_review_post_bad_decision_is_fail_soft(tmp_path):
    archive, _ = _run_with_ws(tmp_path)
    httpd, base = _serve(archive)
    try:
        req = urllib.request.Request(
            f"{base}/api/runs/r1/review",
            method="POST",
            data=json.dumps({"decision": "maybe"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req)
        assert exc.value.code == 400
        assert "error" in json.loads(exc.value.read())
    finally:
        httpd.shutdown()

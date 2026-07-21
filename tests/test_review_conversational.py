"""PR2 — conversational review: ask a question about a document, request a revision on the spot.

While a --review-file node is parked at its gate, the run thread services reviewer messages through
the executor: a question is answered, a revision rewrites the document in place. Covers the review.py
loop + helpers, the fixture executor's answers, and the dashboard message/reply endpoints over HTTP.
"""

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

from cadora.dashboard.server import make_handler
from cadora.executors.base import ExecutionResult
from cadora.executors.fixture import FixtureExecutor
from cadora.review import (
    DECISION_FILE,
    MESSAGE_FILE,
    REPLY_FILE,
    REQUEST_FILE,
    REVIEW_APPROVE,
    REVIEW_QUESTION,
    REVIEW_REVISION,
    file_review_fn,
    read_review_reply,
    read_review_request,
    write_review_message,
)
from cadora.topology import Node


class _FakeExec:
    def __init__(self, answer="Decimal avoids float rounding."):
        self.answer = answer

    def run(self, node, prompt, *, cwd, env=None):
        text = "REVISED DOC BODY" if "[[cadora-review-revision]]" in prompt else self.answer
        return ExecutionResult(node_id=node.id, ok=True, exit_code=0, text=text)


def _stage_doc(cwd):
    (cwd / "aidlc-docs").mkdir(exist_ok=True)
    (cwd / "aidlc-docs" / "x.md").write_text("# X\noriginal body\n")


def _park(tmp_path, executor):
    """Run a file review gate on a thread and wait until it opens; return (thread, result-holder)."""
    holder = {}

    def run():
        holder["r"] = file_review_fn(timeout=0, interval=0.02, executor=executor)(
            Node(id="requirements", review=True), str(tmp_path)
        )

    # daemon=True is load-bearing: the gate waits INDEFINITELY (timeout=0), so if an assertion
    # below fails before the decision file is written, a non-daemon thread would keep polling and
    # block interpreter shutdown — turning a test failure into a hung run (CI killed it at 6h).
    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(300):
        if (tmp_path / REQUEST_FILE).is_file():
            break
        time.sleep(0.02)
    return t, holder


def _await_reply(tmp_path):
    for _ in range(300):
        reply = read_review_reply(tmp_path)
        if reply and reply.get("reply"):
            return reply
        time.sleep(0.02)
    raise AssertionError("no reply written")


# --- the message helper (dashboard -> run) ------------------------------------------------------


def test_message_helper_fail_soft(tmp_path):
    assert "error" in write_review_message(tmp_path, "question", "why?")  # nothing pending
    (tmp_path / REQUEST_FILE).write_text(json.dumps({"node_id": "n", "documents": []}))
    assert "error" in write_review_message(tmp_path, "bogus", "x")  # unknown kind
    assert "error" in write_review_message(tmp_path, "question", "   ")  # empty message
    (tmp_path / REPLY_FILE).write_text("stale")
    assert write_review_message(tmp_path, "question", "why?", "a.md") == {"sent": "question"}
    assert not (tmp_path / REPLY_FILE).exists()  # the previous reply is cleared before asking again
    assert json.loads((tmp_path / MESSAGE_FILE).read_text())["message"] == "why?"


# --- the parked run answers, then still takes a decision -----------------------------------------


def test_parked_gate_answers_a_question_then_decides(tmp_path):
    _stage_doc(tmp_path)
    t, holder = _park(tmp_path, _FakeExec())
    # Assert the SEND, not just the reply: a rejected send ("no review is pending" from a
    # partially-written request file) used to surface only as a mystifying "no reply written".
    assert write_review_message(tmp_path, REVIEW_QUESTION, "why Decimal?", "aidlc-docs/x.md") == {"sent": "question"}
    reply = _await_reply(tmp_path)
    assert reply["kind"] == "question" and "Decimal" in reply["reply"]
    (tmp_path / DECISION_FILE).write_text(json.dumps({"decision": "approve", "comments": ""}))
    t.join(timeout=3)
    assert holder["r"].decision == REVIEW_APPROVE  # conversation didn't consume the gate


def test_parked_gate_revision_rewrites_the_document_in_place(tmp_path):
    _stage_doc(tmp_path)
    t, holder = _park(tmp_path, _FakeExec())
    assert write_review_message(tmp_path, REVIEW_REVISION, "tighten it", "aidlc-docs/x.md") == {"sent": "revision"}
    reply = _await_reply(tmp_path)
    assert reply["kind"] == "revision"
    assert (tmp_path / "aidlc-docs" / "x.md").read_text() == "REVISED DOC BODY"  # applied in place
    (tmp_path / DECISION_FILE).write_text(json.dumps({"decision": "approve"}))
    t.join(timeout=3)


# --- the fixture executor answers / revises deterministically -----------------------------------


def test_fixture_executor_answers_and_revises(tmp_path):
    ex = FixtureExecutor()
    q = ex.run(
        Node(id="n"),
        "[[cadora-review-question]] A reviewer asks about `x`.\nQuestion: why Decimal?\n\n"
        "Document:\n# Doc\nUses Decimal for money.\n\nAnswer concisely from the document. Do not modify any files.",
        cwd=str(tmp_path),
    )
    assert "why Decimal?" in q.text and "Decimal for money" in q.text
    r = ex.run(
        Node(id="n"),
        "[[cadora-review-revision]] A reviewer requested a revision of `x`.\nInstruction: shorten it\n\n"
        "Current document:\n# Doc\nbody\n\nReturn the COMPLETE revised document as your response, and nothing else.",
        cwd=str(tmp_path),
    )
    assert "## Revision (fixture)" in r.text and "shorten it" in r.text


# --- dashboard: message POST + reply GET over HTTP ----------------------------------------------


def test_dashboard_message_and_reply_over_http(tmp_path):
    archive = tmp_path / "runs"
    run_dir = archive / "r1"
    run_dir.mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    (run_dir / "run-input.json").write_text(json.dumps({"cwd": str(ws)}))
    _stage_doc(ws)
    (ws / REQUEST_FILE).write_text(
        json.dumps({"node_id": "requirements", "documents": [{"path": "aidlc-docs/x.md", "kind": "new"}]})
    )
    (ws / REPLY_FILE).write_text(json.dumps({"kind": "question", "reply": "because"}))

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(archive))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        rep = json.loads(urllib.request.urlopen(f"{base}/api/runs/r1/review/reply").read())
        assert rep["reply"] == "because"
        req = urllib.request.Request(
            f"{base}/api/runs/r1/review/message",
            method="POST",
            data=json.dumps({"kind": "question", "message": "why?", "path": "aidlc-docs/x.md"}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert json.loads(urllib.request.urlopen(req).read()) == {"sent": "question"}
        assert json.loads((ws / MESSAGE_FILE).read_text())["message"] == "why?"
    finally:
        httpd.shutdown()


# --- the mid-write race (the CI flake) -----------------------------------------------------------


def test_partial_message_is_retried_not_swallowed(tmp_path):
    """A message file caught mid-write must NOT be deleted as corrupt.

    The parked gate polls MESSAGE_FILE on a tight interval; before writes were atomic it could
    read a half-written body, fail to parse it, and unlink it — silently swallowing the
    reviewer's ask (CI: "no reply written"). The reader must leave an unparsable file for the
    next tick, and the completed message must then be serviced normally.
    """
    _stage_doc(tmp_path)
    t, holder = _park(tmp_path, _FakeExec())
    # Simulate a non-atomic third-party writer caught mid-write: truncated JSON on disk.
    (tmp_path / MESSAGE_FILE).write_text('{"kind": "question", "mess')
    time.sleep(0.2)  # several poll ticks at interval=0.02
    assert (tmp_path / MESSAGE_FILE).is_file(), "partial message was deleted (swallowed)"
    # The writer finishes: replace with the complete body (what an atomic writer guarantees).
    # Assert the SEND, not just the reply: a rejected send ("no review is pending" from a
    # partially-written request file) used to surface only as a mystifying "no reply written".
    assert write_review_message(tmp_path, REVIEW_QUESTION, "why Decimal?", "aidlc-docs/x.md") == {"sent": "question"}
    reply = _await_reply(tmp_path)
    assert reply["kind"] == "question" and reply["reply"]
    (tmp_path / DECISION_FILE).write_text(json.dumps({"decision": "approve"}))
    t.join(timeout=3)
    assert holder["r"].decision == REVIEW_APPROVE


def test_request_file_is_never_observed_partially_written(tmp_path):
    """The request file must appear atomically — existence implies a parseable body.

    An out-of-process reader (the dashboard, write_review_message) polls for REQUEST_FILE to
    exist and then parses it. When it was written non-atomically, a slow runner could see the
    file before its body landed, parse nothing, and reject the reviewer's message with
    "no review is pending" — the CI flake that surfaced as "no reply written" on 3.10.
    """
    _stage_doc(tmp_path)
    t, holder = _park(tmp_path, _FakeExec())
    # The gate is parked, so the request file exists; every read of it must parse.
    for _ in range(50):
        assert read_review_request(tmp_path) is not None, "request file existed but did not parse"
    # And a message sent immediately after the file appears must be accepted.
    assert write_review_message(tmp_path, REVIEW_QUESTION, "why?", "aidlc-docs/x.md") == {
        "sent": "question"
    }
    (tmp_path / DECISION_FILE).write_text(json.dumps({"decision": "approve"}))
    t.join(timeout=3)
    assert holder["r"].decision == REVIEW_APPROVE

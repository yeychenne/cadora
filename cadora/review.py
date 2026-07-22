"""Structured human-review decisions for explicit HITL topology gates."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REVIEW_APPROVE = "approve"
REVIEW_REQUEST_CHANGES = "request_changes"
REVIEW_ABORT = "abort"
REVIEW_DECISIONS = {REVIEW_APPROVE, REVIEW_REQUEST_CHANGES, REVIEW_ABORT}


@dataclass(frozen=True)
class ReviewResult:
    decision: str
    comments: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if self.decision not in REVIEW_DECISIONS:
            raise ValueError(f"invalid review decision: {self.decision!r}")
        if not self.timestamp:
            object.__setattr__(
                self,
                "timestamp",
                datetime.now(timezone.utc).isoformat(),
            )
        if self.decision == REVIEW_REQUEST_CHANGES and not self.comments.strip():
            raise ValueError("request_changes requires reviewer comments")


def format_review_history(reviews: list[ReviewResult]) -> str:
    sections = []
    for attempt, review in enumerate(reviews, start=1):
        body = review.comments.strip() or "(no comments)"
        sections.append(
            f"## Review {attempt}\n\n"
            f"- Decision: `{review.decision}`\n"
            f"- Timestamp: `{review.timestamp}`\n\n"
            f"{body}\n"
        )
    return "\n".join(sections)


REQUEST_FILE = "cadora-review-request.json"
DECISION_FILE = "cadora-review-decision.json"
# Conversational review (dropped by a reviewer, answered by the parked run thread via the executor):
# a message asks a question about a document or requests a revised draft; the reply carries the answer.
MESSAGE_FILE = "cadora-review-message.json"
REPLY_FILE = "cadora-review-reply.json"
REVIEW_QUESTION = "question"
REVIEW_REVISION = "revision"
REVIEW_MESSAGE_KINDS = {REVIEW_QUESTION, REVIEW_REVISION}


def file_review_fn(timeout: float = 3600.0, interval: float = 2.0, executor=None,
                   spend_journal: str | Path | None = None):
    """A headless HITL reviewer: write a request file, poll for a decision file.

    For non-interactive runs (Quick Desktop, CI, background) where there is no TTY. Cadora writes
    ``cadora-review-request.json`` into the node's workspace listing the stage's documents, then
    blocks until any tool or human drops a ``cadora-review-decision.json`` next to it:

        {"decision": "approve" | "request_changes" | "abort", "comments": "..."}

    Fails **closed**: an invalid decision, or no decision within ``timeout`` seconds, returns
    ``abort`` — the run stops honestly rather than proceeding unreviewed. A ``timeout`` of ``0`` (or
    negative) waits **indefinitely** — the right choice for a genuinely async human reviewer (a
    dashboard, a person who stepped away) where a walk-away client isn't the risk — matching the MCP
    channel's ``review_timeout=0`` semantics.

    When ``executor`` is supplied, the parked run also answers **conversational** review while it
    waits: a reviewer drops ``cadora-review-message.json`` (``{"kind": "question" | "revision",
    "message": ..., "path": ...}``) and the run runs the executor scoped to that document, writing the
    answer to ``cadora-review-reply.json`` — a question is answered, a revision rewrites the document
    in place and re-surfaces it — all before any decision is made.

    ``spend_journal`` (a path inside the run's archive) makes that conversational spend
    **crash-durable**: every turn is appended to the journal the moment it completes, so a run
    killed while parked loses no cost record — the next invocation reads the pending turns back
    and charges them. In memory it would die with the process.
    """

    indefinite = timeout <= 0
    # Conversational review spends real money: every Ask and every Revise is a full executor call,
    # and a reviewer may send unlimited messages while a gate is parked. Accumulate it here so the
    # runner can charge it to the budget ledger and the archive — otherwise it is spend that no
    # ceiling can see and no evidence records. Shared across review_fn calls; the runner reads the
    # delta around each gate.
    spend: dict = {"cost_usd": 0.0, "usage": {}}
    current_node: dict = {"id": None}

    def _record_spend(result) -> None:
        cost = getattr(result, "cost_usd", None)
        if cost:
            spend["cost_usd"] += cost
        usage = getattr(result, "usage", None) or {}
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                spend["usage"][key] = spend["usage"].get(key, 0) + value
        # Durability before bookkeeping order: the turn hits disk the moment it happened. A run
        # killed while parked then owes nothing to memory — the journal IS the record.
        if spend_journal is not None:
            append_review_turn(
                spend_journal,
                node_id=current_node["id"],
                cost_usd=cost,
                usage=usage,
            )

    def review_fn(node, node_cwd: str, documents=None) -> ReviewResult:
        current_node["id"] = node.id
        base = Path(node_cwd)
        request, decision = base / REQUEST_FILE, base / DECISION_FILE
        message, reply = base / MESSAGE_FILE, base / REPLY_FILE
        for stale in (decision, message, reply):
            stale.unlink(missing_ok=True)  # clear any stale files before we ask
        # Atomic like the other three review files: an out-of-process reader (the dashboard,
        # write_review_message) polls for this file's EXISTENCE and then parses it. A plain
        # write_text lets that reader see the file before its body lands, parse nothing, and
        # conclude "no review is pending" — silently dropping the reviewer's message.
        _write_json_atomic(
            request,
            {
                "node_id": node.id,
                "documents": [{"path": p, "kind": k} for p, k in (documents or [])],
                "how_to_respond": (
                    f"write {DECISION_FILE} next to this file: "
                    '{"decision": "approve" | "request_changes" | "abort", "comments": "..."}'
                ),
            },
            indent=2,
        )
        responder = (
            _review_responder(executor, node, node_cwd, on_spend=_record_spend)
            if executor is not None
            else None
        )
        if responder is not None:
            # Enforce the budget ceiling DURING the conversation, not one node later. The runner
            # attaches `budget_guard` when a stop/failover policy is active; called with what this
            # gate's conversation has spent so far, it returns a refusal message once the ceiling
            # is reached. The refusal replaces the executor call — the reviewer is told plainly,
            # and the gate stays decidable: approve / request changes / abort cost nothing.
            gate_start = spend["cost_usd"]
            inner = responder
            guard = getattr(review_fn, "budget_guard", None)

            def responder(kind: str, text: str, path: str) -> str:  # noqa: F811
                if guard is not None:
                    refusal = guard(spend["cost_usd"] - gate_start)
                    if refusal:
                        return refusal
                return inner(kind, text, path)
        deadline = time.monotonic() + timeout
        while indefinite or time.monotonic() < deadline:
            if responder is not None and message.is_file():
                _service_review_message(message, reply, responder)  # ask / revise, then keep waiting
                continue
            if decision.is_file():
                try:
                    data = json.loads(decision.read_text())
                except (OSError, ValueError):
                    time.sleep(interval)
                    continue
                request.unlink(missing_ok=True)
                decision.unlink(missing_ok=True)
                reply.unlink(missing_ok=True)
                choice = str(data.get("decision", "")).strip()
                comments = str(data.get("comments", "")).strip()
                if choice not in REVIEW_DECISIONS:
                    return ReviewResult(REVIEW_ABORT, f"invalid decision in {DECISION_FILE}: {choice!r}")
                if choice == REVIEW_REQUEST_CHANGES and not comments:
                    return ReviewResult(REVIEW_ABORT, "request_changes requires reviewer comments")
                return ReviewResult(choice, comments)
            time.sleep(interval)
        request.unlink(missing_ok=True)
        return ReviewResult(REVIEW_ABORT, f"file review timed out after {int(timeout)}s")

    # Published so the runner can charge what the conversation cost. An attribute rather than a
    # changed return type keeps every other review_fn (stdin, MCP, test doubles) working untouched.
    review_fn.review_spend = spend
    review_fn.spend_journal = spend_journal
    return review_fn


def append_review_turn(journal: str | Path, *, node_id: str | None, cost_usd, usage: dict) -> None:
    """Append one conversational turn to the crash-durability journal (one JSON line per turn).

    Appends are line-granular: a crash mid-write costs at most the line being written, and the
    reader skips anything unparsable rather than losing the rest.
    """
    path = Path(journal)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "node_id": node_id,
                "cost_usd": cost_usd,
                "usage": {k: v for k, v in (usage or {}).items() if isinstance(v, (int, float))},
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        with path.open("a") as handle:
            handle.write(line + "\n")
            handle.flush()
    except OSError:
        # Journaling is belt-and-braces on top of in-memory accounting — refusing to answer a
        # reviewer because the journal disk write failed would be the worse trade.
        pass


def read_pending_review_spend(journal: str | Path | None, node_id: str) -> dict:
    """Total uncommitted conversational spend for one node: ``{"cost_usd": float, "usage": {}}``.

    "Uncommitted" means still in the journal — turns are cleared by :func:`clear_review_spend`
    once the node's archive record has absorbed them. After a kill while parked, everything the
    conversation spent is still here.
    """
    totals: dict = {"cost_usd": 0.0, "usage": {}}
    if journal is None:
        return totals
    path = Path(journal)
    if not path.is_file():
        return totals
    for line in path.read_text().splitlines():
        try:
            turn = json.loads(line)
        except (ValueError, TypeError):
            continue  # a torn final line from a crash — skip it, keep the rest
        if not isinstance(turn, dict) or turn.get("node_id") != node_id:
            continue
        totals["cost_usd"] += turn.get("cost_usd") or 0.0
        for key, value in (turn.get("usage") or {}).items():
            if isinstance(value, (int, float)):
                totals["usage"][key] = totals["usage"].get(key, 0) + value
    return totals


def clear_review_spend(journal: str | Path | None, node_id: str) -> None:
    """Drop one node's turns from the journal — called only after the archive recorded them.

    Rewrites atomically (tmp + replace), keeping other nodes' pending turns intact.
    """
    if journal is None:
        return
    path = Path(journal)
    if not path.is_file():
        return
    kept = []
    for line in path.read_text().splitlines():
        try:
            turn = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(turn, dict) and turn.get("node_id") != node_id:
            kept.append(line)
    try:
        if kept:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text("\n".join(kept) + "\n")
            tmp.replace(path)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _write_json_atomic(path: Path, payload: dict, *, indent: int | None = None) -> None:
    """Write a message/reply file atomically (tmp + rename).

    The parked gate polls these files on a tight interval; a plain ``write_text`` opens a window
    where the poller sees the file exist with a partial body. For the reply that only delays a
    reader one tick — but a partially-read *message* used to be unlinked as corrupt, silently
    swallowing the reviewer's ask (the CI flake: "no reply written"). Atomic rename closes the
    window at the writer; the reader below also retries instead of deleting, as belt and braces.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=indent))
    tmp.replace(path)


def _service_review_message(message_path: Path, reply_path: Path, responder) -> None:
    """Consume one reviewer message and write the run's reply (ask / revise, via the executor)."""
    try:
        msg = json.loads(message_path.read_text())
    except (OSError, ValueError):
        # Unreadable right now — most likely a mid-write race from a non-atomic third-party
        # writer. Leave the file in place and let the next poll tick retry; deleting here would
        # silently swallow the reviewer's message (the decision path has the same retry shape).
        return
    message_path.unlink(missing_ok=True)
    kind = str(msg.get("kind", "")).strip()
    text = str(msg.get("message", "")).strip()
    path = str(msg.get("path", "")).strip()
    if kind not in REVIEW_MESSAGE_KINDS or not text:
        payload = {"kind": kind, "path": path, "error": "invalid review message"}
    else:
        payload = {
            "kind": kind,
            "path": path,
            "reply": responder(kind, text, path),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    _write_json_atomic(reply_path, payload)


def _review_responder(executor, node, node_cwd: str, on_spend=None):
    """Answer a reviewer's question, or produce a revised draft, via the node's executor scoped to a
    document. A question returns the executor's answer (no file changes); a revision rewrites the
    document in place so the gate re-surfaces it. The executor is asked to reply in its response text.

    ``on_spend`` receives each execution result so its cost and usage reach the ledger and the
    archive. Without it the reply text is kept and the price of producing it is thrown away.
    """

    def responder(kind: str, message: str, path: str) -> str:
        doc_text, target = "", None
        if path:
            target = Path(node_cwd) / path
            if target.is_file():
                doc_text = target.read_text(errors="replace")[:8000]
        if kind == REVIEW_REVISION:
            prompt = (
                f"[[cadora-review-revision]] A reviewer requested a revision of `{path}`.\n"
                f"Instruction: {message}\n\nCurrent document:\n{doc_text}\n\n"
                "Return the COMPLETE revised document as your response, and nothing else."
            )
            result = executor.run(node, prompt, cwd=node_cwd)
            if on_spend is not None:
                on_spend(result)
            revised = (getattr(result, "text", "") or "").strip()
            if revised and target is not None:
                target.write_text(revised, encoding="utf-8")
            return revised or "(no revision produced)"
        prompt = (
            f"[[cadora-review-question]] A reviewer asks about `{path}`.\n"
            f"Question: {message}\n\nDocument:\n{doc_text}\n\n"
            "Answer concisely from the document. Do not modify any files."
        )
        result = executor.run(node, prompt, cwd=node_cwd)
        if on_spend is not None:
            on_spend(result)
        return (getattr(result, "text", "") or "").strip() or "(no answer)"

    return responder


def write_review_message(cwd: str | Path, kind: str, message: str, path: str = "") -> dict:
    """Ask a parked run a question or request a revision (dashboard → run). Fail-soft, like
    :func:`write_review_decision`. The run answers into ``cadora-review-reply.json``."""
    if kind not in REVIEW_MESSAGE_KINDS:
        return {"error": f"unknown message kind: {kind!r}"}
    if not str(message).strip():
        return {"error": "a message is required"}
    if read_review_request(cwd) is None:
        return {"error": "no review is pending for this run"}
    (Path(cwd) / REPLY_FILE).unlink(missing_ok=True)  # clear the previous reply before asking again
    # Atomic: the parked gate polls MESSAGE_FILE on a tight interval, and a plain write_text lets
    # it observe a partially-written body mid-write (the swallowed-message CI flake).
    _write_json_atomic(Path(cwd) / MESSAGE_FILE, {"kind": kind, "message": message, "path": path})
    return {"sent": kind}


def read_review_reply(cwd: str | Path) -> dict | None:
    """The latest reply the run wrote to a reviewer message, or ``None``."""
    path = Path(cwd) / REPLY_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def read_review_request(cwd: str | Path) -> dict | None:
    """The pending file-review request in ``cwd`` (written by :func:`file_review_fn`), or ``None``.

    Lets an out-of-process surface — the dashboard — discover what a ``--review-file`` run is
    currently waiting on (node + documents) without holding the run's in-memory review channel.
    """
    path = Path(cwd) / REQUEST_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_review_decision(cwd: str | Path, decision: str, comments: str = "") -> dict:
    """Deliver a review decision to a ``--review-file`` run by dropping its decision file.

    The cross-process counterpart to :func:`file_review_fn`. Fails **soft** the same way the MCP
    ``submit_review`` tool does — an invalid decision, a ``request_changes`` with no comments, or a
    submit when no review is pending returns ``{"error": ...}`` rather than raising — so a UI never
    has to handle an exception. Returns ``{"submitted": decision}`` once the file is written.
    """
    try:
        ReviewResult(decision, comments)
    except ValueError as exc:
        return {"error": str(exc)}
    if read_review_request(cwd) is None:
        return {"error": "no review is pending for this run"}
    # Atomic for the same reason as the message file — the gate loop tolerates a partial read by
    # retrying, but there is no reason to open the window at all.
    _write_json_atomic(Path(cwd) / DECISION_FILE, {"decision": decision, "comments": comments})
    return {"submitted": decision}

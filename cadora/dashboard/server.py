"""Small localhost dashboard server backed by the run archive."""

from __future__ import annotations

import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from cadora.archive import list_runs, read_manifest
from cadora.review import (
    read_review_reply,
    read_review_request,
    write_review_decision,
    write_review_message,
)
from cadora.usage import normalize_manifest_usage, summarize_usage


def serve_dashboard(
    archive_dir: str | Path | list[str | Path] = "runs",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Serve the local dashboard until interrupted.

    ``archive_dir`` may be one archive or a list — a reviewer running several projects (each
    archiving to its own dir) needs their pending gates on ONE surface; a dashboard that silently
    serves a single archive let a review decision be "approved" into the void.

    Binds localhost by default and has no authentication, so keep it on loopback or front
    it with TLS + authentication before exposing it beyond the host.
    """
    handler = make_handler(archive_dir)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"cadora dashboard: http://{host}:{httpd.server_port}/")
    for root in _as_roots(archive_dir):
        resolved = root.resolve()
        print(
            f"  serving archive: {resolved}  "
            f"({'ok' if resolved.is_dir() else 'MISSING — no runs here'})"
        )
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"WARNING: dashboard bound to {host} (non-loopback) with no authentication; "
            "it serves the run archive. Front it with TLS + auth before exposing beyond this host."
        )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ncadora dashboard stopped")
    finally:
        httpd.server_close()


def _as_roots(archive_dir: str | Path | list[str | Path]) -> list[Path]:
    """Normalize the archive argument to a list of root paths (single value stays supported)."""
    if isinstance(archive_dir, (str, Path)):
        return [Path(archive_dir)]
    return [Path(p) for p in archive_dir] or [Path("runs")]


def make_handler(archive_dir: str | Path | list[str | Path]):
    roots = _as_roots(archive_dir)

    def _root_for(run_id: str) -> Path:
        """The archive that holds ``run_id`` — first match wins on a duplicate id."""
        for root in roots:
            if (root / run_id).is_dir():
                return root
        return roots[0]

    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "CadoraDashboard/0.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            try:
                if path == "/" or path.startswith("/runs/"):
                    self._static("index.html")
                elif path.startswith("/static/"):
                    self._static(path.removeprefix("/static/"))
                elif path == "/api/runs":
                    self._json(_runs_payload(roots))
                elif path == "/api/usage":
                    since = (query.get("since") or [None])[0]
                    self._json(summarize_usage(roots, since=since).to_dict())
                elif path.startswith("/api/runs/"):
                    self._run_api(path, query)
                else:
                    self.send_error(404, "not found")
            except FileNotFoundError:
                self.send_error(404, "not found")
            except ValueError as exc:
                self.send_error(400, str(exc))

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            # The one write path: deliver a human review decision to a live --review-file run.
            parts = [unquote(p) for p in urlparse(self.path).path.split("/") if p]
            try:
                if len(parts) >= 4 and parts[0] == "api" and parts[1] == "runs" and parts[3] == "review":
                    run_id = _safe_segment(parts[2])
                    if len(parts) == 4:
                        self._submit_review(run_id)
                        return
                    if len(parts) == 5 and parts[4] == "message":
                        self._submit_message(run_id)
                        return
                self.send_error(404, "not found")
            except ValueError as exc:
                self.send_error(400, str(exc))

        def _submit_review(self, run_id: str) -> None:
            # Basic CSRF guard: require a JSON content-type so a cross-origin form POST can't reach
            # this write path without a (blocked) preflight. The surface is loopback + unauth anyway.
            ctype = self.headers.get("Content-Type", "")
            if "application/json" not in ctype:
                self.send_error(415, "content-type must be application/json")
                return
            cwd = _run_cwd(_root_for(run_id), run_id)
            if cwd is None:
                self.send_error(404, "run workspace unknown — decisions need a --review-file run")
                return
            body = self._read_json_body()
            result = write_review_decision(
                cwd,
                str(body.get("decision", "")),
                str(body.get("comments", "")),
                # Self-asserted, like every local surface — but recorded, with the method that
                # carried it, so the evidence can distinguish a dashboard approval from a
                # hand-dropped file.
                reviewer=str(body.get("reviewer", "")).strip() or None,
                method="dashboard",
            )
            self._json(result, status=200 if "submitted" in result else 400)

        def _submit_message(self, run_id: str) -> None:
            if "application/json" not in self.headers.get("Content-Type", ""):
                self.send_error(415, "content-type must be application/json")
                return
            cwd = _run_cwd(_root_for(run_id), run_id)
            if cwd is None:
                self.send_error(404, "run workspace unknown — needs a --review-file run")
                return
            body = self._read_json_body()
            result = write_review_message(
                cwd,
                str(body.get("kind", "")),
                str(body.get("message", "")),
                str(body.get("path", "")),
            )
            self._json(result, status=200 if "sent" in result else 400)

        def log_message(self, format: str, *args) -> None:
            return

        def _run_api(self, path: str, query: dict) -> None:
            parts = [unquote(p) for p in path.split("/") if p]
            if len(parts) < 3:
                self.send_error(404, "not found")
                return
            run_id = _safe_segment(parts[2])
            root = _root_for(run_id)
            if len(parts) == 3:
                self._json(_run_payload(root, run_id))
                return
            if parts[3] == "status" and len(parts) == 4:
                self._json(_read_json(root / run_id / "status.json"))
                return
            if parts[3] == "events" and len(parts) == 4:
                self._json(_read_jsonl(root / run_id / "run-events.jsonl"))
                return
            if parts[3] == "input" and len(parts) == 4:
                self._json(_read_json(root / run_id / "run-input.json"))
                return
            if len(parts) >= 6 and parts[3] == "nodes":
                node_id = _safe_segment(parts[4])
                node_dir = root / run_id / node_id
                if parts[5] == "output":
                    self._text((node_dir / "output.txt").read_text())
                    return
                if parts[5] == "events":
                    self._json(_read_jsonl(node_dir / "events.jsonl"))
                    return
                if parts[5] == "artifacts":
                    if len(parts) == 6:
                        self._json(_node_artifacts(node_dir))
                        return
                    if len(parts) == 7:
                        artifact = _safe_artifact_path(node_dir, parts[6])
                        self._text(artifact.read_text(errors="replace"))
                        return
            if parts[3] == "review":
                cwd = _run_cwd(root, run_id)
                if len(parts) == 4:
                    self._json(_review_payload(run_id, cwd))
                    return
                if len(parts) == 5 and parts[4] == "reply":
                    self._json((read_review_reply(cwd) if cwd else None) or {"reply": None})
                    return
                if len(parts) == 5 and parts[4] == "doc":
                    if cwd is None:
                        self.send_error(404, "run workspace unknown")
                        return
                    relpath = (query.get("path") or [""])[0]
                    self._text(_safe_cwd_path(Path(cwd), relpath).read_text(errors="replace"))
                    return
            self.send_error(404, "not found")

        def _json(self, payload: object, status: int = 200) -> None:
            data = json.dumps(payload, indent=2).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _text(self, payload: str, status: int = 200) -> None:
            data = payload.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json_body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            try:
                data = json.loads(self.rfile.read(length))
            except (ValueError, TypeError) as exc:
                raise ValueError("request body must be JSON") from exc
            return data if isinstance(data, dict) else {}

        def _static(self, name: str) -> None:
            if "/" in name:
                clean = name.split("/")[-1]
            else:
                clean = name
            data = resources.files("cadora.dashboard.static").joinpath(clean).read_bytes()
            ctype = mimetypes.guess_type(clean)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # Revalidate on every load: browsers otherwise cache app.js/style.css indefinitely
            # (no validators were sent), so dashboard fixes silently didn't reach reviewers.
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

    return DashboardHandler


def _runs_payload(roots: Path | list[Path]) -> dict:
    """All runs across every served archive, newest-first, each tagged with its archive.

    On a duplicate run id the first archive wins (matching ``_root_for``), so the list and the
    run-detail endpoints always agree about which run a given id names.
    """
    all_runs: dict[str, dict] = {}
    root_list = _as_roots(roots)
    for root in root_list:
        manifests = {m.get("run_id"): m for m in list_runs(root)}
        run_ids = set(manifests)
        if root.is_dir():
            run_ids.update(d.name for d in root.iterdir() if d.is_dir())
        for run_id in run_ids:
            if run_id in all_runs:
                continue  # first archive wins, like _root_for
            status_path = root / str(run_id) / "status.json"
            status = _maybe_json(status_path)
            all_runs[run_id] = {
                "run_id": run_id,
                "archive": str(root),
                "manifest": manifests.get(run_id),
                "node_costs": _node_costs(manifests.get(run_id)),
                "status": status,
                "active": bool(status and status.get("status") in {"created", "running"}),
                # How long since the run last wrote its status. A killed conductor (SIGKILL,
                # power loss) leaves status "running" forever; age lets the UI label such
                # zombies honestly instead of listing them as healthily active.
                "status_age_seconds": _file_age_seconds(status_path),
            }
    runs = [all_runs[k] for k in sorted(all_runs, reverse=True)]
    return {"runs": runs, "archives": [str(r) for r in root_list]}


def _run_payload(root: Path, run_id: str) -> dict:
    try:
        manifest = read_manifest(root, run_id)
    except FileNotFoundError:
        manifest = None
    status = _maybe_json(root / run_id / "status.json")
    if manifest is None and status is None:
        raise FileNotFoundError(run_id)
    return {
        "run_id": run_id,
        "manifest": manifest,
        "node_costs": _node_costs(manifest),
        "status": status,
        "events": _read_jsonl(root / run_id / "run-events.jsonl"),
    }


def _node_costs(manifest: dict | None) -> dict:
    """Per-node display costs via the SAME read-time normalization the rest of the tooling uses.

    The archive deliberately stores raw truth — Codex reports tokens, not dollars, so its nodes
    carry ``cost_usd: null``. usage/compare/eval already price those from the rate table at read
    time (flagged estimated); the dashboard used to read the manifest directly and showed $0.0000
    for whole Codex runs. One normalization, every surface.
    """
    if not manifest:
        return {}
    return {
        u.node_id: {
            "cost_usd": u.cost_usd,
            "estimated": u.cost_estimated,
            "credits": u.credits,
        }
        for u in normalize_manifest_usage(manifest)
    }


def _file_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _safe_segment(name: str) -> str:
    """Reject path segments that could escape the archive root (path traversal)."""
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError(f"invalid path segment: {name!r}")
    return name


def _run_cwd(root: Path, run_id: str) -> str | None:
    """The run's live workspace path, recorded in run-input.json — or ``None`` if unknown/gone.

    The bridge that lets the (archive-only) dashboard reach a live run's under-review documents and
    drop a decision back into its workspace.
    """
    data = _maybe_json(root / run_id / "run-input.json")
    cwd = (data or {}).get("cwd")
    return cwd if cwd and Path(cwd).is_dir() else None


def _review_payload(run_id: str, cwd: str | None) -> dict:
    """The pending file-review for a run: the waiting node and its documents as openable URLs."""
    request = read_review_request(cwd) if cwd else None
    if not request:
        return {"pending": False}
    documents = [
        {
            "path": doc.get("path"),
            "kind": doc.get("kind"),
            "url": f"/api/runs/{run_id}/review/doc?path={quote(str(doc.get('path', '')))}",
        }
        for doc in request.get("documents", [])
    ]
    return {"pending": True, "node_id": request.get("node_id"), "documents": documents}


def _safe_cwd_path(cwd: Path, raw_path: str) -> Path:
    """Resolve a document path under the run workspace, rejecting traversal outside it."""
    requested = unquote(raw_path)
    path = (cwd / requested).resolve()
    root = cwd.resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"invalid document path: {requested!r}")
    if not path.is_file():
        raise FileNotFoundError(requested)
    return path


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _maybe_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return _read_json(path)
    except json.JSONDecodeError:
        return None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    events = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"raw": line})
    return events


def _node_artifacts(node_dir: Path) -> dict:
    if not node_dir.is_dir():
        raise FileNotFoundError(node_dir)
    artifacts = []
    for path in sorted(node_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(node_dir).as_posix()
        artifacts.append(
            {
                "path": rel,
                "name": path.name,
                "size": path.stat().st_size,
                "kind": _artifact_kind(rel),
                "previewable": _is_previewable(path),
            }
        )
    return {"artifacts": artifacts}


def _safe_artifact_path(node_dir: Path, raw_path: str) -> Path:
    requested = unquote(raw_path)
    path = (node_dir / requested).resolve()
    root = node_dir.resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"invalid artifact path: {requested!r}")
    if not path.is_file():
        raise FileNotFoundError(requested)
    return path


def _artifact_kind(path: str) -> str:
    if path.endswith(".jsonl"):
        return "events"
    if path.endswith(".json"):
        return "json"
    if path.endswith(".md"):
        return "markdown"
    if path.endswith(".txt"):
        return "text"
    if path.startswith("aidlc-docs/"):
        return "aidlc"
    return "file"


def _is_previewable(path: Path) -> bool:
    return path.suffix.lower() in {".txt", ".md", ".json", ".jsonl", ".yaml", ".yml"}

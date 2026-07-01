"""Small localhost dashboard server backed by the run archive."""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from cadora.archive import list_runs, read_manifest
from cadora.usage import summarize_usage


def serve_dashboard(
    archive_dir: str = "runs",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Serve the local dashboard until interrupted.

    Binds localhost by default and has no authentication, so keep it on loopback or front
    it with TLS + authentication before exposing it beyond the host.
    """
    handler = make_handler(archive_dir)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"cadora dashboard: http://{host}:{httpd.server_port}/")
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


def make_handler(archive_dir: str | Path):
    archive_root = Path(archive_dir)

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
                    self._json(_runs_payload(archive_root))
                elif path == "/api/usage":
                    since = (query.get("since") or [None])[0]
                    self._json(summarize_usage(archive_root, since=since).to_dict())
                elif path.startswith("/api/runs/"):
                    self._run_api(path)
                else:
                    self.send_error(404, "not found")
            except FileNotFoundError:
                self.send_error(404, "not found")
            except ValueError as exc:
                self.send_error(400, str(exc))

        def log_message(self, format: str, *args) -> None:
            return

        def _run_api(self, path: str) -> None:
            parts = [unquote(p) for p in path.split("/") if p]
            if len(parts) < 3:
                self.send_error(404, "not found")
                return
            run_id = _safe_segment(parts[2])
            if len(parts) == 3:
                self._json(_run_payload(archive_root, run_id))
                return
            if parts[3] == "status" and len(parts) == 4:
                self._json(_read_json(archive_root / run_id / "status.json"))
                return
            if parts[3] == "events" and len(parts) == 4:
                self._json(_read_jsonl(archive_root / run_id / "run-events.jsonl"))
                return
            if len(parts) >= 6 and parts[3] == "nodes":
                node_id = _safe_segment(parts[4])
                node_dir = archive_root / run_id / node_id
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
            self.end_headers()
            self.wfile.write(data)

    return DashboardHandler


def _runs_payload(root: Path) -> dict:
    manifests = {m.get("run_id"): m for m in list_runs(root)}
    run_ids = set(manifests)
    if root.is_dir():
        run_ids.update(d.name for d in root.iterdir() if d.is_dir())
    runs = []
    for run_id in sorted(run_ids, reverse=True):
        run_dir = root / str(run_id)
        status = _maybe_json(run_dir / "status.json")
        manifest = manifests.get(run_id)
        runs.append(
            {
                "run_id": run_id,
                "manifest": manifest,
                "status": status,
                "active": bool(status and status.get("status") in {"created", "running"}),
            }
        )
    return {"runs": runs}


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
        "status": status,
        "events": _read_jsonl(root / run_id / "run-events.jsonl"),
    }


def _safe_segment(name: str) -> str:
    """Reject path segments that could escape the archive root (path traversal)."""
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError(f"invalid path segment: {name!r}")
    return name


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

"""Bearer-token auth for the MCP HTTP transport — a pure-ASGI unit check (no live server, no mcp)."""

import asyncio

from cadora.mcp.auth import bearer_auth, resolve_token


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _request(app, headers, scope_type="http"):
    scope = {"type": scope_type, "headers": headers}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def _status(sent):
    return next((m["status"] for m in sent if m["type"] == "http.response.start"), None)


def test_rejects_missing_token():
    guarded = bearer_auth(_ok_app, "s3cret")
    assert _status(_request(guarded, [(b"host", b"x")])) == 401


def test_rejects_wrong_token():
    guarded = bearer_auth(_ok_app, "s3cret")
    assert _status(_request(guarded, [(b"authorization", b"Bearer nope")])) == 401


def test_rejects_non_bearer_scheme():
    guarded = bearer_auth(_ok_app, "s3cret")
    assert _status(_request(guarded, [(b"authorization", b"Basic s3cret")])) == 401


def test_allows_correct_token():
    guarded = bearer_auth(_ok_app, "s3cret")
    assert _status(_request(guarded, [(b"authorization", b"Bearer s3cret")])) == 200


def test_non_http_scope_passes_through():
    reached = {"v": False}

    async def app(scope, receive, send):
        reached["v"] = True

    _request(bearer_auth(app, "s3cret"), [], scope_type="lifespan")
    assert reached["v"]  # lifespan/websocket scopes bypass the per-request header check


def test_resolve_token_prefers_explicit_then_env(monkeypatch):
    monkeypatch.delenv("CADORA_MCP_TOKEN", raising=False)
    assert resolve_token(None) is None
    monkeypatch.setenv("CADORA_MCP_TOKEN", "env-tok")
    assert resolve_token(None) == "env-tok"
    assert resolve_token("explicit") == "explicit"

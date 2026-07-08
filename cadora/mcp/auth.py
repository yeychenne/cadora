"""Bearer-token auth for the MCP HTTP transport.

The MCP server is loopback-only and unauthenticated by default (``cadora mcp`` refuses a
non-loopback bind unless you acknowledge that). To expose it over HTTP *safely*,
``cadora mcp --transport http --auth-token <token>`` (or ``CADORA_MCP_TOKEN``) wraps the ASGI app
in a middleware that requires ``Authorization: Bearer <token>`` on every HTTP request — a present
token also lifts the non-loopback refusal, since the surface is no longer unauthenticated.

Kept as a tiny standalone ASGI wrapper (no coupling to FastMCP's own auth stack) so it is stable
across ``mcp`` SDK versions and unit-testable without a live server. This is transport auth, not a
substitute for TLS — still front a network-exposed server with TLS.
"""

from __future__ import annotations

import hmac
import os

_BEARER = "Bearer "


def resolve_token(explicit: str | None) -> str | None:
    """The effective token: an explicit ``--auth-token`` wins, else ``CADORA_MCP_TOKEN``."""
    return explicit or os.environ.get("CADORA_MCP_TOKEN") or None


def _authorized(header: str, token: str) -> bool:
    if not header.startswith(_BEARER):
        return False
    return hmac.compare_digest(header[len(_BEARER):], token)  # constant-time


def bearer_auth(app, token: str):
    """Wrap an ASGI ``app`` so every HTTP request must carry ``Authorization: Bearer <token>``.

    Non-HTTP scopes (``lifespan``, ``websocket``) pass through untouched — the check is applied per
    HTTP request, which is where the MCP streamable-http endpoint lives.
    """

    async def guarded(scope, receive, send):
        if scope.get("type") != "http":
            await app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if not _authorized(auth, token):
            await _reject(send)
            return
        await app(scope, receive, send)

    return guarded


async def _reject(send) -> None:
    body = b'{"error":"unauthorized"}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})

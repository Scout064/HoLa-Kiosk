"""
Status-page proxy for the kiosk's second screen.

Some services (notably Uptime-Kuma) send `X-Frame-Options: SAMEORIGIN` on
their status pages, which makes Chromium refuse to embed them in the kiosk
page ("refused to connect" inside the frame). This proxy relays GET/HEAD
requests to the upstream status service and strips the frame-blocking
headers, so the kiosk page can embed it from its own host:

    iframe: http://127.0.0.1:8601/status/internal
             → proxied to  $STATUS_PROXY_TARGET/status/internal
               (default: http://10.0.0.10:3001)

Root-absolute asset paths (/icon.svg, /api/status-page/…, /socket.io
polling) hit the proxy origin and are relayed as well — the page renders
normally, and socket.io falls back to HTTP long-polling (no WebSocket
relay, which is fine for a glance screen).

Run (deploy/hola-status-proxy.service does this):
    STATUS_PROXY_TARGET=http://10.0.0.10:3001 \
    uvicorn status_proxy:app --host 127.0.0.1 --port 8601
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response

TARGET = os.environ.get("STATUS_PROXY_TARGET", "http://10.0.0.10:3001")
LISTEN_PORT = int(os.environ.get("STATUS_PROXY_PORT", "8601"))

app = FastAPI(title="HoLa Kiosk status proxy")

# headers that would block framing of the relayed page
STRIP_HEADERS = {
    "x-frame-options",
    "content-security-policy",
    "content-security-policy-report-only",
    "cross-origin-opener-policy",
    "cross-origin-embedder-policy",
}
# hop-by-hop / length headers — httpx already decoded the body
HOP_BY_HOP = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "host",
    "server",
    "date",
}

_client: httpx.AsyncClient | None = None


def filter_headers(headers: Any) -> dict[str, str]:
    """Drop frame-blocking and hop-by-hop headers, keep the rest."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in STRIP_HEADERS | HOP_BY_HOP
    }


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=TARGET, timeout=15.0, follow_redirects=True
        )
    return _client


@app.api_route("/{path:path}", methods=["GET", "HEAD"])
async def relay(path: str, request: Request) -> Response:
    headers = {
        "accept": request.headers.get("accept", "*/*"),
        "user-agent": request.headers.get("user-agent", ""),
    }
    try:
        upstream = await _get_client().request(
            request.method, f"/{path}",
            params=request.query_params, headers=headers,
        )
    except httpx.HTTPError as exc:
        return Response(
            status_code=502,
            content=f"status service unreachable: {exc}".encode(),
            media_type="text/plain",
        )
    relayed = filter_headers(upstream.headers)
    # the kiosk page probes us (HEAD) to light the "Status Page" dot —
    # allow that cross-origin read; the proxy binds to loopback only
    relayed["access-control-allow-origin"] = "*"
    return Response(
        status_code=upstream.status_code,
        content=upstream.content,
        headers=relayed,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=LISTEN_PORT)

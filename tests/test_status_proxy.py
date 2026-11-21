"""The status-page proxy: strips frame-blocking headers so the kiosk page
can embed services that send X-Frame-Options (Uptime-Kuma)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy"
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

import status_proxy  # noqa: E402
from status_proxy import STRIP_HEADERS, filter_headers  # noqa: E402


def _lower(headers: dict) -> dict:
    return {k.lower(): v for k, v in headers.items()}


def test_frame_blocking_headers_are_stripped():
    headers = httpx.Headers({
        "X-Frame-Options": "SAMEORIGIN",
        "Content-Security-Policy": "frame-ancestors 'none'",
        "Content-Type": "text/html",
        "Set-Cookie": "session=x",
    })
    kept = _lower(filter_headers(headers))
    assert "x-frame-options" not in kept
    assert "content-security-policy" not in kept
    assert kept["content-type"] == "text/html"
    # session cookies for the proxy origin pass through unharmed
    assert kept["set-cookie"] == "session=x"


def test_hop_by_hop_headers_are_stripped():
    headers = httpx.Headers({
        "Content-Encoding": "gzip",      # httpx already decoded the body
        "Content-Length": "123",
        "Transfer-Encoding": "chunked",
        "Connection": "keep-alive",
        "Server": "uvicorn",
    })
    assert filter_headers(headers) == {}


def _install_fake_upstream(status_code=200, body=b"<html>ok</html>",
                           extra_headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body, headers={
            "x-frame-options": "SAMEORIGIN",
            "content-type": "text/html",
            **(extra_headers or {}),
        })

    status_proxy._client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )


def _restore_client():
    status_proxy._client = None


async def test_relay_strips_x_frame_options():
    from fastapi.testclient import TestClient

    _install_fake_upstream()
    try:
        client = TestClient(status_proxy.app)
        response = client.get("/status/internal?theme=auto")

        assert response.status_code == 200
        assert response.text == "<html>ok</html>"
        assert response.headers["content-type"].startswith("text/html")
        assert "x-frame-options" not in response.headers
        # the kiosk page probes the proxy cross-origin for the reachability
        # dot — the proxy (loopback-only) allows that read
        assert response.headers["access-control-allow-origin"] == "*"
    finally:
        _restore_client()


async def test_relay_passes_status_and_errors_through():
    from fastapi.testclient import TestClient

    _install_fake_upstream(status_code=404, body=b"not found")
    try:
        client = TestClient(status_proxy.app)
        assert client.get("/status/missing").status_code == 404
    finally:
        _restore_client()


async def test_relay_reports_unreachable_upstream():
    from fastapi.testclient import TestClient

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    status_proxy._client = httpx.AsyncClient(
        base_url="http://downstream.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        client = TestClient(status_proxy.app)
        response = client.get("/status/internal")
        assert response.status_code == 502
        assert "unreachable" in response.text
    finally:
        _restore_client()


def test_strip_set_covers_the_known_blockers():
    assert "x-frame-options" in STRIP_HEADERS
    assert "content-security-policy" in STRIP_HEADERS

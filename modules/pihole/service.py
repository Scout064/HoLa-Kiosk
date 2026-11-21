"""
Connector for the Pi-hole v6 REST API (https://docs.pi-hole.net/api/).

Endpoints used and why:
- POST /api/auth         → session login ({"password": …} → SID); only used
                           when a password is configured. Re-login on 401.
- GET  /api/stats/summary → query/block statistics in one call
- GET  /api/dns/blocking  → is blocking actually enabled?
- GET  /api/dns/blocking (unauthenticated) for the health probe: any HTTP
  response — even a 401 — proves the server is reachable.

Auth: session SID sent as `X-FTL-SID` header (no CSRF token needed for GETs).
With no password configured, the v6 API is open and no login is performed.
"""

from __future__ import annotations

import httpx


class PiholeService:
    def __init__(self, base_url: str, password: str = "", timeout: float = 10.0,
                 verify_ssl: bool = False,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._password = password
        self._sid: str | None = None
        # verify_ssl: Pi-hole commonly serves its API over HTTPS with a
        # self-signed certificate — kiosk/LAN deployments disable verification
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout,
            verify=verify_ssl, transport=transport
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── auth ───────────────────────────────────────────────────────────────

    async def _login(self) -> None:
        if not self._password:
            return  # open API, nothing to do
        response = await self._client.post("/api/auth", json={"password": self._password})
        if response.status_code != 200:
            raise RuntimeError(
                f"Pi-hole login failed (HTTP {response.status_code})"
            )
        session = response.json().get("session") or {}
        self._sid = session.get("sid")
        if not self._sid:
            raise RuntimeError("Pi-hole login response contained no SID")

    async def _request(
        self, method: str, path: str, *, _retried: bool = False, **kwargs
    ) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        if self._sid:
            headers["X-FTL-SID"] = self._sid
        response = await self._client.request(method, path, headers=headers, **kwargs)
        if response.status_code == 401 and not _retried and self._password:
            await self._login()  # session expired — refresh once
            return await self._request(method, path, _retried=True, **kwargs)
        response.raise_for_status()
        return response

    # ── API calls ────────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Any HTTP response (even 401) proves the server is reachable."""
        try:
            await self._client.get("/api/dns/blocking")
            return True
        except httpx.HTTPError:
            return False

    async def summary(self) -> dict:
        return (await self._request("GET", "/api/stats/summary")).json()

    async def blocking_status(self) -> dict:
        return (await self._request("GET", "/api/dns/blocking")).json()

    # ── tops for the detail page (some builds lack these endpoints —
    #    callers tolerate failures) ─────────────────────────────────────

    async def top_domains(self, limit: int = 10) -> list[dict]:
        body = (await self._request("GET", "/api/stats/top_domains")).json()
        return (body.get("domains") or [])[:limit]

    async def top_clients(self, limit: int = 10) -> list[dict]:
        body = (await self._request("GET", "/api/stats/top_clients")).json()
        return (body.get("clients") or [])[:limit]

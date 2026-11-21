"""Pi-hole module: v6 API auth flow (SID), stats parsing, widget build."""

from __future__ import annotations

import httpx
import pytest

from app.models import WidgetTypes
from pihole.plugin import PiholeDataSource
from pihole.service import PiholeService

SUMMARY = {
    "queries": {"total": 12345, "blocked": 891, "percentBlocked": 7.216789},
    "clients": {"active": 7, "total": 12},
    "gravity": {"domains_being_blocked": 1_234_567},
}

SUMMARY_SNAKE_CASE = {
    # Pi-hole v6 builds exist that return snake_case keys instead
    "queries": {"total": 999, "blocked": 50, "percent_blocked": 5.0},
    "clients": {"active": 3},
    "gravity": {"domains_being_blocked": 100},
}


def _make_service(password: str = "pw", blocking: bool = True,
                  summary: dict | None = None) -> PiholeService:
    """
    A PiholeService against a mocked Pi-hole v6 API.

    Models the v6 semantics the service must handle:
    - with a password set, auth'd endpoints require a valid SID,
    - SIDs are single-use (each is invalidated right after a successful call),
      so a second call must transparently re-login.
    """
    state = {"logins": 1, "sid": "SID-1", "uses": 0}
    require_auth = bool(password)
    summary = SUMMARY if summary is None else summary

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth":
            state["logins"] += 1
            state["sid"] = f"SID-{state['logins']}"
            return httpx.Response(200, json={
                "session": {"valid": True, "sid": state["sid"], "csrf": "CSRF"},
                "took": 0.001,
            })
        if require_auth and request.headers.get("X-FTL-SID") != state["sid"]:
            return httpx.Response(401, json={
                "error": {"key": "unauthorized", "message": "Unauthorized",
                          "hint": None},
            })
        state["sid"] = "EXPIRED"  # single-use: works once, then expires
        if request.url.path == "/api/stats/summary":
            return httpx.Response(200, json=summary)
        if request.url.path == "/api/dns/blocking":
            return httpx.Response(200, json={"blocking": blocking})
        return httpx.Response(404, json={"error": {"key": "not_found"}})

    return PiholeService(
        "http://pi.hole", password=password, transport=httpx.MockTransport(handler)
    )


async def test_first_call_logs_in_lazily():
    service = _make_service()
    summary = await service.summary()
    assert summary["queries"]["total"] == 12345
    assert service._sid == "SID-2"


async def test_expired_session_is_renewed():
    service = _make_service()

    first = await service.summary()           # login → SID-2 (single-use)
    second = await service.summary()          # SID-2 now stale → 401 → re-login

    assert first["queries"]["total"] == 12345
    assert second["queries"]["total"] == 12345
    assert service._sid == "SID-3"


async def test_health_check_any_response_means_reachable():
    service = _make_service()
    assert await service.health_check() is True


async def test_health_check_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    service = PiholeService(
        "http://127.0.0.1:1", transport=httpx.MockTransport(handler)
    )
    assert await service.health_check() is False


async def test_plugin_collect_builds_widget():
    plugin = PiholeDataSource()
    await plugin.setup({"base_url": "http://pi.hole", "password": "pw"})

    # inject the mocked service exactly as the backend would have built it
    plugin.service = _make_service(blocking=True)

    assert await plugin.health_check() is True
    widgets = await plugin.collect()
    assert len(widgets) == 1
    widget = widgets[0]
    assert widget.id == "pihole-stats"
    assert widget.type == WidgetTypes.METRIC_LIST

    entries = {
        entry["label"]: entry
        for entry in widget.data["groups"][0]["entries"]
    }
    assert entries["Queries (24h)"]["value"] == 12345
    assert entries["Block rate"]["value"] == 7.2          # rounded to one decimal
    assert entries["Blocklist size"]["value"] == 1_234_567
    assert entries["Blocking"]["value"] == "enabled"
    assert entries["Blocking"]["status"] == "online"


async def test_plugin_collect_flags_disabled_blocking():
    plugin = PiholeDataSource()
    await plugin.setup({"base_url": "http://pi.hole", "password": "pw"})
    plugin.service = _make_service(blocking=False)

    widgets = await plugin.collect()
    entries = {
        entry["label"]: entry
        for entry in widgets[0].data["groups"][0]["entries"]
    }
    assert entries["Blocking"]["value"] == "disabled"
    assert entries["Blocking"]["status"] == "offline"


async def test_plugin_collect_with_open_api():
    """No password configured — the v6 API is open, no login ever happens."""
    plugin = PiholeDataSource()
    await plugin.setup({"base_url": "http://pi.hole", "password": ""})
    plugin.service = _make_service(password="")

    widgets = await plugin.collect()
    entries = {
        entry["label"]: entry
        for entry in widgets[0].data["groups"][0]["entries"]
    }
    assert entries["Queries (24h)"]["value"] == 12345
    assert plugin.service._sid is None


async def test_plugin_setup_requires_base_url():
    plugin = PiholeDataSource()
    with pytest.raises(ValueError, match="base_url"):
        await plugin.setup({})


async def test_plugin_forwards_verify_ssl(monkeypatch):
    """HTTPS with self-signed certs is the Pi-hole norm — verification is
    off by default, on when explicitly configured."""
    captured: dict = {}

    class SpyService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def health_check(self):
            return True

        async def aclose(self):
            pass

    monkeypatch.setattr("pihole.plugin.PiholeService", SpyService)
    plugin = PiholeDataSource()

    await plugin.setup({"base_url": "https://pi.hole", "password": "x"})
    assert captured["verify_ssl"] is False

    await plugin.setup({"base_url": "https://pi.hole", "verify_ssl": True})
    assert captured["verify_ssl"] is True


async def test_plugin_accepts_snake_case_summary():
    """Some Pi-hole v6 builds return percent_blocked instead of
    percentBlocked — both must work."""
    plugin = PiholeDataSource()
    await plugin.setup({"base_url": "http://pi.hole", "password": "pw"})
    plugin.service = _make_service(summary=SUMMARY_SNAKE_CASE)

    widgets = await plugin.collect()
    entries = {
        entry["label"]: entry
        for entry in widgets[0].data["groups"][0]["entries"]
    }
    assert entries["Queries (24h)"]["value"] == 999
    assert entries["Block rate"]["value"] == 5.0


TOP_DOMAINS = [
    {"domain": "nc.example.com", "count": 2977},
    {"domain": "dndwiki.example.com", "count": 2873},
]
TOP_CLIENTS = [
    {"name": "", "ip": "198.51.100.9", "count": 23662},
    {"name": "monitor.example.com", "ip": "198.51.100.4", "count": 18689},
]


def _make_service_with_tops(password: str = "pw") -> PiholeService:
    state = {"logins": 1, "sid": "SID-1"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth":
            state["logins"] += 1
            state["sid"] = f"SID-{state['logins']}"
            return httpx.Response(200, json={
                "session": {"valid": True, "sid": state["sid"], "csrf": "CSRF"},
            })
        if request.headers.get("X-FTL-SID") != state["sid"]:
            return httpx.Response(401, json={"error": {"key": "unauthorized"}})
        state["sid"] = "EXPIRED"
        if request.url.path == "/api/stats/summary":
            return httpx.Response(200, json=SUMMARY)
        if request.url.path == "/api/dns/blocking":
            return httpx.Response(200, json={"blocking": True})
        if request.url.path == "/api/stats/top_domains":
            return httpx.Response(200, json={"domains": TOP_DOMAINS})
        if request.url.path == "/api/stats/top_clients":
            return httpx.Response(200, json={"clients": TOP_CLIENTS})
        return httpx.Response(404, json={"error": {"key": "not_found"}})

    return PiholeService(
        "http://pi.hole", password=password, transport=httpx.MockTransport(handler)
    )


async def test_collect_includes_top_lists_for_detail_page():
    plugin = PiholeDataSource()
    await plugin.setup({"base_url": "http://pi.hole", "password": "pw"})
    plugin.service = _make_service_with_tops()

    widgets = await plugin.collect()
    detail = widgets[0].data["detail"]
    assert detail["top_domains"][0]["domain"] == "nc.example.com"
    assert detail["top_domains"][0]["count"] == 2977
    assert detail["top_clients"][0]["ip"] == "198.51.100.9"
    assert detail["top_clients"][1]["name"] == "monitor.example.com"


async def test_collect_survives_missing_tops_endpoints():
    """Older Pi-hole builds 404 on the tops endpoints — the widget must
    still be served (with empty detail), not fail."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/stats/summary":
            return httpx.Response(200, json=SUMMARY)
        if request.url.path == "/api/dns/blocking":
            return httpx.Response(200, json={"blocking": True})
        return httpx.Response(404, json={"error": {"key": "not_found"}})

    plugin = PiholeDataSource()
    await plugin.setup({"base_url": "http://pi.hole", "password": ""})
    plugin.service = PiholeService(
        "http://pi.hole", password="", transport=httpx.MockTransport(handler)
    )

    widgets = await plugin.collect()
    data = widgets[0].data
    assert data["detail"] == {}
    assert data["groups"][0]["entries"][0]["value"] == 12345

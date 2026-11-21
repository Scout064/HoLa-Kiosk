"""
Pi-hole module — DNS filtering stats for the kiosk screen.

Widget:
- "pihole-stats" (metric-list): queries/blocked/block-rate, blocklist size,
  active clients, and whether blocking is actually enabled.

Config (settings.yaml → plugins: pihole:):
    base_url, password (empty = open API), poll_interval_seconds
"""

from __future__ import annotations

from typing import Any

from app.models import MetricEntry, MetricGroup, Widget, WidgetSpec, WidgetTypes
from app.plugins.base import DataSource
from pihole.service import PiholeService


def _round1(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 1)
    return value


class PiholeDataSource(DataSource):
    name = "pihole"
    display_name = "Pi-hole"
    version = "0.1.0"
    description = "Pi-hole DNS filtering stats: queries, blocked share, blocking status."
    config_key = "pihole"
    default_poll_interval_seconds = 30

    def __init__(self) -> None:
        self.service: PiholeService | None = None

    async def setup(self, config: dict[str, Any]) -> None:
        base_url = config.get("base_url")
        if not base_url:
            raise ValueError(
                "Missing Pi-hole config key: base_url "
                "(set it under plugins:pihole: in config/settings.yaml)"
            )
        self.service = PiholeService(
            base_url=str(base_url),
            password=str(config.get("password") or ""),
            # Pi-hole's HTTPS is usually self-signed — verify off by default
            verify_ssl=bool(config.get("verify_ssl", False)),
        )

    async def health_check(self) -> bool:
        assert self.service is not None
        return await self.service.health_check()

    async def teardown(self) -> None:
        if self.service is not None:
            await self.service.aclose()
            self.service = None

    def describe_widgets(self) -> list[WidgetSpec]:
        return [
            WidgetSpec(id="pihole-stats", type=WidgetTypes.METRIC_LIST,
                       title="Pi-hole", span=1),
        ]

    async def collect(self) -> list[Widget]:
        assert self.service is not None
        summary = await self.service.summary()
        blocking = await self.service.blocking_status()

        queries = summary.get("queries") or {}
        clients = summary.get("clients") or {}
        gravity = summary.get("gravity") or {}
        blocking_enabled = bool(blocking.get("blocking"))
        # Pi-hole v6 summary JSONs exist in both spellings — camelCase
        # (percentBlocked) and snake_case (percent_blocked) — accept either
        percent_blocked = queries.get(
            "percentBlocked", queries.get("percent_blocked")
        )

        group = MetricGroup(
            label="Pi-hole",
            entries=[
                MetricEntry(label="Queries (24h)", value=queries.get("total")),
                MetricEntry(label="Blocked (24h)", value=queries.get("blocked")),
                MetricEntry(label="Block rate",
                            value=_round1(percent_blocked), unit="%"),
                MetricEntry(label="Active clients", value=clients.get("active")),
                MetricEntry(label="Blocklist size",
                            value=gravity.get("domains_being_blocked")),
                MetricEntry(
                    label="Blocking",
                    value="enabled" if blocking_enabled else "disabled",
                    status="online" if blocking_enabled else "offline",
                ),
            ],
        )

        return [
            Widget(
                id="pihole-stats",
                type=WidgetTypes.METRIC_LIST,
                title="Pi-hole",
                span=1,
                data={"groups": [group.model_dump()], "detail": await self._tops()},
            ),
        ]

    async def _tops(self) -> dict[str, Any]:
        """Top domains/clients for the detail page. Older Pi-hole builds
        lack these endpoints — failures yield an empty dict, the widget
        itself stays healthy."""
        assert self.service is not None
        detail: dict[str, Any] = {}
        try:
            detail["top_domains"] = await self.service.top_domains(10)
        except Exception:
            pass
        try:
            detail["top_clients"] = await self.service.top_clients(10)
        except Exception:
            pass
        return detail

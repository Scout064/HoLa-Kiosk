"""
Kiosk Pi module — the status screen's own host: CPU temperature, load,
RAM, root disk and uptime. Always available (local reads), so the screen
never loses its "self" tile — and shows the Pi's temperature next to the
Proxmox host sensors as the kiosk brief requires.

Widget:
- "kiosk-pi" (resource-tiles): one tile with RAM/disk bars + uptime;
  temperature and load live in tile.info (cpu_temp_c, load1) for the kiosk
  page to color by threshold.

Config (settings.yaml → plugins: rpi:) — all optional:
    poll_interval_seconds, thermal_zone (path override)
"""

from __future__ import annotations

import socket
from typing import Any

from app.models import ResourceTile, Widget, WidgetSpec, WidgetTypes
from app.plugins.base import DataSource
from rpi.service import DEFAULT_PROC_STAT, DEFAULT_THERMAL_ZONE, RpiService


class RpiDataSource(DataSource):
    name = "rpi"
    display_name = "Kiosk Pi"
    version = "0.1.0"
    description = "The kiosk Pi itself: CPU temperature, load, RAM, disk, uptime."
    config_key = "rpi"
    default_poll_interval_seconds = 30

    def __init__(self) -> None:
        self.service: RpiService | None = None

    async def setup(self, config: dict[str, Any]) -> None:
        self.service = RpiService(
            thermal_zone=str(config.get("thermal_zone") or DEFAULT_THERMAL_ZONE),
            proc_stat=str(config.get("proc_stat") or DEFAULT_PROC_STAT),
        )
        # baseline for the CPU utilization average (first collect then has a
        # full interval delta instead of showing "—")
        await self.service.cpu_pct()

    async def health_check(self) -> bool:
        return True  # local sysfs reads — always reachable

    async def teardown(self) -> None:
        self.service = None

    def describe_widgets(self) -> list[WidgetSpec]:
        return [
            WidgetSpec(id="kiosk-pi", type=WidgetTypes.RESOURCE_TILES,
                       title="Kiosk Pi", span=1),
        ]

    async def collect(self) -> list[Widget]:
        assert self.service is not None
        data = await self.service.read()

        info: dict[str, Any] = {}
        if data.get("cpu_temp_c") is not None:
            info["cpu_temp_c"] = data["cpu_temp_c"]
        if data.get("load1") is not None:
            info["load1"] = data["load1"]

        tile = ResourceTile(
            id=socket.gethostname(),
            label=socket.gethostname(),
            status="online",  # it is running if it can answer this
            cpu_pct=data.get("cpu_pct"),   # average CPU load % over the poll interval
            ram_pct=data.get("ram_pct"),
            disk_pct=data.get("disk_pct"),
            uptime_seconds=data.get("uptime_seconds"),
            info=info,
        )

        return [
            Widget(
                id="kiosk-pi",
                type=WidgetTypes.RESOURCE_TILES,
                title="Kiosk Pi",
                span=1,
                data={"tiles": [tile.model_dump()]},
            ),
        ]

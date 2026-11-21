"""
Network hosts module — reachability of gateway, WAN and other homelab hosts,
probed directly from the kiosk Pi (independent of every monitored service).

Widget:
- "network-hosts" (device-table): one row per configured check with
  online/offline status and latency.

Config (settings.yaml → plugins: hosts:):
    poll_interval_seconds, ping_timeout_seconds
    checks:                 # name + host required
      - {name: "Gateway", host: "10.0.0.254", role: "gateway"}
      - {name: "WAN", host: "1.1.1.1", role: "wan"}
      - {name: "Some service", host: "10.0.0.30", port: 443}   # TCP check
Optional keys per check: port (TCP instead of ICMP), role (display category).
Speedtest (once per hour, attached to the WAN check, cached between runs):
    speedtest_interval_seconds: 3600     # 0 disables
    speedtest_command: ""                 # auto-detects speedtest-cli in the
                                          # backend venv; override for another
                                          # speedtest-cli-compatible binary
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from app.models import DeviceRecord, Widget, WidgetSpec, WidgetTypes
from app.plugins.base import DataSource
from hosts.service import HostsService


@dataclass
class _Check:
    name: str
    host: str
    port: int | None
    role: str


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "host"


class HostsDataSource(DataSource):
    name = "hosts"
    display_name = "Network Hosts"
    version = "0.1.0"
    description = "Direct ping/TCP reachability checks for gateway, WAN and homelab hosts."
    config_key = "hosts"
    default_poll_interval_seconds = 30

    def __init__(self) -> None:
        self.service: HostsService | None = None
        self._checks: list[_Check] = []
        # speedtest (slow background probe — see service docstring)
        self._speedtest_task: asyncio.Task | None = None
        self._speedtest_interval_seconds: float = 3600.0
        self._speedtest_command: str | None = None
        self._speedtest_cache: dict[str, Any] | None = None
        self._speedtest_error: str | None = None

    async def setup(self, config: dict[str, Any]) -> None:
        raw_checks = config.get("checks")
        if not isinstance(raw_checks, list) or not raw_checks:
            raise ValueError(
                "No hosts configured — add checks: under plugins:hosts: in "
                "config/settings.yaml (e.g. Gateway and WAN)"
            )
        checks: list[_Check] = []
        for index, entry in enumerate(raw_checks):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            host = str(entry.get("host") or "").strip()
            if not name or not host:
                continue
            port = entry.get("port")
            checks.append(_Check(
                name=name,
                host=host,
                port=int(port) if port else None,
                role=str(entry.get("role") or "host"),
            ))
        if not checks:
            raise ValueError("hosts: checks contains no usable entries (name + host each)")
        self._checks = checks
        self.service = HostsService(
            ping_timeout_seconds=float(config.get("ping_timeout_seconds", 2.0)),
        )
        self._start_speedtest_task(config)

    def _start_speedtest_task(self, config: dict[str, Any]) -> None:
        """Hourly bandwidth measurement attached to the WAN check (role wan).

        Skipped without a WAN check, when disabled via
        speedtest_interval_seconds: 0, or when no speedtest binary exists
        (warned once — the module keeps working without it).
        """
        from datetime import datetime, timezone

        interval = config.get("speedtest_interval_seconds", 3600)
        try:
            interval = float(interval)
        except (TypeError, ValueError):
            interval = 3600.0
        self._speedtest_command = (
            str(config["speedtest_command"]) if config.get("speedtest_command") else None
        )
        has_wan = any(check.role == "wan" for check in self._checks)
        if not has_wan or interval <= 0:
            return

        from hosts.service import resolve_speedtest_command
        if not resolve_speedtest_command(self._speedtest_command):
            import structlog
            structlog.get_logger().warning(
                "speedtest_skipped",
                reason="speedtest-cli not found (pip install speedtest-cli)",
            )
            return

        self._speedtest_interval_seconds = interval
        self._speedtest_task = asyncio.create_task(
            self._speedtest_loop(), name="hola-speedtest"
        )

    async def _speedtest_loop(self) -> None:
        import structlog
        from datetime import datetime, timezone

        log = structlog.get_logger()
        # let the boot settle before the first full-bandwidth test
        await asyncio.sleep(30)
        while True:
            try:
                assert self.service is not None
                result = await self.service.run_speedtest(self._speedtest_command)
                self._speedtest_cache = {
                    **result,
                    "speedtest_at": datetime.now(timezone.utc).isoformat(),
                }
                self._speedtest_error = None
                log.info("speedtest_result",
                         down_mbps=result["down_mbps"],
                         up_mbps=result["up_mbps"],
                         server=result.get("server"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # keep the last good result; surface the failure briefly
                self._speedtest_error = f"{type(exc).__name__}: {exc}"
                log.warning("speedtest_failed", error=self._speedtest_error)
            await asyncio.sleep(self._speedtest_interval_seconds)

    async def health_check(self) -> bool:
        # Probes are local; the module is healthy once set up.
        return self.service is not None

    async def teardown(self) -> None:
        if self._speedtest_task is not None:
            self._speedtest_task.cancel()
            try:
                await self._speedtest_task
            except (asyncio.CancelledError, Exception):
                pass
            self._speedtest_task = None
        self.service = None

    def describe_widgets(self) -> list[WidgetSpec]:
        return [
            WidgetSpec(id="network-hosts", type=WidgetTypes.DEVICE_TABLE,
                       title="Network", span=2),
        ]

    async def collect(self) -> list[Widget]:
        assert self.service is not None
        results = await asyncio.gather(
            *(self._run_check(check) for check in self._checks)
        )
        records = [record.model_dump() for record in results]

        # attach the cached speedtest result to the WAN row (role: wan)
        for record in records:
            if record.get("category") != "wan":
                continue
            metrics = record.setdefault("metrics", {})
            if self._speedtest_cache:
                metrics.update(self._speedtest_cache)
            if self._speedtest_error and not self._speedtest_cache:
                metrics["speedtest_error"] = self._speedtest_error

        return [
            Widget(
                id="network-hosts",
                type=WidgetTypes.DEVICE_TABLE,
                title="Network",
                span=2,
                data={"devices": records},
            ),
        ]

    async def _run_check(self, check: _Check) -> DeviceRecord:
        assert self.service is not None
        if check.port:
            reachable, latency_ms = await self.service.tcp_check(check.host, check.port)
            method = f"tcp/{check.port}"
        else:
            reachable, latency_ms = await self.service.ping(check.host)
            method = "icmp"

        metrics: dict[str, Any] = {"probe": method}
        if latency_ms is not None:
            metrics["latency_ms"] = latency_ms

        return DeviceRecord(
            id=_slug(check.name),
            name=check.name,
            category=check.role,
            status="online" if reachable else "offline",
            ip=check.host,
            tags=[check.role],
            metrics=metrics,
        )

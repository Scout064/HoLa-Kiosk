"""
Local kiosk Pi health — read straight from the kernel: CPU temperature
(sysfs thermal zone), memory (/proc/meminfo), root filesystem, uptime
(/proc/uptime), load average.

Purely local file reads: this module keeps reporting while every network
service is down (which is exactly when a status screen is most useful).
On non-Linux dev machines the files are absent and the values degrade to
None instead of failing.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

DEFAULT_THERMAL_ZONE = "/sys/class/thermal/thermal_zone0/temp"
DEFAULT_MEMINFO = "/proc/meminfo"
DEFAULT_UPTIME = "/proc/uptime"
DEFAULT_PROC_STAT = "/proc/stat"


class RpiService:
    def __init__(
        self,
        thermal_zone: str = DEFAULT_THERMAL_ZONE,
        meminfo: str = DEFAULT_MEMINFO,
        uptime_file: str = DEFAULT_UPTIME,
        proc_stat: str = DEFAULT_PROC_STAT,
        root: str = "/",
    ) -> None:
        self.thermal_zone = Path(thermal_zone)
        self.meminfo = Path(meminfo)
        self.uptime_file = Path(uptime_file)
        self.proc_stat = Path(proc_stat)
        self.root = root
        self._last_stat: tuple[int, int] | None = None  # (busy, total) jiffies

    async def health_check(self) -> bool:
        return True  # local sysfs — always "reachable"

    # ── readers (each defensive: None when the source is absent) ──────────

    async def _read_text(self, path: Path) -> str | None:
        def _read() -> str | None:
            try:
                return path.read_text().strip()
            except OSError:
                return None
        return await asyncio.to_thread(_read)

    async def cpu_temp_c(self) -> float | None:
        raw = await self._read_text(self.thermal_zone)
        if raw is None or not raw.isdigit():
            return None
        try:
            return round(int(raw) / 1000.0, 1)  # sysfs reports millidegrees
        except ValueError:
            return None

    async def ram_pct(self) -> float | None:
        raw = await self._read_text(self.meminfo)
        if raw is None:
            return None
        fields: dict[str, int] = {}
        for line in raw.splitlines():
            parts = line.split(":", 1)
            if len(parts) == 2:
                try:
                    fields[parts[0]] = int(parts[1].strip().split()[0])  # kB
                except (ValueError, IndexError):
                    continue
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable", fields.get("MemFree"))
        if not total or available is None:
            return None
        return round((total - available) / total * 100.0, 1)

    async def disk_pct(self) -> float | None:
        def _usage() -> float | None:
            try:
                usage = shutil.disk_usage(self.root)
            except OSError:
                return None
            return round((usage.total - usage.free) / usage.total * 100.0, 1)
        return await asyncio.to_thread(_usage)

    async def uptime_seconds(self) -> int | None:
        raw = await self._read_text(self.uptime_file)
        if raw is None:
            return None
        try:
            return int(float(raw.split()[0]))
        except (ValueError, IndexError):
            return None

    # ── average CPU load (utilization % over the poll interval) ────────────

    def _read_stat_sync(self) -> tuple[int, int] | None:
        """Parse /proc/stat's first line → (busy, total) jiffies."""
        try:
            first = self.proc_stat.read_text().splitlines()[0]
        except (OSError, IndexError):
            return None
        fields = first.split()
        # cpu user nice system idle iowait irq softirq steal
        try:
            values = [int(v) for v in fields[1:9]]
        except (ValueError, IndexError):
            return None
        if len(values) < 5:
            return None
        total = sum(values[:8]) if len(values) >= 8 else sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
        return (total - idle, total)

    async def cpu_pct(self) -> float | None:
        """
        Average CPU utilization in % since the previous call — the delta of
        /proc/stat counters (i.e. averaged over the poll interval). The first
        call only stores the baseline and returns None.
        """
        sample = await asyncio.to_thread(self._read_stat_sync)
        if sample is None:
            self._last_stat = None
            return None
        if self._last_stat is None:
            self._last_stat = sample
            return None
        busy_delta = sample[0] - self._last_stat[0]
        total_delta = sample[1] - self._last_stat[1]
        self._last_stat = sample
        if total_delta <= 0:
            return None
        return round(busy_delta / total_delta * 100.0, 1)

    def load1(self) -> float | None:
        try:
            return round(os.getloadavg()[0], 2)
        except OSError:
            return None

    async def read(self) -> dict[str, Any]:
        temp_c, ram_pct, disk_pct, uptime, cpu_pct = await asyncio.gather(
            self.cpu_temp_c(), self.ram_pct(), self.disk_pct(),
            self.uptime_seconds(), self.cpu_pct(),
        )
        return {
            "cpu_temp_c": temp_c,
            "cpu_pct": cpu_pct,
            "ram_pct": ram_pct,
            "disk_pct": disk_pct,
            "uptime_seconds": uptime,
            "load1": self.load1(),
        }

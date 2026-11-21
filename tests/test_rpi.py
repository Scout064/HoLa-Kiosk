"""Kiosk Pi module: sysfs/proc parsing with injected fake files."""

from __future__ import annotations

from pathlib import Path

from app.models import WidgetTypes
from rpi.plugin import RpiDataSource
from rpi.service import RpiService


def _write(path: Path, content: str) -> str:
    path.write_text(content)
    return str(path)


async def test_service_parses_local_sources(tmp_path):
    thermal = _write(tmp_path / "temp", "45600")             # millidegrees
    meminfo = _write(tmp_path / "meminfo",
                     "MemTotal:       1000000 kB\n"
                     "MemAvailable:    250000 kB\n")
    uptime = _write(tmp_path / "uptime", "100050.12 800.0\n")

    service = RpiService(
        thermal_zone=thermal,
        meminfo=meminfo,
        uptime_file=uptime,
        root=str(tmp_path),
    )
    data = await service.read()

    assert data["cpu_temp_c"] == 45.6
    assert data["ram_pct"] == 75.0
    assert data["uptime_seconds"] == 100050
    assert data["disk_pct"] is not None and data["disk_pct"] >= 0
    assert data["load1"] is None or data["load1"] >= 0


async def test_service_degrades_on_missing_sources(tmp_path):
    service = RpiService(
        thermal_zone=str(tmp_path / "missing-temp"),
        meminfo=str(tmp_path / "missing-meminfo"),
        uptime_file=str(tmp_path / "missing-uptime"),
        proc_stat=str(tmp_path / "missing-stat"),
        root=str(tmp_path),
    )
    data = await service.read()
    assert data["cpu_temp_c"] is None
    assert data["ram_pct"] is None
    assert data["uptime_seconds"] is None
    assert data["cpu_pct"] is None
    assert await service.health_check() is True


def _write_proc_stat(path: Path, busy: int, idle: int) -> None:
    # /proc/stat first line: user nice system idle iowait irq softirq steal
    path.write_text(
        f"cpu  {busy} 0 0 {idle} 0 0 0 0\n"
        "cpu0 1 0 0 1 0 0 0 0\n"
    )


async def test_cpu_pct_averages_over_two_samples(tmp_path):
    stat = tmp_path / "stat"
    service = RpiService(proc_stat=str(stat))

    _write_proc_stat(stat, busy=1000, idle=3000)          # total 4000
    first = await service.cpu_pct()
    assert first is None                                   # baseline only

    _write_proc_stat(stat, busy=1500, idle=3500)          # +500 busy, +500 idle
    second = await service.cpu_pct()
    # delta: busy +500, total +1000 → 50.0 %
    assert second == 50.0


async def test_cpu_pct_survives_counter_wraparound(tmp_path):
    stat = tmp_path / "stat"
    service = RpiService(proc_stat=str(stat))
    _write_proc_stat(stat, busy=1000, idle=3000)
    await service.cpu_pct()
    # pathological: no delta → None, not a crash or bogus number
    assert await service.cpu_pct() is None


async def test_cpu_pct_resets_on_unreadable_source(tmp_path):
    stat = tmp_path / "stat"                    # not yet created → unreadable
    service = RpiService(proc_stat=str(stat))
    assert await service.cpu_pct() is None      # missing → baseline reset
    _write_proc_stat(stat, busy=10, idle=10)    # source comes back
    assert await service.cpu_pct() is None      # fresh baseline
    _write_proc_stat(stat, busy=20, idle=20)    # busy +10, idle +10 → 50%
    assert await service.cpu_pct() == 50.0


async def test_plugin_collect_uses_configured_thermal_zone(tmp_path):
    thermal = tmp_path / "temp"
    thermal.write_text("51200")

    plugin = RpiDataSource()
    await plugin.setup({"thermal_zone": str(thermal)})

    assert await plugin.health_check() is True
    widgets = await plugin.collect()

    assert len(widgets) == 1
    widget = widgets[0]
    assert widget.id == "kiosk-pi"
    assert widget.type == WidgetTypes.RESOURCE_TILES

    tile = widget.data["tiles"][0]
    assert tile["status"] == "online"
    assert tile["info"]["cpu_temp_c"] == 51.2
    # ram/uptime come from /proc — absent on dev machines, tolerated on the Pi
    assert "ram_pct" in tile and "disk_pct" in tile
    assert "uptime_seconds" in tile


async def test_plugin_describe_widgets():
    plugin = RpiDataSource()
    specs = plugin.describe_widgets()
    assert [spec.id for spec in specs] == ["kiosk-pi"]

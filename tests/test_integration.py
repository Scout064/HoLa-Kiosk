"""
End-to-end integration: the kiosk modules load through the REAL HoLa-Dash
registry (the reuse decision) and the isolation guarantees hold:

- local modules (rpi, hosts with a loopback check) collect successfully,
- unreachable/misconfigured modules (pihole, proxmox) degrade their widgets
  to "unavailable" with the error message — the kiosk keeps working,
- disabled modules are skipped but reported.

This mirrors the kiosk's worst case: Proxmox down / unconfigured → its tiles
say UNREACHABLE, everything else keeps ticking.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models import WidgetStatus
from app.plugins.registry import PluginRegistry
from conftest import DASH_BACKEND, MODULES_ROOT


def _kiosk_settings(**overrides) -> Settings:
    plugins = {
        "directories": [str(DASH_BACKEND / "plugins"), str(MODULES_ROOT)],
        "disabled": ["zynk", "homeassistant", "prometheus"],
        # deliberately unconfigured → setup must fail gracefully
        "proxmox": {},
        # unreachable endpoint → collect must fail gracefully
        "pihole": {"base_url": "http://127.0.0.1:1", "password": ""},
        # local checks → must succeed
        "hosts": {"checks": [{"name": "Loopback", "host": "127.0.0.1"}],
                  "ping_timeout_seconds": 2},
        # injected below: thermal_zone → deterministic temp on any platform
        "rpi": {},
    }
    plugins.update(overrides)
    return Settings(plugins=plugins)


async def test_kiosk_modules_load_through_the_dash_registry(tmp_path):
    # deterministic kiosk-Pi temperature on any dev platform
    thermal = tmp_path / "temp"
    thermal.write_text("45600")

    registry = PluginRegistry()
    try:
        await registry.load_all(_kiosk_settings(
            rpi={"thermal_zone": str(thermal)},
        ))
        # real probes run in the pollers — wait for their first collection
        await registry.wait_first_poll(timeout=30)

        statuses = {info.name: info for info in registry.status()}

        # dash built-ins
        assert statuses["proxmox"].status == "failed"          # no config given
        assert statuses["zynk"].status == "disabled"
        assert statuses["homeassistant"].status == "disabled"
        assert statuses["prometheus"].status == "disabled"
        # kiosk modules
        assert statuses["rpi"].status == "ok"
        assert statuses["hosts"].status == "ok"
        assert statuses["pihole"].status == "unavailable"      # endpoint refused

        widgets = {w.id: w for w in registry.widgets()}

        # Proxmox tiles degrade to placeholders with the setup error —
        # this is exactly the "Proxmox is down" display the kiosk must show.
        for widget_id in ("proxmox-nodes", "proxmox-guests"):
            widget = widgets[widget_id]
            assert widget.status == WidgetStatus.unavailable
            assert widget.error and "setup failed" in widget.error
            assert widget.title

        # Pi-hole refused → unavailable with the connection error
        pihole = widgets["pihole-stats"]
        assert pihole.status == WidgetStatus.unavailable
        assert pihole.error

        # Local modules collect real data
        kiosk_pi = widgets["kiosk-pi"]
        assert kiosk_pi.status == WidgetStatus.ok
        assert kiosk_pi.data["tiles"][0]["info"]["cpu_temp_c"] == 45.6

        network = widgets["network-hosts"]
        assert network.status == WidgetStatus.ok
        devices = {d["name"]: d for d in network.data["devices"]}
        assert devices["Loopback"]["status"] == "online"
        assert devices["Loopback"]["metrics"]["probe"] == "icmp"

        # disabled modules contribute no widgets
        assert all(w.plugin != "zynk" for w in registry.widgets())
        assert all(w.plugin != "prometheus" for w in registry.widgets())
    finally:
        await registry.teardown_all()


async def test_rpi_stays_ok_when_every_network_source_is_down():
    """Kiosk worst case: backend up, Proxmox/Pi-hole unreachable."""
    registry = PluginRegistry()
    try:
        await registry.load_all(_kiosk_settings(
            hosts={"checks": [{"name": "Blackhole", "host": "192.0.2.1"}],
                   "ping_timeout_seconds": 1},
        ))
        await registry.wait_first_poll(timeout=30)

        widgets = {w.id: w for w in registry.widgets()}
        assert widgets["kiosk-pi"].status == WidgetStatus.ok
        # the probe runs locally — it reports DOWN instead of failing
        network = widgets["network-hosts"]
        assert network.status == WidgetStatus.ok
        device = network.data["devices"][0]
        assert device["status"] == "offline"
        # and the unreachable sources show their tiles as unavailable
        assert widgets["proxmox-nodes"].status == WidgetStatus.unavailable
        assert widgets["pihole-stats"].status == WidgetStatus.unavailable
    finally:
        await registry.teardown_all()


def test_kiosk_page_files_exist():
    page = MODULES_ROOT.parent / "kiosk"
    for name in ("index.html", "style.css", "app.js"):
        assert (page / name).is_file(), f"missing kiosk/{name}"


def test_deploy_files_exist():
    deploy = MODULES_ROOT.parent / "deploy"
    for name in ("install.sh", "hola-backend.service", "hola-kiosk.service",
                 "config.txt.snippet"):
        assert (deploy / name).is_file(), f"missing deploy/{name}"

"""Hosts module: ping/TCP probes, RTT parsing, config validation, and the
hourly speedtest background probe."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest

from app.models import WidgetTypes
from hosts.plugin import HostsDataSource
from hosts.service import _RTT_RE, HostsService


def test_rtt_regex_matches_linux_and_macos_output():
    linux = "64 bytes from 192.0.2.254: icmp_seq=1 ttl=64 time=0.412 ms"
    macos = "64 bytes from 127.0.0.1: icmp_seq=0 ttl=64 time=0.045 ms"
    no_rtt = "rtt min/avg/max/mdev = 0.412/0.412/0.412/0.412 ms"

    match = _RTT_RE.search(linux)
    assert match and float(match.group(1)) == 0.412
    match = _RTT_RE.search(macos)
    assert match and float(match.group(1)) == 0.045
    assert _RTT_RE.search(no_rtt) is None or "time=" not in no_rtt


async def test_ping_loopback():
    reachable, rtt = await HostsService().ping("127.0.0.1", timeout=2)
    assert reachable is True
    assert rtt is None or rtt >= 0


async def test_ping_unreachable_host():
    # TEST-NET-1 (RFC 5737) — guaranteed unroutable
    reachable, rtt = await HostsService(ping_timeout_seconds=1).ping("192.0.2.1")
    assert reachable is False
    assert rtt is None


async def test_tcp_check_open_port():
    async def noop(reader, writer):
        pass

    server = await asyncio.start_server(noop, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        reachable, latency = await HostsService().tcp_check("127.0.0.1", port)
        assert reachable is True
        assert latency is not None and latency >= 0
    finally:
        server.close()
        await server.wait_closed()


async def test_tcp_check_closed_port():
    # grab a free port, close it again, then probe it
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()

    reachable, latency = await HostsService(port_timeout_seconds=0.5).tcp_check(
        "127.0.0.1", port
    )
    assert reachable is False
    assert latency is None


async def test_plugin_setup_requires_checks():
    plugin = HostsDataSource()
    with pytest.raises(ValueError, match="checks"):
        await plugin.setup({})
    with pytest.raises(ValueError, match="checks"):
        await plugin.setup({"checks": []})
    with pytest.raises(ValueError, match="usable"):
        await plugin.setup({"checks": [{"name": ""}]})


async def test_plugin_collect_reports_online_and_offline():
    # a TCP server that stays up, and a free-then-closed port that stays down
    async def noop(reader, writer):
        pass

    server = await asyncio.start_server(noop, "127.0.0.1", 0)
    open_port = server.sockets[0].getsockname()[1]
    probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    closed_port = probe.sockets[0].getsockname()[1]
    probe.close()
    await probe.wait_closed()

    plugin = HostsDataSource()
    await plugin.setup({
        "checks": [
            {"name": "Local Service", "host": "127.0.0.1", "port": open_port,
             "role": "gateway"},
            {"name": "Dead Port", "host": "127.0.0.1", "port": closed_port},
            {"name": "Loopback", "host": "127.0.0.1", "role": "wan"},
        ],
        "ping_timeout_seconds": 2,
    })
    try:
        widgets = await plugin.collect()
    finally:
        server.close()
        await server.wait_closed()

    assert len(widgets) == 1
    widget = widgets[0]
    assert widget.id == "network-hosts"
    assert widget.type == WidgetTypes.DEVICE_TABLE

    devices = {d["name"]: d for d in widget.data["devices"]}
    assert devices["Local Service"]["status"] == "online"
    assert devices["Local Service"]["category"] == "gateway"
    assert devices["Local Service"]["metrics"]["probe"] == f"tcp/{open_port}"
    assert "latency_ms" in devices["Local Service"]["metrics"]

    assert devices["Dead Port"]["status"] == "offline"

    assert devices["Loopback"]["status"] == "online"
    assert devices["Loopback"]["metrics"]["probe"] == "icmp"


# ── speedtest background probe ──────────────────────────────────────────────

SPEEDTEST_JSON = {
    "download": 45_200_000.0,     # bits per second
    "upload": 12_100_000.0,
    "ping": 23.456,
    "server": {"sponsor": "Test ISP"},
}


def _fake_speedtest_binary(tmp_path, payload=SPEEDTEST_JSON, exit_code=0):
    """A stand-in speedtest-cli that prints canned JSON."""
    script = tmp_path / "speedtest-cli"
    script.write_text(
        "#!/bin/sh\n"
        f"echo '{json.dumps(payload)}'\n"
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    return str(script)


async def test_run_speedtest_parses_results(tmp_path):
    service = HostsService()
    result = await service.run_speedtest(command=_fake_speedtest_binary(tmp_path))
    assert result["down_mbps"] == 45.2
    assert result["up_mbps"] == 12.1
    assert result["ping_ms"] == 23.5
    assert result["server"] == "Test ISP"


async def test_run_speedtest_filters_ping_sentinel(tmp_path):
    """speedtest-cli reports 1800000 ms when the ping couldn't be measured."""
    payload = dict(SPEEDTEST_JSON, ping=1_800_000.0)
    service = HostsService()
    result = await service.run_speedtest(
        command=_fake_speedtest_binary(tmp_path, payload=payload)
    )
    assert result["ping_ms"] is None
    assert result["down_mbps"] == 45.2  # rest intact


async def test_run_speedtest_missing_binary():
    service = HostsService()
    with pytest.raises(RuntimeError, match="not found"):
        await service.run_speedtest(command="/nonexistent/speedtest-cli")


async def test_run_speedtest_reports_failure(tmp_path):
    service = HostsService()
    binary = _fake_speedtest_binary(tmp_path, exit_code=1)
    with pytest.raises(RuntimeError, match="exited 1"):
        await service.run_speedtest(command=binary)


async def test_run_speedtest_rejects_garbage(tmp_path):
    service = HostsService()
    binary = _fake_speedtest_binary(tmp_path, payload={"unexpected": True})
    with pytest.raises(RuntimeError, match="unexpected speedtest-cli output"):
        await service.run_speedtest(command=binary)


def _hosts_config(**extra):
    config = {
        "checks": [
            {"name": "Gateway", "host": "192.0.2.254", "role": "gateway"},
            {"name": "WAN", "host": "1.1.1.1", "role": "wan"},
        ],
        "ping_timeout_seconds": 2,
    }
    config.update(extra)
    return config


class _NoProbeService(HostsService):
    """Probes replaced with instant offline results (no network in tests)."""

    async def ping(self, host, timeout=None):
        return (False, None)

    async def tcp_check(self, host, port, timeout=None):
        return (False, None)


async def test_speedtest_cache_merges_into_wan_row(tmp_path):
    plugin = HostsDataSource()
    await plugin.setup(_hosts_config(
        speedtest_interval_seconds=0,   # no background task needed here
        speedtest_command=_fake_speedtest_binary(tmp_path),
    ))
    plugin.service = _NoProbeService()

    # simulate a completed background run
    plugin._speedtest_cache = {
        "down_mbps": 87.3, "up_mbps": 24.0, "ping_ms": 19.0,
        "server": "Test ISP", "speedtest_at": "2026-11-21T11:02:41+00:00",
    }

    widgets = await plugin.collect()
    devices = {d["name"]: d for d in widgets[0].data["devices"]}
    wan = devices["WAN"]["metrics"]
    assert wan["down_mbps"] == 87.3
    assert wan["up_mbps"] == 24.0
    assert wan["speedtest_at"] == "2026-11-21T11:02:41+00:00"
    # other rows stay untouched
    assert "down_mbps" not in devices["Gateway"]["metrics"]


async def test_speedtest_task_started_for_wan_check(tmp_path):
    plugin = HostsDataSource()
    await plugin.setup(_hosts_config(
        speedtest_command=_fake_speedtest_binary(tmp_path),
    ))
    assert plugin._speedtest_task is not None
    await plugin.teardown()


async def test_speedtest_task_skipped_without_wan_check(tmp_path):
    plugin = HostsDataSource()
    await plugin.setup({
        "checks": [{"name": "Gateway", "host": "192.0.2.254", "role": "gateway"}],
        "speedtest_command": _fake_speedtest_binary(tmp_path),
    })
    assert plugin._speedtest_task is None


async def test_speedtest_task_skipped_when_disabled(tmp_path):
    plugin = HostsDataSource()
    await plugin.setup(_hosts_config(
        speedtest_interval_seconds=0,
        speedtest_command=_fake_speedtest_binary(tmp_path),
    ))
    assert plugin._speedtest_task is None


async def test_speedtest_failure_surfaces_until_first_success(tmp_path):
    plugin = HostsDataSource()
    await plugin.setup(_hosts_config(
        speedtest_interval_seconds=0,
        speedtest_command=_fake_speedtest_binary(tmp_path),
    ))
    plugin.service = _NoProbeService()

    plugin._speedtest_error = "RuntimeError: speedtest exited 1: boom"
    widgets = await plugin.collect()
    wan = {d["name"]: d for d in widgets[0].data["devices"]}["WAN"]["metrics"]
    assert "speedtest_error" in wan
    assert "down_mbps" not in wan


def _hanging_speedtest_binary(tmp_path):
    """A speedtest stand-in that reports its PID, then sleeps forever."""
    script = tmp_path / "speedtest-hang"
    script.write_text(
        "#!/bin/sh\n"
        f"echo $$ > {tmp_path}/hanging.pid\n"
        "sleep 300\n"
    )
    script.chmod(0o755)
    return str(script)


async def test_run_speedtest_timeout_kills_the_process(tmp_path):
    """A hung speedtest must be killed — an orphaned full-bandwidth process
    would saturate the WiFi forever."""
    import os
    import signal
    import time as _time

    service = HostsService()
    binary = _hanging_speedtest_binary(tmp_path)

    with pytest.raises(RuntimeError, match="timed out"):
        await service.run_speedtest(command=binary, timeout=1.0)

    pid = int((tmp_path / "hanging.pid").read_text().strip())
    # the kill is asynchronous-ish — give the reaper a moment
    for _ in range(40):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("orphaned speedtest process survived the timeout")

"""
Local reachability probes — ICMP ping and TCP port checks, run directly on
the kiosk Pi (subprocess ping / asyncio socket), plus an optional bandwidth
measurement via `speedtest-cli --json`. Deliberately local:

- no dependency on any monitored service (the kiosk must show "Proxmox DOWN"
  while Proxmox is down — its own probes keep working),
- no SNMP/agent required on the targets,
- results carry the RTT as latency.

The speedtest is EXPENSIVE (tens of seconds, full-bandwidth traffic) — the
plugin runs it in a slow background task (default: once an hour) and caches
the last result; it is never part of the fast 30s collect cycle.

Ping flags differ by platform: Linux (`-W` seconds, deadline) vs macOS
(`-t` timeout seconds) — handled here so tests behave the same on dev
machines and on the Pi.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

_RTT_RE = re.compile(r"time[=<]([\d.]+)\s*ms")


def resolve_speedtest_command(configured: str | None = None) -> str | None:
    """Locate speedtest-cli: explicit config beats the backend venv's bin
    directory, which beats PATH lookup. A configured path that doesn't
    exist (or isn't executable) resolves to None so callers report a clear
    'not found' instead of a cryptic exec failure."""
    if configured:
        if os.path.isabs(configured) or os.sep in configured:
            return configured if os.access(configured, os.X_OK) else None
        return shutil.which(configured)
    venv_bin = Path(sys.executable).parent / "speedtest-cli"
    if venv_bin.is_file():
        return str(venv_bin)
    return shutil.which("speedtest-cli")


class HostsService:
    def __init__(self, ping_timeout_seconds: float = 2.0,
                 port_timeout_seconds: float = 2.0) -> None:
        self.ping_timeout = ping_timeout_seconds
        self.port_timeout = port_timeout_seconds

    async def ping(self, host: str, timeout: float | None = None) -> tuple[bool, float | None]:
        """One ICMP probe → (reachable, rtt_ms)."""
        timeout = timeout or self.ping_timeout
        if sys.platform == "darwin":
            cmd = ["ping", "-c", "1", "-t", str(max(1, int(timeout)))]
        else:  # Linux (the kiosk Pi) and friends
            cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout)))]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, host,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout + 5)
        except (OSError, asyncio.TimeoutError):
            return (False, None)
        if proc.returncode != 0:
            return (False, None)
        match = _RTT_RE.search(stdout.decode(errors="ignore"))
        rtt = float(match.group(1)) if match else None
        return (True, rtt)

    async def tcp_check(self, host: str, port: int,
                        timeout: float | None = None) -> tuple[bool, float | None]:
        """One TCP connect probe → (reachable, connect_ms)."""
        timeout = timeout or self.port_timeout
        started = time.monotonic()
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout
            )
        except (OSError, asyncio.TimeoutError):
            return (False, None)
        try:
            writer.close()
        except Exception:
            pass
        latency_ms = round((time.monotonic() - started) * 1000.0, 1)
        return (True, latency_ms)

    # ── speedtest (slow background probe — see module docstring) ─────────

    async def run_speedtest(self, command: str | None = None,
                             timeout: float = 180.0) -> dict:
        """
        Run `speedtest-cli --json` →
        {"down_mbps", "up_mbps", "ping_ms", "server", "speedtest_at"}.
        Raises on a missing binary or a failed run — the plugin caches only
        successful results. On timeout the subprocess is KILLED (an orphaned
        full-bandwidth process would eat the WiFi forever).
        """
        binary = resolve_speedtest_command(command)
        if not binary:
            raise RuntimeError(
                "speedtest-cli not found — install it into the backend venv "
                "(pip install speedtest-cli) or set hosts.speedtest_command"
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                binary, "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise RuntimeError(f"speedtest failed to start: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()  # reap; no orphan left behind
            raise RuntimeError(f"speedtest timed out after {timeout:.0f}s") from None
        if proc.returncode != 0:
            tail = stderr.decode(errors="ignore").strip()[-160:]
            raise RuntimeError(f"speedtest exited {proc.returncode}: {tail}")
        try:
            data = json.loads(stdout.decode(errors="ignore"))
            down_bps = float(data["download"])
            up_bps = float(data["upload"])
            ping_ms = float(data["ping"])
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"unexpected speedtest-cli output: {exc}") from exc
        # speedtest-cli reports 1800000.0 (30 min) when it failed to measure
        # the ping — store None instead of a nonsense value
        if ping_ms >= 60_000.0:
            ping_ms = None
        else:
            ping_ms = round(ping_ms, 1)
        server = data.get("server") or {}
        return {
            "down_mbps": round(down_bps / 1_000_000.0, 1),
            "up_mbps": round(up_bps / 1_000_000.0, 1),
            "ping_ms": ping_ms,
            "server": server.get("sponsor") or server.get("name"),
        }

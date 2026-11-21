#!/usr/bin/env python3
"""Mock HoLa-Dash backend for developing/checking the kiosk page.

Serves /api/widgets with a realistic full payload (every widget OK, plus
one unavailable so the degradation card can be checked) — no real backend
or services needed:

    python3 tests/render/mock_backend.py        # → http://127.0.0.1:8602

Then open the page against it:

    cd kiosk && python3 -m http.server 8603
    open "http://localhost:8603/index.html?backend=http://127.0.0.1:8602"
"""
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

NOW = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"

WIDGETS = [
    {
        "id": "proxmox-nodes", "type": "resource-tiles", "title": "Proxmox Nodes",
        "span": 1, "plugin": "proxmox", "status": "ok", "error": None, "updated_at": NOW,
        "data": {"tiles": [
            {"id": "pve1", "label": "pve1", "status": "online", "cpu_pct": 62.3,
             "ram_pct": 44.1, "disk_pct": 31.0, "uptime_seconds": 1209600,
             "info": {"temp_c": 45.5, "temps": "CPU 45.5°C · Board 38.0°C",
                       "loadavg": "0.42, 0.35, 0.30"}},
            {"id": "pve2", "label": "pve2", "status": "online", "cpu_pct": 88.7,
             "ram_pct": 91.2, "disk_pct": 55.0, "uptime_seconds": 864000,
             "info": {"temp_c": 71.0, "temps": "CPU 71.0°C",
                       "loadavg": "2.1, 1.8, 1.2"}},
        ],
        "details": {
            "pve1": {
                "kversion": "Linux 6.8.12-1-pve",
                "pveversion": "pve-manager/8.2.4",
                "cpu_model": "Intel N100", "cpu_cores": 4, "cpu_sockets": 1,
                "memory_used_gb": 100.0, "memory_total_gb": 256.0,
                "rootfs_used_gb": 10.0, "rootfs_total_gb": 50.0,
                "swap_used_gb": 0.0, "swap_total_gb": 8.0,
                "loadavg": "0.42, 0.35, 0.30",
                "thermal": [
                    {"type": "CPU", "value": "45.5°C"},
                    {"type": "Board", "value": "38.0°C"},
                ],
            },
        }},
    },
    {
        "id": "proxmox-guests", "type": "device-table", "title": "Proxmox VMs & Containers",
        "span": 2, "plugin": "proxmox", "status": "ok", "error": None, "updated_at": NOW,
        "data": {"devices": [
            {"id": "vm/100", "name": "pihole", "category": "vm", "status": "running",
             "model": None, "ip": None, "node": "pve1", "tags": ["dns"],
             "metrics": {"cpu_pct": 3.1, "mem_pct": 22.0, "mem_used_gb": 0.7,
                         "mem_max_gb": 3.0, "uptime_seconds": 1200000},
             "actions": []},
            {"id": "vm/101", "name": "zynk.example.com", "category": "vm", "status": "running",
             "model": None, "ip": None, "node": "pve1", "tags": [],
             "metrics": {"cpu_pct": 1.2, "mem_pct": 18.0, "uptime_seconds": 900000},
             "actions": []},
            {"id": "lxc/200", "name": "prometheus", "category": "lxc", "status": "running",
             "model": None, "ip": None, "node": "pve2", "tags": ["snmp"],
             "metrics": {"cpu_pct": 5.4, "mem_pct": 40.0, "uptime_seconds": 700000},
             "actions": []},
            {"id": "vm/102", "name": "windows-test", "category": "vm", "status": "stopped",
             "model": None, "ip": None, "node": "pve2", "tags": [],
             "metrics": {"cpu_pct": 0.0, "mem_pct": 0.0}, "actions": []},
            {"id": "vm/103", "name": "comfyui", "category": "vm", "status": "running",
             "model": None, "ip": None, "node": "pve2", "tags": ["gpu"],
             "metrics": {"cpu_pct": 72.0, "mem_pct": 84.0, "uptime_seconds": 30000},
             "actions": []},
        ]},
    },
    {
        "id": "pihole-stats", "type": "metric-list", "title": "Pi-hole",
        "span": 1, "plugin": "pihole", "status": "ok", "error": None, "updated_at": NOW,
        "data": {"groups": [{"label": "Pi-hole", "entries": [
            {"label": "Queries (24h)", "value": 12345, "unit": None, "status": "unknown"},
            {"label": "Blocked (24h)", "value": 891, "unit": None, "status": "unknown"},
            {"label": "Block rate", "value": 7.2, "unit": "%", "status": "unknown"},
            {"label": "Active clients", "value": 7, "unit": None, "status": "unknown"},
            {"label": "Blocklist size", "value": 1234567, "unit": None, "status": "unknown"},
            {"label": "Blocking", "value": "enabled", "unit": None, "status": "online"},
        ]}],
        "detail": {
            "top_domains": [
                {"domain": "nc.example.com", "count": 2977},
                {"domain": "dndwiki.example.com", "count": 2873},
            ],
            "top_clients": [
                {"name": "", "ip": "198.51.100.9", "count": 23662},
                {"name": "monitor.example.com", "ip": "198.51.100.4", "count": 18689},
            ],
        }},
    },
    {
        "id": "network-hosts", "type": "device-table", "title": "Network",
        "span": 2, "plugin": "hosts", "status": "ok", "error": None, "updated_at": NOW,
        "data": {"devices": [
            {"id": "gateway", "name": "Gateway", "category": "gateway", "status": "online",
             "model": None, "ip": "10.0.0.254", "node": None, "tags": ["gateway"],
             "metrics": {"probe": "icmp", "latency_ms": 0.4}, "actions": []},
            {"id": "wan", "name": "WAN", "category": "wan", "status": "online",
             "model": None, "ip": "1.1.1.1", "node": None, "tags": ["wan"],
             "metrics": {"probe": "icmp", "latency_ms": 142.5,
                         "down_mbps": 45.2, "up_mbps": 12.1,
                         "speedtest_at": "2026-11-21T11:02:41+00:00"},
             "actions": []},
            {"id": "nas", "name": "NAS", "category": "host", "status": "offline",
             "model": None, "ip": "10.0.0.30", "node": None, "tags": ["host"],
             "metrics": {"probe": "tcp/443"}, "actions": []},
        ]},
    },
    {
        "id": "prometheus-metrics", "type": "metric-list", "title": "Network Hardware Metrics",
        "span": 1, "plugin": "prometheus", "status": "unavailable",
        "error": "ConnectError: prometheus host unreachable", "updated_at": NOW,
        "data": {"groups": []},
    },
    {
        "id": "kiosk-pi", "type": "resource-tiles", "title": "Kiosk Pi",
        "span": 1, "plugin": "rpi", "status": "ok", "error": None, "updated_at": NOW,
        "data": {"tiles": [
            {"id": "hola-kiosk", "label": "hola-kiosk", "status": "online",
             "cpu_pct": 18.4, "ram_pct": 43.0, "disk_pct": 29.0,
             "uptime_seconds": 276480, "info": {"cpu_temp_c": 51.2, "load1": 0.84}},
        ]},
    },
]

PAYLOAD = json.dumps({"widgets": WIDGETS})


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/widgets"):
            body = PAYLOAD.encode()
            code, ctype = 200, "application/json"
        elif self.path.startswith("/api/health"):
            body = json.dumps({"status": "ok", "version": "0.1.0-mock"}).encode()
            code, ctype = 200, "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8602), Handler)
    print("mock backend on http://127.0.0.1:8602/api/widgets (CORS: *)")
    server.serve_forever()

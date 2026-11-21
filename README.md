# HoLa Kiosk — Homelab Status Screen (Raspberry Pi 4 + 7" DSI)

An unattended wall display for the homelab: **Proxmox** node/VM health with
CPU/RAM/disk and host temperatures, **Pi-hole** filtering stats, **network
reachability** (gateway, WAN latency — probed directly from the kiosk Pi),
and the **kiosk Pi itself**. Single 800×480 view, color-coded green/amber/red,
readable from a few meters. Runs entirely on the local network.

Built on **HoLa-Dash** (sibling repo, [`../HoLa_Dash`](../HoLa_Dash)) — see
[`AGENTS.md`](AGENTS.md) for the original brief.

---

## Reuse decision (per AGENTS.md: "locate and inspect HoLa-Dash first")

HoLa-Dash's backend **runs standalone on the kiosk Pi** and is reused directly
as the data source:

| Requirement | How it is met |
|---|---|
| "Expose a simple JSON endpoint the kiosk can poll" | HoLa-Dash's `GET /api/widgets` (+ public `GET /api/health` for watchdogs) is exactly that |
| "Not depend on any service it is monitoring" | The backend runs **on the Pi itself** — if the Proxmox host dies, the backend keeps serving and the Proxmox tiles show **UNREACHABLE** instead of a blank screen |
| "One broken data source must never crash the kiosk" | HoLa-Dash's registry isolates module failures (32 dash tests + this repo's integration tests) |
| "Light enough for a 1 GB Pi 4" | Backend is DB-free FastAPI (~150 MB RSS); the screen is static HTML/CSS/JS — no React, no Electron, no build step |

Kiosk-specific sources (Pi-hole, ping/TCP probes, kiosk-Pi sensors) are
**HoLa-Dash modules in this repo**, loaded through the standard
`plugins.directories` mechanism — the dashboard engine is not forked or
patched.

```
┌────────────────────── Raspberry Pi 4 (kiosk) ─────────────────────────────┐
│  systemd: hola-kiosk.service ── cage (Wayland) ── Chromium --kiosk         │
│        │ file:///…/kiosk/index.html  (static page, no framework)           │
│        │          fetch http://127.0.0.1:8600/api/widgets every 10 s      │
│        ▼                                                                   │
│  systemd: hola-backend.service ── HoLa-Dash backend (uvicorn, 127.0.0.1)   │
│        ├── dash modules:   proxmox (token), prometheus (SNMP metrics)      │
│        └── this repo's modules (plugins.directories):                     │
│              pihole   ── v6 REST API (SID session)                         │
│              hosts    ── ping/TCP probes (local — work in any outage)       │
│              rpi      ── sysfs/proc: temp, load, RAM, disk, uptime          │
└─────────────────────────────────────────────────────────────────────────────┘
```

Both services use `Restart=always`: a crashed cage/Chromium session or backend
comes back within seconds. The page itself keeps retrying (it never blanks),
and recycles itself every 6 h as a long-run safety net.

---

## Install on the kiosk Pi

**Target:** Raspberry Pi OS **Lite (64-bit)**, Bookworm or newer, on the Pi 4
with the official 7" DSI panel. Default service user: `pi` (or whichever user
invoked sudo).

```bash
# on the Pi — both repos side by side (e.g. cloned or rsync'd to /home/pi)
cd ~/Development           # wherever the siblings live
cd HoLa_Kiosk
sudo ./deploy/install.sh   # defaults: --dash-dir ../HoLa_Dash --target /opt/hola

# 1. secrets
sudo nano /opt/hola/HoLa_Kiosk/.env
#    PROXMOX_TOKEN_NAME / PROXMOX_TOKEN_VALUE  (read-only token is enough!)
#    PIHOLE_PASSWORD                           (empty if your Pi-hole has none)

# 2. IPs & hosts
sudo nano /opt/hola/HoLa_Kiosk/config/settings.yaml

# 3. go
sudo reboot
```

What `install.sh` does (idempotent, safe to re-run): installs `cage`,
`chromium`, `python3-venv`; rsyncs both repos to `/opt/hola`; creates the venv
and `pip install`s the backend; installs both systemd units (enabled:
backend now, kiosk on next boot — it takes over tty1); appends the boot-config
snippet (**HiFiBerry DAC classic** → `dtoverlay=hifiberry-dac`; the 7" DSI
panel needs no overlay on Bookworm).

### Open the backend from your desk (optional)

The backend binds to `127.0.0.1` only (kiosk appliance default). To inspect it
from the LAN: set `web.host: "0.0.0.0"` **and** `web.api_key` (via
`HOLA_API_KEY` in `.env`) in `config/settings.yaml`, then
`http://<pi>:8600/api/widgets` (OpenAPI docs at `/docs`). Loopback clients —
the kiosk screen itself — are exempt from the key; it applies to LAN clients
only. The main **HoLa-Dash frontend** can also be pointed at the kiosk, or
run separately on another host — same API (enter the key once when prompted).

---

## The screen

```
┌──────────────────────────────────────────────────────────────────────────┐
│ HoLa ●                                          14:32:05                │
├───────────────────────────────────────────┬──────────────────────────────┤
│ PROXMOX NODES (tap a tile → node details) │ PI-HOLE (tap → details)      │
│ ● pve1  45°C    ● pve2  41°C              │ Queries (24h)   12,345       │
│   CPU ▓▓▓░░ 62%   RAM ▓▓░░░ 44%           │ Blocked (24h)     891       │
│   DISK ▓░░░░ 31%                          │ Block rate       7.2%       │
│ PROXMOX VMS & CTs (tap → machine details) │ Blocking         ● ON        │
│ ● pihole ● zynk ● pve2-ct ● home          ├──────────────────────────────┤
│                                           │ NETWORK                      │
│                                           │ ● Gateway      0.4 ms        │
│                                           │ ● WAN         12.3 ms       │
│                                           │   ↓ 45.2 ↑ 12.1 Mb/s · 11:02│
│                                           ├──────────────────────────────┤
│                                           │ ● Network Hardware ›   ● Status Page › │
├───────────────────────────────────────────┴──────────────────────────────┤
│ KIOSK PI   51.2°C   load 0.8   CPU ▓░ 18% RAM ▓▓░░ 44% DISK ▓░░ 31% up 3d│
└──────────────────────────────────────────────────────────────────────────┘
```

The WAN row carries the hourly speedtest (↓/↑ + run time); failures show as
`speedtest: <reason>` until the next successful run, the last good result
otherwise.

**Colors / thresholds** (tune in `kiosk/app.js`, `TH`): CPU/RAM/disk green <
70 %, amber < 90 %, red ≥ 90 %; Proxmox host sensors amber ≥ 60 °C, red
≥ 70 °C; kiosk Pi temp amber ≥ 65 °C, red ≥ 75 °C; WAN RTT amber > 100 ms.

**Degradation rules** (implemented in `kiosk/app.js` + the backend):

| Failure | Behavior |
|---|---|
| One source unreachable (e.g. Proxmox down) | Its card(s) show **UNREACHABLE** + the error; everything else keeps ticking |
| Module disabled / removed | Its card disappears; layout re-flows |
| Backend unreachable (worst case) | Red banner "Backend unreachable — retrying", last tiles stay visible (stale outline), auto-retry every 10 s — the screen never blanks |
| Unknown widget id | Generic card with raw data instead of being dropped |

The page inserts all dynamic text via `textContent` (no `innerHTML`), so device
names from any API can never inject markup.

### Pages: swipe and tap navigation

The screen is a **six-page slider** — the dashboard plus five detail views.
Everything is reachable by tap from the dashboard; swipes walk the pages in
order (dashboard → machine → Pi-hole → node → hardware metrics → status):

| Page | Opens from | Shows |
|---|---|---|
| **Dashboard** | — | All cards (below) |
| **Machine details** | tapping a guest chip | full FQDN, type, ID, node, uptime, disk, RAM, tags, live CPU/RAM bars |
| **Pi-hole details** | tapping the Pi-hole card | summary stats, top domains, top clients (from the v6 tops endpoints) |
| **Proxmox node details** | tapping a node tile | live CPU/RAM/disk bars, kernel, PVE version, CPU model, RAM/root FS/swap totals, sensors, running guests on that node (chips → machine details) |
| **Hardware metrics** | the "Network Hardware ›" chip | full SNMP metric groups (Zyxel temps, fans, VPN) |
| **Status page** | the "Status Page ›" chip | an external status page, embedded via iframe |

Every non-dashboard page has a **"‹ close" strip**, the **page dots** and
swipe-back; arrow keys work in a normal browser. Detail pages stay live —
they re-render with every 10 s poll while open.

The status page deserves a note: services like Uptime-Kuma send
`X-Frame-Options: SAMEORIGIN`, which Chromium refuses to embed ("refused to
connect"). The kiosk therefore runs a **local stripping proxy**
(`deploy/status_proxy.py`, systemd unit `hola-status-proxy`, port 8601) — the
iframe points at `http://127.0.0.1:8601/status/internal`, which relays to
your instance (`STATUS_PROXY_TARGET` in the unit, **preserved across
re-installs** once set). The chip's reachability dot probes through it.
`index.html?page2=<url>` embeds a different page directly (must allow
framing). Re-entering the page reloads the iframe — fresh content and a retry
if the service was down.

> **Privacy note:** all shipped IPs, hostnames and ranges in this repo are
> **placeholders** (`10.0.0.x` for config examples, RFC 5737 doc ranges in
> tests, `*.example.com` hostnames). Nothing real, no credentials — secrets
> live in `.env` (never committed) and Fernet-encrypted config values.

---

## Configuration — `config/settings.yaml`

Same conventions as HoLa-Dash (`${VAR}` from `.env`, per-module sections
under `plugins:`). Kiosk specifics:

| Section | Meaning |
|---|---|
| `web.host` | `127.0.0.1` (default, kiosk-local) or `0.0.0.0` for LAN access (then set `api_key`!) |
| `plugins.directories` | dash built-ins (`"plugins"`, resolved by the backend) + this repo's `modules/` (absolute path on the Pi) |
| `plugins.disabled` | `["zynk", "homeassistant"]` — keep the Pi lean |
| `plugins.proxmox` | host + **read-only** token; `poll_interval_seconds: 30` |
| `plugins.prometheus` | compact SNMP query set (temps, fans, VPN) — see note below |
| `plugins.pihole` | `base_url` (no `/api` suffix), `password` (empty = open API) |
| `plugins.hosts` | `checks:` — name + host (ICMP) or + `port` (TCP); optional `role` (`gateway` / `wan` / `host`) for grouping; `ping_timeout_seconds`; `speedtest_interval_seconds: 3600` — hourly bandwidth measurement on the WAN row (0 disables, needs a `wan`-role check and `speedtest-cli`, which install.sh pip-installs into the backend venv; override the binary via `speedtest_command`) |
| `plugins.rpi` | optional `thermal_zone` path override |

Note on `prometheus`: that tile depends on your Prometheus box being up —
if it isn't, the tile degrades to UNREACHABLE (by design; the kiosk's own
probes are local and keep working). Remove it from `directories`/re-enable it
in `disabled` if you don't want it on the screen.

### Endpoints used per source (kiosk brief §"check the APIs first")

| Source | Endpoints | Why |
|---|---|---|
| Proxmox | `GET /nodes`, `GET /nodes/{n}/qemu`, `GET /nodes/{n}/lxc`, `GET /nodes/{n}/status` (thermal) via proxmoxer, token auth | official REST API; node status carries the host sensor readings the kiosk brief asks for |
| Pi-hole | `POST /api/auth` (SID), `GET /api/stats/summary`, `GET /api/dns/blocking`, health via any HTTP response | v6 REST API; session is renewed transparently on expiry |
| Prometheus | `GET /api/v1/query` (instant) | reuse the existing snmp_exporter scrape stack — zero extra load on the Zyxel devices |
| Gateway / WAN / hosts | local `ping -c1` (ICMP) and TCP connects | independent of every monitored service; carries RTT |
| WAN bandwidth | `speedtest-cli --json` (backend venv), hourly background task | full-bandwidth test is too heavy for the poll cycle — cached result attached to the WAN row |
| Kiosk Pi | sysfs `/sys/class/thermal`, `/proc/meminfo`, `/proc/uptime`, `getloadavg` | local — works during any outage |

---

## Development

Backend + modules (uses the HoLa-Dash environment; sibling repo):

```bash
cd HoLa_Dash/backend
uv venv && uv pip install -e ".[dev]"

# run with the kiosk config (adjust directories in it for local paths):
HOLA_CONFIG=$PWD/../../HoLa_Kiosk/config/settings.yaml \
HOLA_DATA_DIR=/tmp/hola-data uvicorn app.main:app --port 8600

# tests (either repo):
cd ../HoLa_Kiosk && uv run --project ../HoLa_Dash/backend python -m pytest
```

The screen itself is three files in `kiosk/` — open `kiosk/index.html` in any
browser; it defaults to `http://127.0.0.1:8600` and can be pointed elsewhere
via `index.html?backend=http://10.0.0.20:8600`.

**Rendering the page without any backend**: `tests/render/mock_backend.py`
serves a realistic mock `/api/widgets` (CORS open) —

```bash
python3 tests/render/mock_backend.py          # → http://127.0.0.1:8602
cd kiosk && python3 -m http.server 8603
open "http://localhost:8603/index.html?backend=http://127.0.0.1:8602"
```

A headless render check (`tests/render/render_check.js`, jsdom ad hoc, 29
assertions covering every renderer incl. the UNREACHABLE degradation card)
is documented in the script header.

**Tests (23)** cover the Pi-hole SID flow with single-use sessions, ping/TCP
probes (real loopback + real closed ports), sysfs parsing with injected
files, and an end-to-end registry integration: kiosk modules load through the
**real HoLa-Dash registry** while Proxmox/Pi-hole are down — proving the
kiosk keeps working when its monitored services don't.

---

## Hardware notes

- **1 GB RAM budget** (measured ballparks): Pi OS Lite headless ~80 MB,
  backend ~150 MB, cage ~50 MB, Chromium kiosk ~300–450 MB → fits; keep zram
  (Pi OS default) enabled. Disable the prometheus module if you want more
  headroom.
- **HiFiBerry DAC (classic)**: `dtoverlay=hifiberry-dac` (added by
  install.sh). v1 doesn't use audio; the optional "critical failure" alert
  sound (see AGENTS.md) can be added later with e.g. `paplay` from a systemd
  timer or from the page — the overlay is already in place.
- **Display rotation**: the 7" panel is landscape by default. For portrait
  mounts, the cleanest path on Bookworm is a `wlr_output_transform` /
  cage transform or rotating via `dtoverlay=vc4-kms-dsi-7inch` — see the
  kiosk page's `POLL`/layout constants when rotating (it is designed for
  800×480).
- **Touch**: the panel's touch input works through cage/libinput out of the
  box. v1 uses it for tooltips only (tap a guest chip / host row); real
  interactions (drill-down pages) are a nice-to-have for later.

## Troubleshooting

```bash
systemctl status hola-backend hola-kiosk
journalctl -u hola-backend -e     # backend + module errors (unreachable sources)
journalctl -u hola-kiosk -e       # cage/Chromium output
curl http://127.0.0.1:8600/api/health     # backend liveness
curl http://127.0.0.1:8600/api/widgets    # what the screen renders
```

- **Black screen**: `journalctl -u hola-kiosk -e` — usually cage failing to
  get DRM master; check that `User=`/`PAMName=login` survived customization
  and no getty grabbed tty1 (`systemctl status getty@tty1`).
- **Screen shows banners only**: backend down — `journalctl -u hola-backend`.
  The page keeps retrying; it recovers automatically.
- **Proxmox tile UNREACHABLE with "setup failed"**: missing
  `.env` values or wrong `plugins.proxmox.host` — the message names the
  missing key.
- After config changes: `sudo systemctl restart hola-backend` (the page picks
  changes up on the next poll; no kiosk restart needed).

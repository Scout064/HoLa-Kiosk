# AGENTS.md — Homelab Status Kiosk (Raspberry Pi 4 Touchscreen)

## Purpose

Build a kiosk-mode homelab status screen running on a dedicated Raspberry Pi 4
(1 GB RAM) fitted with the official 7" touchscreen (800×480, DSI) and a HiFi Berry
stereo DAC HAT. The screen shows at-a-glance health for the homelab: Proxmox
host/VM status, temperatures, network reachability, and Pi-hole stats. It
must run unattended, recover from failures on its own, and not depend on any
service it is monitoring (it should still show "Proxmox is down" even while
Proxmox is down).

## Relationship to existing work

This project should build on **HoLa-Dash**, an existing modular homelab
dashboard project with a plugin architecture and Proxmox REST API
integration (token-based auth), guided by its own `AGENTS.md` brief. Before
writing any new code:

1. Locate and inspect the HoLa-Dash repository and its `AGENTS.md`.
2. Determine whether HoLa-Dash's backend/plugin system (device metrics,
   Proxmox plugin, graceful plugin failure isolation) can be reused directly
   as the data source for this kiosk, exposing a simple JSON or HTML endpoint
   the kiosk can poll.
3. If reuse is impractical (e.g. HoLa-Dash's frontend is too heavy for a
   1 GB Pi 4, or it isn't meant to run standalone on this device), treat
   HoLa-Dash's plugin modules as the reference implementation for talking to
   Proxmox, Pi-hole, and Zyxel devices, and write a lighter-weight kiosk
   backend inspired by that pattern instead of designing the API calls from
   scratch.
4. Do not assume file paths, module names, or API shapes from memory —
   inspect the actual HoLa-Dash code first, the same working principle its
   own AGENTS.md establishes.

If reused as a live dependency, plugin failure isolation is a hard
requirement carried over from HoLa-Dash: one broken data source (e.g.
Pi-hole unreachable) must never crash or blank the whole kiosk display.

## Hardware constraints

- Raspberry Pi 4, 1 GB RAM — keep the software stack light; avoid a full
  desktop environment or Electron-based kiosk shells.
- Official Raspberry Pi 7" touchscreen, 800×480, connected via DSI.
- Stereo DAC HAT (model TBD) — used for optional audible alerts, not
  required for v1.
- Display should be usable with touch (e.g. tap a tile for more detail),
  but touch interaction is a nice-to-have, not the core requirement.

## Requirements

- Kiosk-mode Chromium (via a minimal Wayland compositor such as `cage`),
  auto-starting on boot via systemd, auto-restarting on crash.
- A local backend (reused from HoLa-Dash or purpose-built) that polls, on a
  30–60s interval:
  - Proxmox: node status, CPU/RAM/disk usage, VM/container up-down state,
    via the official REST API with a read-only token.
  - Temperatures: Proxmox host sensors, and optionally the kiosk Pi's own
    CPU temperature.
  - Network: Pi-hole query/block stats via its API, firewall/gateway
    reachability, WAN latency.
  - Other hosts (NAS, other Raspberry Pis on the network): simple up/down
    via ping or port check.
- A single dashboard view, sized for 800×480, with clear color-coded
  status (e.g. green/amber/red) per tile, legible from a few meters away.
- Must degrade gracefully: if a data source is unreachable, its tile shows
  a clear "unreachable" state rather than crashing the page or the backend.
- Runs entirely on the local network; no cloud dependency.

## Non-goals (for v1)

- Historical graphing/trends (HoLa-Dash or a separate tool can own this
  later if wanted).
- Voice or remote control.
- Multi-user auth — this is a single-purpose wall display.

## Suggested approach

1. Inspect HoLa-Dash and decide reuse vs. reference-only (see above).
2. Stand up the backend (Flask, if extending David's existing pattern from
   the Event Inventory project) exposing one endpoint the kiosk polls.
3. Build the dashboard page as static HTML/CSS/JS, auto-refreshing.
4. Package the Pi OS image: Raspberry Pi OS Lite (64-bit), `cage` +
   Chromium kiosk systemd service pointed at `http://localhost:<port>/`.
5. Add the DAC overlay to `config.txt` once the DAC model is confirmed, and
   wire up an optional alert sound for critical failures.

## Open questions to resolve before/while building

- Confirm whether HoLa-Dash's backend can run standalone on this Pi 4, or
  needs to be queried remotely from wherever it's actually hosted.
- Confirm the DAC HAT model, to select the correct `dtoverlay` line.
- Confirm which hosts/services beyond Proxmox and Pi-hole should appear on
  the screen (NAS, Kismet Pi, WoL station Pi, etc.).

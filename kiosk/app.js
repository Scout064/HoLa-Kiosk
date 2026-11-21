/* HoLa Kiosk — display logic.
 *
 * Polls the HoLa-Dash backend (running on this Pi) for /api/widgets and
 * renders color-coded tiles for the 800x480 DSI panel.
 *
 * Degradation rules (kiosk brief):
 *  - a widget whose source is unreachable shows a clear "UNREACHABLE" tile
 *    instead of crashing the page,
 *  - if the backend itself is unreachable, a red banner appears and the last
 *    tiles stay visible (marked stale) while retrying — the page never blanks,
 *  - unknown widget ids render generically instead of being dropped.
 *
 * All dynamic text is inserted via textContent — no innerHTML with data.
 */
"use strict";

const BACKEND = new URLSearchParams(location.search).get("backend")
  || "http://127.0.0.1:8600";
const POLL_SECONDS = 10;
const RELOAD_AFTER_MS = 6 * 60 * 60 * 1000; /* nightly self-recycle */

/* Color thresholds — tune to taste */
const TH = {
  pctWarn: 70, pctBad: 90,        /* CPU/RAM/disk % */
  tempWarn: 60, tempBad: 70,      /* Proxmox host sensors °C */
  piTempWarn: 65, piTempBad: 75,  /* kiosk Pi CPU °C */
  wanWarnMs: 100,                 /* WAN RTT */
};

const SLOTS = {
  "proxmox-nodes": "slot-proxmox-nodes",
  "proxmox-guests": "slot-proxmox-guests",
  "pihole-stats": "slot-pihole-stats",
  "network-hosts": "slot-network-hosts",
  "kiosk-pi": "slot-kiosk-pi",
};

/* ── tiny DOM helpers ──────────────────────────────────────────────── */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pctClass(pct) {
  if (pct === null || pct === undefined || Number.isNaN(pct)) return "na";
  if (pct >= TH.pctBad) return "bad";
  if (pct >= TH.pctWarn) return "warn";
  return "ok";
}

function tempClass(tempC, warn, bad) {
  if (tempC === null || tempC === undefined) return "na";
  if (tempC >= bad) return "bad";
  if (tempC >= warn) return "warn";
  return "ok";
}

function fmtNum(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (!Number.isFinite(value)) return "—";
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function fmtBig(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value >= 1e6) return (value / 1e6).toFixed(1) + "M";
  if (value >= 1e3) return (value / 1e3).toFixed(1) + "k";
  return fmtNum(value);
}

/* Pi-hole entries: numbers get humanized; strings (e.g. "enabled") pass through */
function displayValue(entry) {
  if (typeof entry.value === "number" && Number.isFinite(entry.value))
    return fmtBig(entry.value);
  return fmtNum(entry.value);
}

/* "vaultwarden.example.com" → "vaultwarden" (display only; tooltips keep FQDNs) */
function shortHost(name) {
  const text = String(name || "");
  const dot = text.indexOf(".");
  return dot > 0 ? text.slice(0, dot) : text;
}

function fmtUptime(seconds) {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  if (d > 0) return d + "d " + h + "h";
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return h + "h " + m + "m";
  return m + "m";
}

/* ── renderers ─────────────────────────────────────────────────────── */

function renderBar(label, pct) {
  const cls = pctClass(pct);
  const row = el("div", "bar");
  row.append(el("span", "lbl", label));
  const track = el("div", "track");
  const fill = el("div", "fill " + (cls === "ok" ? "ok" : cls));
  if (typeof pct === "number") fill.style.width = Math.min(100, Math.max(0, pct)) + "%";
  track.append(fill);
  row.append(track, el("span", "val c-" + cls, typeof pct === "number" ? pct + "%" : "—"));
  return row;
}

function renderNodeTile(tile, tempWarn, tempBad) {
  const node = el("div", "tile");
  const head = el("div", "tile-head");

  const name = el("div", "tile-name");
  name.append(el("span", "dot " + (tile.status === "online" ? "ok" : "bad")));
  name.append(el("span", undefined, tile.label));
  head.append(name);

  const info = tile.info || {};
  const temp = info.temp_c !== undefined ? info.temp_c : null;
  const tCls = tempClass(temp, tempWarn, tempBad);
  if (temp !== null) head.append(el("span", "tile-temp c-" + tCls, fmtNum(temp) + "°C"));
  node.append(head);

  node.append(renderBar("CPU", tile.cpu_pct));
  node.append(renderBar("RAM", tile.ram_pct));
  node.append(renderBar("DISK", tile.disk_pct));

  const foot = el("div", "tile-foot");
  const bits = [];
  if (info.temps) bits.push(info.temps);
  if (tile.uptime_seconds !== null && tile.uptime_seconds !== undefined)
    bits.push("up " + fmtUptime(tile.uptime_seconds));
  if (info.load1 !== undefined) bits.push("load " + fmtNum(info.load1));
  foot.append(document.createTextNode(bits.join(" · ")));
  if (info.hint) foot.append(el("div", "c-warn", info.hint));
  node.append(foot);
  return node;
}

function renderProxmoxNodes(widget) {
  const body = el("div", "body tiles");
  const tiles = (widget.data && widget.data.tiles) || [];
  tiles.forEach((tile) => {
    const nodeEl = renderNodeTile(tile, TH.tempWarn, TH.tempBad);
    // tapping a node tile opens the node detail page
    nodeEl.onclick = () => openNodeDetail(tile.id);
    nodeEl.style.cursor = "pointer";
    body.append(nodeEl);
  });
  if (!tiles.length) body.append(el("div", "c-muted", "no nodes"));
  return body;
}

/* latest node data + the selected node (detail page 6) */
let latestNodesWidget = null;
let selectedNodeId = null;

function openNodeDetail(nodeId) {
  selectedNodeId = nodeId;
  renderNodeDetail();
  gotoPage(6);
}

function renderNodeDetail() {
  const box = document.getElementById("nodedetail");
  if (!box) return;
  box.textContent = "";
  const widget = latestNodesWidget;
  if (!widget) {
    box.append(el("div", "c-muted", "no Proxmox data"));
    return;
  }
  if (widget.status !== "ok") {
    const unavail = el("div", "unavail");
    unavail.append(el("span", "u-title", "UNREACHABLE"));
    unavail.append(el("span", "u-msg", widget.error || "no data"));
    box.append(unavail);
    return;
  }

  const tiles = (widget.data && widget.data.tiles) || [];
  const tile = tiles.find((t) => t.id === selectedNodeId);
  if (!tile) {
    box.append(el("div", "c-muted", "tap a node on the dashboard to see its details"));
    return;
  }
  const detail = ((widget.data && widget.data.details) || {})[tile.id] || {};

  const head = el("div", "gd-head");
  head.append(el("span", "dot " + (tile.status === "online" ? "ok" : "bad")));
  head.append(el("span", "name", tile.label));
  head.append(el("span", "status", tile.status));
  box.append(head);

  // live bars
  const barTile = el("div", "tile");
  barTile.style.maxWidth = "420px";
  barTile.append(renderBar("CPU", tile.cpu_pct));
  barTile.append(renderBar("RAM", tile.ram_pct));
  barTile.append(renderBar("DISK", tile.disk_pct));
  box.append(barTile);

  // facts from /nodes/{n}/status (may be empty on limited tokens)
  const stats = el("div", "stats");
  const add = (label, value) => {
    if (value === undefined || value === null || value === "") return;
    const stat = el("div", "stat");
    stat.append(el("span", "label", label));
    stat.append(el("span", "value", String(value)));
    stats.append(stat);
  };
  add("Uptime", tile.uptime_seconds ? fmtUptime(tile.uptime_seconds) : "—");
  add("Kernel", detail.kversion);
  add("PVE", detail.pveversion);
  add("CPU", detail.cpu_model
    ? detail.cpu_model + (detail.cpu_cores ? " (" + detail.cpu_cores + " cores)" : "")
    : undefined);
  add("Load", detail.loadavg);
  if (detail.memory_used_gb != null && detail.memory_total_gb != null) {
    add("RAM", detail.memory_used_gb + " / " + detail.memory_total_gb + " GB");
  }
  if (detail.rootfs_used_gb != null && detail.rootfs_total_gb != null) {
    add("Root FS", detail.rootfs_used_gb + " / " + detail.rootfs_total_gb + " GB");
  }
  if (detail.swap_total_gb != null) {
    add("Swap", (detail.swap_used_gb || 0) + " / " + detail.swap_total_gb + " GB");
  }
  if (stats.childNodes.length) box.append(stats);

  // host sensors
  const thermal = detail.thermal || [];
  if (thermal.length) {
    box.append(el("div", "mgroup", "Sensors"));
    const list = el("div", "top-list");
    thermal.forEach((entry) => {
      const row = el("div", "top-row");
      row.append(el("span", "top-name",
        String(entry.get ? entry.get("type") : entry.type) || "sensor"));
      row.append(el("span", "top-count",
        String(entry.get ? entry.get("value") : entry.value) || "—"));
      list.append(row);
    });
    box.append(list);
  }

  // running guests on this node — chips drill down to the machine page
  const nodeGuests = latestGuestDevices.filter((d) => d.node === tile.id);
  if (nodeGuests.length) {
    box.append(el("div", "mgroup", "Running guests on this node"));
    const cols = el("div", "guest-cols");
    nodeGuests.forEach((device) => cols.append(buildGuestChip(device)));
    box.append(cols);
  }
}

function buildGuestChip(device) {
  const chip = el("div", "chip " + (device.status === "running" ? "run" : "stop"));
  chip.append(el("span", "dot " + (device.status === "running" ? "ok" : "bad")));
  chip.append(el("span", undefined, shortHost(device.name)));
  const metrics = device.metrics || {};
  if (metrics.cpu_pct !== undefined && metrics.cpu_pct !== null) {
    chip.append(el("span", "c-muted", fmtNum(metrics.cpu_pct) + "%"));
  }
  const tip = [device.name, device.id, device.status, device.node || "",
    metrics.cpu_pct !== undefined ? "cpu " + metrics.cpu_pct + "%" : "",
    metrics.mem_pct !== undefined ? "mem " + metrics.mem_pct + "%" : "",
    metrics.uptime_seconds ? "up " + fmtUptime(metrics.uptime_seconds) : ""]
    .filter(Boolean).join(" · ");
  chip.title = tip;
  chip.style.cursor = "pointer";
  chip.onclick = () => openGuestDetail(device);
  return chip;
}

/* latest guest data + the currently selected machine (detail page 4) */
let latestGuestDevices = [];
let selectedGuestId = null;
let lastKnownGuest = null;

function renderGuests(widget) {
  const body = el("div", "body");
  const devices = (widget.data && widget.data.devices) || [];
  latestGuestDevices = devices;

  // all running machines together (regardless of VM/LXC), flowing across
  // two side-by-side rows
  const cols = el("div", "guest-cols");
  devices.forEach((device) => cols.append(buildGuestChip(device)));
  body.append(cols);

  if (!devices.length) body.append(el("div", "c-muted", "no guests"));
  return body;
}

/* ── machine detail page (page 4, opened by tapping a guest chip) ────── */

function openGuestDetail(device) {
  selectedGuestId = device.id;
  renderGuestDetail();
  gotoPage(4);
}

function renderGuestDetail() {
  const box = document.getElementById("guestdetail");
  if (!box) return;
  box.textContent = "";

  // live data if the machine is still in the list; last known otherwise
  const device = latestGuestDevices.find((d) => d.id === selectedGuestId);
  if (device) lastKnownGuest = device;
  const shown = device || lastKnownGuest;

  if (!shown) {
    box.append(el("div", "c-muted",
      "tap a machine on the dashboard to see its details"));
    return;
  }

  const metrics = shown.metrics || {};

  // headline: full name (chips show the short form; here we have space)
  const head = el("div", "gd-head");
  head.append(el("span", "dot " + (shown.status === "running" ? "ok" : "bad")));
  head.append(el("span", "name", shown.name));
  head.append(el("span", "status", shown.status));
  box.append(head);

  if (!device && shown.status !== "running") {
    box.append(el("div", "c-warn", "no longer in the running list"));
  }

  // facts
  const stats = el("div", "stats");
  const add = (label, value) => {
    if (value === undefined || value === null || value === "") return;
    const stat = el("div", "stat");
    stat.append(el("span", "label", label));
    stat.append(el("span", "value", String(value)));
    stats.append(stat);
  };
  add("Type", shown.category === "lxc" ? "Container (LXC)" : "Virtual Machine");
  add("ID", shown.id);
  add("Node", shown.node);
  add("Uptime", metrics.uptime_seconds ? fmtUptime(metrics.uptime_seconds) : "—");
  add("Disk", metrics.disk_gb !== undefined && metrics.disk_gb !== null
    ? fmtNum(metrics.disk_gb) + " GB" : "—");
  if (metrics.mem_used_gb != null && metrics.mem_max_gb != null) {
    add("RAM", fmtNum(metrics.mem_used_gb) + " / " + fmtNum(metrics.mem_max_gb) + " GB");
  }
  add("Tags", (shown.tags || []).join(", "));
  box.append(stats);

  // live bars
  const tile = el("div", "tile");
  tile.style.maxWidth = "420px";
  tile.append(renderBar("CPU", metrics.cpu_pct));
  tile.append(renderBar("RAM", metrics.mem_pct));
  box.append(tile);
}

function renderPihole(widget) {
  const body = el("div", "body stats");
  const groups = (widget.data && widget.data.groups) || [];
  const entries = (groups[0] && groups[0].entries) || [];
  const byLabel = {};
  entries.forEach((entry) => { byLabel[entry.label] = entry; });

  const add = (label, colorClass) => {
    const entry = byLabel[label];
    if (!entry) return;
    const stat = el("div", "stat");
    stat.append(el("span", "label", label));
    const value = el("span", "value", displayValue(entry));
    if (colorClass) value.className += " " + colorClass;
    stat.append(value);
    if (entry.unit) stat.append(el("span", "unit", entry.unit));
    body.append(stat);
  };

  // all live values use the same font size as every other stat on the screen
  add("Queries (24h)");
  add("Blocked (24h)");
  add("Block rate");
  add("Active clients");
  add("Blocklist size");
  const blocking = byLabel["Blocking"];
  if (blocking) {
    const stat = el("div", "stat");
    stat.append(el("span", "label", "Blocking"));
    const on = String(blocking.value) === "enabled";
    stat.append(el("span", "value", on ? "ON" : "OFF"));
    const badge = el("span", "dot " + (on ? "ok" : "bad"));
    stat.append(badge);
    body.append(stat);
  }
  return body;
}

function renderHosts(widget) {
  const body = el("div", "body rows");
  const devices = (widget.data && widget.data.devices) || [];
  devices.forEach((device) => {
    const row = el("div", "row");
    const online = device.status === "online";
    row.append(el("span", "dot " + (online ? "ok" : "bad")));
    row.append(el("span", "name", device.name));
    const latency = device.metrics && device.metrics.latency_ms;
    if (online) {
      let latClass = "c-ok";
      if (device.category === "wan" && latency > TH.wanWarnMs) latClass = "c-warn";
      row.append(el("span", "lat " + latClass,
        typeof latency === "number" ? fmtNum(latency) + " ms" : "up"));
    } else {
      row.append(el("span", "down", "DOWN"));
    }
    row.title = device.id + " · " + device.ip + (device.metrics && device.metrics.probe ? " · " + device.metrics.probe : "");
    body.append(row);

    // WAN row: hourly speedtest stats as a sub-line
    const m = device.metrics || {};
    if (device.category === "wan" &&
        (m.down_mbps !== undefined || m.speedtest_error)) {
      const sub = el("div", "row-sub");
      if (m.down_mbps !== undefined && m.down_mbps !== null) {
        sub.append(el("span", "c-ok", "↓ " + fmtNum(m.down_mbps) +
          " ↑ " + fmtNum(m.up_mbps) + " Mb/s"));
        if (m.speedtest_at) {
          const stamp = String(m.speedtest_at);
          sub.append(el("span", "c-muted",
            " · " + (stamp.length >= 16 ? stamp.slice(11, 16) : stamp)));
        }
      } else if (m.speedtest_error) {
        sub.append(el("span", "c-warn",
          "speedtest: " + String(m.speedtest_error).slice(0, 42)));
      }
      body.append(sub);
    }
  });
  if (!devices.length) body.append(el("div", "c-muted", "no checks configured"));
  return body;
}

function renderKioskPi(widget) {
  const body = el("div", "body");
  const tile = (widget.data && widget.data.tiles && widget.data.tiles[0]) || null;
  const info = (tile && tile.info) || {};

  const temp = info.cpu_temp_c !== undefined ? info.cpu_temp_c : null;
  const tCls = tempClass(temp, TH.piTempWarn, TH.piTempBad);
  body.append(el("span", "foot-temp c-" + tCls, temp !== null ? fmtNum(temp) + "°C" : "—"));

  if (info.load1 !== undefined) body.append(el("span", "foot-load", "load " + fmtNum(info.load1)));

  const barBox = el("div", "tiles");
  barBox.style.flex = "1";
  const ram = el("div", "tile");
  ram.style.flex = "1 1 40%";
  ram.append(renderBar("CPU", tile ? tile.cpu_pct : null));
  ram.append(renderBar("RAM", tile ? tile.ram_pct : null));
  ram.append(renderBar("DISK", tile ? tile.disk_pct : null));
  barBox.append(ram);
  body.append(barBox);

  const up = el("span", "foot-load", "up " + (tile ? fmtUptime(tile.uptime_seconds) : "—"));
  body.append(up);
  return body;
}

function renderGeneric(widget) {
  const body = el("div", "body stats");
  const data = widget.data || {};
  Object.keys(data).forEach((key) => {
    const stat = el("div", "stat");
    stat.append(el("span", "label", key));
    stat.append(el("span", "value", JSON.stringify(data[key]).slice(0, 40)));
    body.append(stat);
  });
  return body;
}

/* Detail views (Network Hardware Metrics, Status Page) open from a shared
   row of tappable chips — one card, two reachability dots. The chips are
   built once; polls and probes only update the dots. */

const detailChipsSlot = document.getElementById("slot-detailchips");
let metricsChipDotEl = null;
let statusChipDotEl = null;

function initDetailChips() {
  detailChipsSlot.hidden = false;
  detailChipsSlot.textContent = "";
  const row = el("div", "chips-row");

  const metricsChip = el("div", "detail-chip");
  metricsChipDotEl = el("span", "dot na");
  metricsChip.append(metricsChipDotEl,
    el("span", undefined, "Network Hardware"),
    el("span", "c-muted", "›"));
  metricsChip.title = "Network hardware metrics detail view";
  metricsChip.onclick = () => gotoPage(3);

  const statusChip = el("div", "detail-chip");
  statusChipDotEl = el("span", "dot na");
  statusChip.append(statusChipDotEl,
    el("span", undefined, "Status Page"),
    el("span", "c-muted", "›"));
  statusChip.title = "External status page";
  statusChip.onclick = () => gotoPage(2);

  row.append(metricsChip, statusChip);
  detailChipsSlot.append(row);
}

function updateMetricsChip(widget) {
  if (!metricsChipDotEl) return;
  const reachable = widget.status === "ok";
  metricsChipDotEl.className = "dot " + (reachable ? "ok" : "bad");
  metricsChipDotEl.parentElement.title =
    widget.error || "Network hardware metrics detail view";
}

/* full metrics detail page content (kept fresh with every poll) */
function renderMetricsDetail(widget) {
  const detail = document.getElementById("metrics-detail");
  if (!detail) return;
  detail.textContent = "";
  if (widget.status !== "ok") {
    const box = el("div", "unavail");
    box.append(el("span", "u-title", "UNREACHABLE"));
    box.append(el("span", "u-msg", widget.error || "no data"));
    detail.append(box);
    return;
  }
  const groups = (widget.data && widget.data.groups) || [];
  groups.forEach((group) => {
    detail.append(el("div", "mgroup", group.label));
    (group.entries || []).forEach((entry) => {
      const stat = el("div", "stat");
      stat.append(el("span", "label", entry.label));
      stat.append(el("span", "value", fmtNum(entry.value)));
      if (entry.unit) stat.append(el("span", "unit", entry.unit));
      detail.append(stat);
    });
  });
  if (!groups.length) detail.append(el("div", "c-muted", "no metrics"));
}

const RENDERERS = {
  "proxmox-nodes": renderProxmoxNodes,
  "proxmox-guests": renderGuests,
  "pihole-stats": renderPihole,
  "network-hosts": renderHosts,
  "kiosk-pi": renderKioskPi,
};

/* ── widget rendering with degradation ─────────────────────────────── */

function renderWidget(widget) {
  // Network Hardware Metrics lives in the shared detail-chips row —
  // update its dot and keep the detail page fresh
  if (widget.id === "prometheus-metrics") {
    updateMetricsChip(widget);
    renderMetricsDetail(widget);
    return;
  }

  const slotId = SLOTS[widget.id];
  let section;
  if (slotId) {
    section = document.getElementById(slotId);
  } else {
    const extra = document.getElementById("extra");
    extra.hidden = false;
    section = el("section", "card");
    extra.append(section);
  }

  section.hidden = false;
  section.textContent = "";
  section.append(el("h2", undefined, widget.title));

  if (widget.status !== "ok") {
    const box = el("div", "unavail");
    box.append(el("span", "u-title", "UNREACHABLE"));
    box.append(el("span", "u-msg", widget.error || "no data"));
    section.append(box);
    return;
  }

  const renderer = RENDERERS[widget.id] || renderGeneric;
  section.append(renderer(widget));

  // node data feeds the node detail page (tiles carry the tap targets)
  if (widget.id === "proxmox-nodes") {
    latestNodesWidget = widget;
  }

  // the Pi-hole card opens its detail page (like the guest chips)
  if (widget.id === "pihole-stats") {
    latestPiholeWidget = widget;
    section.onclick = () => { renderPiholeDetail(); gotoPage(5); };
    section.style.cursor = "pointer";
  }
}

let latestPiholeWidget = null;

/* ── Pi-hole detail page (page 5, opened by tapping the card) ─────────── */

function renderPiholeDetail() {
  const box = document.getElementById("piholedetail");
  if (!box) return;
  box.textContent = "";
  const widget = latestPiholeWidget;
  if (!widget) {
    box.append(el("div", "c-muted", "no Pi-hole data"));
    return;
  }
  if (widget.status !== "ok") {
    const unavail = el("div", "unavail");
    unavail.append(el("span", "u-title", "UNREACHABLE"));
    unavail.append(el("span", "u-msg", widget.error || "no data"));
    box.append(unavail);
    return;
  }

  const groups = (widget.data && widget.data.groups) || [];
  const entries = (groups[0] && groups[0].entries) || [];
  const blocking = {};
  entries.forEach((entry) => { blocking[entry.label] = entry; });

  const head = el("div", "gd-head");
  const blockingOn = blocking["Blocking"] &&
    String(blocking["Blocking"].value) === "enabled";
  head.append(el("span", "dot " + (blockingOn ? "ok" : "bad")));
  head.append(el("span", "name", "Pi-hole"));
  head.append(el("span", "status",
    blockingOn ? "blocking enabled" : "blocking disabled"));
  box.append(head);

  // summary facts
  const stats = el("div", "stats");
  entries.forEach((entry) => {
    const stat = el("div", "stat");
    stat.append(el("span", "label", entry.label));
    const value = el("span", "value", displayValue(entry));
    if (entry.label === "Blocking") {
      value.className += " " + (blockingOn ? "c-ok" : "c-bad");
    }
    stat.append(value);
    if (entry.unit) stat.append(el("span", "unit", entry.unit));
    stats.append(stat);
  });
  box.append(stats);

  // top lists
  const detail = (widget.data && widget.data.detail) || {};
  const addTops = (title, items, labelFn) => {
    if (!items || !items.length) return;
    box.append(el("div", "mgroup", title));
    const list = el("div", "top-list");
    items.forEach((item) => {
      const row = el("div", "top-row");
      row.append(el("span", "top-name", labelFn(item)));
      row.append(el("span", "top-count", fmtBigNum(item.count)));
      list.append(row);
    });
    box.append(list);
  };
  addTops("Top domains", detail.top_domains, (i) => i.domain);
  addTops("Top clients", detail.top_clients, (i) => i.name || i.ip);
}

/* "2,977" with thousands separators — the detail page has the space */
function fmtBigNum(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return value.toLocaleString("en-US");
}

function hideEmptySlots(presentIds) {
  Object.values(SLOTS).forEach((slotId) => {
    const section = document.getElementById(slotId);
    if (section && !presentIds.has(section.id.replace(/^slot-/, ""))) section.hidden = true;
  });
}

/* ── polling ───────────────────────────────────────────────────────── */

const connDot = document.getElementById("conn");
const banner = document.getElementById("banner");
const staleNote = document.getElementById("stale-note");
const cards = () => document.querySelectorAll(".card");

async function poll() {
  try {
    const response = await fetch(BACKEND + "/api/widgets", { cache: "no-store" });
    if (!response.ok) throw new Error("HTTP " + response.status);
    const body = await response.json();

    banner.hidden = true;
    staleNote.hidden = true;
    connDot.className = "dot ok";
    connDot.title = "backend ok";
    cards().forEach((card) => card.classList.remove("stale"));

    document.getElementById("extra").textContent = "";
    document.getElementById("extra").hidden = true;

    const present = new Set();
    (body.widgets || []).forEach((widget) => {
      present.add(widget.id);
      renderWidget(widget);
    });
    hideEmptySlots(present);
    // keep the open detail pages live
    if (currentPage === 4) renderGuestDetail();
    if (currentPage === 5) renderPiholeDetail();
    if (currentPage === 6) renderNodeDetail();
  } catch (error) {
    banner.hidden = false;
    staleNote.hidden = false;
    connDot.className = "dot bad";
    connDot.title = "backend unreachable";
    cards().forEach((card) => card.classList.add("stale"));
    /* keep last rendered tiles — the screen never blanks */
  }
}

function tickClock() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  document.getElementById("clock").textContent =
    pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
}

tickClock();
setInterval(tickClock, 1000);
poll();
setInterval(poll, POLL_SECONDS * 1000);
setTimeout(() => location.reload(), RELOAD_AFTER_MS);

/* ── pages: dashboard (1), machine details (4), metrics (3), status (2) ─ */

const PAGE2_URL = new URLSearchParams(location.search).get("page2")
  || "http://127.0.0.1:8601/status/internal";
document.getElementById("status-frame").src = PAGE2_URL;

const swipeEl = document.getElementById("swipe");
const dotsEl = document.getElementById("page-dots");
let currentPage = 1;
// visual order of the pages in the slider
const PAGE_ORDER = [1, 4, 5, 6, 3, 2];

/* keep the slider geometry in sync with the page count — translateX
   percentages resolve against the container's own width, so a stale
   width (300% for four pages) splits pages across the screen. The CSS
   defaults match; this makes adding/removing pages a one-line change. */
swipeEl.style.width = (PAGE_ORDER.length * 100) + "%";
document.querySelectorAll(".page").forEach((page) => {
  page.style.flex = "0 0 " + (100 / PAGE_ORDER.length) + "%";
  page.style.width = (100 / PAGE_ORDER.length) + "%";
});

function gotoPage(n) {
  currentPage = n;
  const index = PAGE_ORDER.indexOf(n);
  swipeEl.style.transform =
    "translateX(-" + (index * (100 / PAGE_ORDER.length)) + "%)";
  document.body.classList.toggle("page2", n === 2);
  document.body.classList.toggle("page3", n === 3);
  document.body.classList.toggle("page4", n === 4);
  document.body.classList.toggle("page5", n === 5);
  document.body.classList.toggle("page6", n === 6);
  dotsEl.querySelectorAll(".dot-btn").forEach((btn) => {
    btn.classList.toggle("active", Number(btn.dataset.page) === n);
  });
  if (n === 2) {
    // re-entering reloads the status page: fresh content, and a retry
    // if the service was unreachable on the previous visit
    document.getElementById("status-frame").src = PAGE2_URL;
  }
}

dotsEl.querySelectorAll(".dot-btn").forEach((btn) => {
  btn.addEventListener("click", () => gotoPage(Number(btn.dataset.page)));
});
document.getElementById("status-strip").addEventListener("click", () => gotoPage(1));
document.getElementById("metrics-strip").addEventListener("click", () => gotoPage(1));
document.getElementById("guestdetail-strip").addEventListener("click", () => gotoPage(1));
document.getElementById("piholedetail-strip").addEventListener("click", () => gotoPage(1));
document.getElementById("nodedetail-strip").addEventListener("click", () => gotoPage(1));

/* touch swipe on the parent's own surfaces (the dashboard, metrics and
   strips; touches inside the cross-origin iframe stay in the iframe) */
let touchStart = null;
swipeEl.addEventListener("touchstart", (e) => {
  if (e.touches.length === 1) {
    touchStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
  }
}, { passive: true });
swipeEl.addEventListener("touchend", (e) => {
  if (!touchStart || !e.changedTouches.length) return;
  const dx = e.changedTouches[0].clientX - touchStart.x;
  const dy = e.changedTouches[0].clientY - touchStart.y;
  touchStart = null;
  if (Math.abs(dx) < 50 || Math.abs(dx) < 1.5 * Math.abs(dy)) return;
  const index = PAGE_ORDER.indexOf(currentPage);
  if (dx < 0 && index < PAGE_ORDER.length - 1) {
    gotoPage(PAGE_ORDER[index + 1]);      // swipe left → next page
  } else if (dx > 0 && index > 0) {
    gotoPage(PAGE_ORDER[index - 1]);      // swipe right → previous page
  }
});

document.addEventListener("keydown", (e) => {
  const index = PAGE_ORDER.indexOf(currentPage);
  if (e.key === "ArrowRight" && index < PAGE_ORDER.length - 1) {
    gotoPage(PAGE_ORDER[index + 1]);
  }
  if (e.key === "ArrowLeft" && index > 0) {
    gotoPage(PAGE_ORDER[index - 1]);
  }
});

/* Status Page reachability: probe through the local proxy (it adds CORS
   so the real status is readable) — green only when the upstream answers. */
async function probeStatus() {
  if (!statusChipDotEl) return;
  let ok = false;
  try {
    const res = await fetch(PAGE2_URL, { method: "HEAD", cache: "no-store" });
    ok = res.ok;   // 502 from the proxy (upstream down) counts as unreachable
  } catch (err) {
    ok = false;    // proxy or network down
  }
  statusChipDotEl.className = "dot " + (ok ? "ok" : "bad");
  statusChipDotEl.parentElement.title =
    ok ? "External status page" : "Status page unreachable";
}

initDetailChips();
probeStatus();
setInterval(probeStatus, POLL_SECONDS * 1000);

/* exposed for the headless render check */
window.__holaKiosk = { gotoPage: gotoPage, page: () => currentPage };

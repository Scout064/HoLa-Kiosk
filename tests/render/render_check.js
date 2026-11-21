/* Headless render check for the kiosk page (optional dev tool — no Node
 * tooling lives in this repo; install jsdom ad hoc):
 *
 *   mkdir /tmp/jsdom && cd /tmp/jsdom && npm init -y && npm i jsdom
 *   node tests/render/render_check.js <kiosk repo>/kiosk --jsdom-dir /tmp/jsdom
 *
 * Loads kiosk/index.html + app.js into jsdom, feeds the saved mock payload
 * (tests/render/widgets.json — regenerate from tests/render/mock_backend.py),
 * and asserts every renderer produced the expected DOM — including the
 * UNREACHABLE degradation card.
 *
 * Not part of pytest: the kiosk page intentionally ships without Node deps.
 */
const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);
const dirFlag = args.indexOf("--jsdom-dir");
const JSDOM_DIR = dirFlag !== -1 ? args[dirFlag + 1] : ".";
const positional = args.filter((a, i) => !a.startsWith("--") && i !== dirFlag + 1);
const KIOSK_DIR = positional[0] || path.join(__dirname, "..", "..", "kiosk");

const { JSDOM } = require(path.join(JSDOM_DIR, "node_modules", "jsdom"));

const html = fs.readFileSync(path.join(KIOSK_DIR, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(KIOSK_DIR, "app.js"), "utf8");

// payload: from the running mock backend, or the saved copy
let widgets;
try {
  widgets = JSON.parse(
    fs.readFileSync(path.join(__dirname, "widgets.json"), "utf8")
  ).widgets;
} catch (err) {
  console.error("read widgets.json failed (regenerate it — see header comment):", err.message);
  process.exit(2);
}

const dom = new JSDOM(html, {
  runScripts: "outside-only",
  pretendToBeVisual: true,
  url: "http://kiosk.local/index.html",
});
const { window } = dom;

let fetchCalls = 0;
window.fetch = (url) => {
  fetchCalls += 1;
  return Promise.resolve({ ok: true, status: 200,
                           json: () => Promise.resolve({ widgets }) });
};
if (!window.URLSearchParams) window.URLSearchParams = URLSearchParams;

window.eval(appJs);

const $ = (sel) => window.document.querySelector(sel);
const $$ = (sel) => window.document.querySelectorAll(sel);

let failures = [];
const check = (name, cond) => {
  if (cond) console.log("  ok:", name);
  else { console.log("  FAIL:", name); failures.push(name); }
};

setTimeout(() => {
  console.log("kiosk page render check");
  check("header clock ticks", /^(\d{2}:){2}\d{2}$/.test($("#clock").textContent));
  check("connection dot ok", $("#conn").className.includes("ok"));
  check("banner hidden on success", $("#banner").hidden === true);

  check("nodes card visible", !$("#slot-proxmox-nodes").hidden);
  check("2 node tiles", $$("#slot-proxmox-nodes .tile").length === 2);
  const temps = $$("#slot-proxmox-nodes .tile-temp");
  check("cool node temp ok class", temps[0].className.includes("c-ok"));
  check("hot node temp bad class", temps[1].className.includes("c-bad"));
  check("ram bar >90 colored bad", $$("#slot-proxmox-nodes .fill")[4].className.includes("bad"));

  check("guests card visible", !$("#slot-proxmox-guests").hidden);
  check("two-row flow container", !!$("#slot-proxmox-guests .guest-cols"));
  check("no type group labels", $$("#slot-proxmox-guests .glabel").length === 0);
  check("5 guest chips", $$("#slot-proxmox-guests .chip").length === 5);
  const fqdnChip = $$("#slot-proxmox-guests .chip")[1];
  check("fqdn shortened in chip", fqdnChip.textContent.startsWith("zynk") &&
        !fqdnChip.textContent.includes("example.com"));
  check("fqdn full name in tooltip", fqdnChip.title.includes("zynk.example.com"));
  check("stopped chip red", $$("#slot-proxmox-guests .chip")[3].className.includes("stop"));
  check("chip tooltip present", !!$$("#slot-proxmox-guests .chip")[0].title);

  check("pihole card visible", !$("#slot-pihole-stats").hidden);
  check("6 pihole stats", $$("#slot-pihole-stats .stat").length === 6);
  check("queries value formatted", $("#slot-pihole-stats .stat .value").textContent === "12.3k");
  check("blocking shows ON", $("#slot-pihole-stats .stat:last-child .value").textContent === "ON");

  check("network card visible", !$("#slot-network-hosts").hidden);
  const rows = $$("#slot-network-hosts .row");
  check("3 host rows", rows.length === 3);
  check("gateway latency shown", rows[0].textContent.includes("0.4 ms"));
  check("WAN latency amber", rows[1].querySelector(".lat").className.includes("c-warn"));
  check("NAS shows DOWN", rows[2].textContent.includes("DOWN"));
  const sub = $("#slot-network-hosts .row-sub");
  check("WAN speedtest sub-line", !!sub);
  check("sub-line shows speeds", sub.textContent.includes("↓ 45.2 ↑ 12.1 Mb/s"));
  check("sub-line shows run time", sub.textContent.includes("11:02"));

  const foot = $("#slot-kiosk-pi");
  check("footer visible", !foot.hidden);
  check("footer temp", foot.querySelector(".foot-temp").textContent === "51.2°C");
  check("footer load", foot.textContent.includes("load 0.8"));
  check("footer uptime", foot.textContent.includes("up 3d 4h"));
  check("footer CPU/RAM/DISK bars", foot.querySelectorAll(".bar").length === 3);
  check("footer CPU value", foot.textContent.includes("18.4%"));

  check("fetch was performed", fetchCalls >= 1);

  // ── swipe pages / machine detail / metrics / status view ───────────────
  check("six swipe pages", $$(".page").length === 6);
  check("slider geometry matches page count",
        $("#swipe").style.width === "600%" &&
        $$(".page")[0].style.width.startsWith("16.6"));
  check("status iframe has URL",
        $("#status-frame").src === "http://127.0.0.1:8601/status/internal");
  check("starts on page 1", window.__holaKiosk.page() === 1 &&
        !window.document.body.className.includes("page"));

  // machine details: tapping a guest chip opens its page
  const guestChip = $$("#slot-proxmox-guests .chip")[1];   // zynk.example.com
  guestChip.onclick();
  check("tap opens machine detail", window.__holaKiosk.page() === 4 &&
        window.document.body.className.includes("page4") &&
        $("#swipe").style.transform.includes("-16.6"));
  const gd = $("#guestdetail");
  check("detail shows full name", gd.textContent.includes("zynk.example.com"));
  check("detail shows status running", gd.querySelector(".gd-head .status").textContent === "running");
  check("detail facts", gd.textContent.includes("Virtual Machine") &&
        gd.textContent.includes("vm/101") &&
        gd.textContent.includes("pve1"));
  check("detail bars", gd.querySelectorAll(".bar").length === 2);
  check("detail CPU value", gd.textContent.includes("1.2%"));
  check("page 4 dot active",
        $("#page-dots .dot-btn[data-page='4']").className.includes("active"));
  check("machine detail strip exists", !!$("#guestdetail-strip"));
  $("#guestdetail-strip").dispatchEvent(new window.MouseEvent("click"));
  check("machine detail close returns", window.__holaKiosk.page() === 1);

  // Pi-hole details: tapping the card opens its page
  $("#slot-pihole-stats").onclick();
  check("tap opens pihole detail", window.__holaKiosk.page() === 5 &&
        window.document.body.className.includes("page5") &&
        $("#swipe").style.transform.includes("-33.3"));
  const ph = $("#piholedetail");
  check("pihole head", ph.querySelector(".gd-head .status").textContent === "blocking enabled");
  check("pihole summary stats", ph.textContent.includes("Queries (24h)") &&
        ph.textContent.includes("12.3k"));
  check("pihole top domains", ph.textContent.includes("nc.example.com") &&
        ph.textContent.includes("2,977"));
  check("pihole top clients", ph.textContent.includes("monitor.example.com") &&
        ph.textContent.includes("18,689"));
  check("page 5 dot active",
        $("#page-dots .dot-btn[data-page='5']").className.includes("active"));
  $("#piholedetail-strip").dispatchEvent(new window.MouseEvent("click"));
  check("pihole detail close returns", window.__holaKiosk.page() === 1);

  // Proxmox node details: tapping a node tile opens its page
  const nodeTile = $$("#slot-proxmox-nodes .tile")[0];   // pve1
  nodeTile.onclick();
  check("tap opens node detail", window.__holaKiosk.page() === 6 &&
        window.document.body.className.includes("page6") &&
        $("#swipe").style.transform.includes("-50"));
  const nd = $("#nodedetail");
  check("node head", nd.querySelector(".gd-head .name").textContent === "pve1" &&
        nd.querySelector(".gd-head .status").textContent === "online");
  check("node facts", nd.textContent.includes("Linux 6.8.12-1-pve") &&
        nd.textContent.includes("pve-manager/8.2.4") &&
        nd.textContent.includes("Intel N100 (4 cores)"));
  check("node bars", nd.querySelectorAll(".bar").length === 3);
  check("node sensors", nd.textContent.includes("Board") &&
        nd.textContent.includes("38.0°C"));
  check("node guest chips", nd.querySelectorAll(".chip").length === 2);
  check("page 6 dot active",
        $("#page-dots .dot-btn[data-page='6']").className.includes("active"));
  $("#nodedetail-strip").dispatchEvent(new window.MouseEvent("click"));
  check("node detail close returns", window.__holaKiosk.page() === 1);

  // shared detail-chips row (Network Hardware / Status Page)
  const chipsRow = $("#slot-detailchips");
  check("detail chips card visible", !chipsRow.hidden);
  const chips = $$(".detail-chip");
  check("two detail chips", chips.length === 2);
  check("chip labels", chips[0].textContent.includes("Network Hardware") &&
        chips[1].textContent.includes("Status Page"));
  check("metrics chip dot red (unreachable)",
        chips[0].querySelector(".dot").className.includes("bad"));
  check("status chip dot green (probe resolved)",
        chips[1].querySelector(".dot").className.includes("ok"));
  check("metrics detail shows error",
        $("#metrics-detail .u-title").textContent === "UNREACHABLE" &&
        $("#metrics-detail .u-msg").textContent.includes("prometheus host unreachable"));

  // tapping the metrics chip opens the detail page
  chips[0].onclick();
  check("tap opens metrics page", window.__holaKiosk.page() === 3 &&
        window.document.body.className.includes("page3"));
  check("metrics transform", $("#swipe").style.transform.includes("-66.6"));
  check("page 3 dot active",
        $("#page-dots .dot-btn[data-page='3']").className.includes("active"));
  check("metrics strip exists", !!$("#metrics-strip"));

  // closing returns to the dashboard
  $("#metrics-strip").dispatchEvent(new window.MouseEvent("click"));
  check("strip close returns to dashboard", window.__holaKiosk.page() === 1);

  // tapping the status chip opens the status page
  chips[1].onclick();
  check("tap opens status page", window.__holaKiosk.page() === 2 &&
        window.document.body.className.includes("page2") &&
        $("#swipe").style.transform.includes("-83.3"));
  check("page 2 dot active",
        $("#page-dots .dot-btn[data-page='2']").className.includes("active"));
  check("status strip exists", !!$("#status-strip"));

  window.__holaKiosk.gotoPage(1);
  check("gotoPage(1) returns", window.__holaKiosk.page() === 1 &&
        !window.document.body.className.includes("page"));

  if (failures.length) {
    console.log("RESULT:", failures.length, "failures");
    process.exit(1);
  }
  console.log("RESULT: all render checks passed");
  process.exit(0);
}, 300);

#!/usr/bin/env bash
# HoLa Kiosk installer — Raspberry Pi OS Lite (64-bit), Bookworm or newer.
#
# Copies HoLa_Dash + HoLa_Kiosk to /opt/hola, creates the Python venv,
# installs the systemd services (backend + cage/Chromium kiosk) and appends
# the boot-config snippet (DSI panel + HiFiBerry DAC overlay).
#
# Usage (from the HoLa_Kiosk repo, as root or with sudo):
#   sudo ./deploy/install.sh [--dash-dir PATH] [--kiosk-dir PATH] \
#                            [--target /opt/hola] [--service-user pi]
#
# Defaults: dash dir = sibling ../HoLa_Dash, kiosk dir = this repo,
#           target = /opt/hola, service user = the sudo-invoking user.
#
# After the install:
#   1. edit /opt/hola/HoLa_Kiosk/.env            (Proxmox token, Pi-hole password)
#   2. edit /opt/hola/HoLa_Kiosk/config/settings.yaml (IPs, hosts)
#   3. sudo reboot
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIOSK_DIR="$(dirname "$SCRIPT_DIR")"

DASH_DIR="$KIOSK_DIR/../HoLa_Dash"
TARGET="/opt/hola"
SERVICE_USER=""

usage() { grep '^#' "$0" | sed -n '2,16p'; exit 2; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dash-dir)     DASH_DIR="$2"; shift 2 ;;
    --kiosk-dir)     KIOSK_DIR="$2"; shift 2 ;;
    --target)        TARGET="$2"; shift 2 ;;
    --service-user)  SERVICE_USER="$2"; shift 2 ;;
    -h|--help)       usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done

[[ "$(id -u)" -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

# Invoking user (for systemd) — defaults to 'pi' under plain sudo
[[ -n "$SERVICE_USER" ]] || SERVICE_USER="${SUDO_USER:-pi}"
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

[[ -d "$DASH_DIR/backend/app" ]] || { echo "HoLa_Dash backend not found at: $DASH_DIR" >&2; exit 1; }
[[ -f "$KIOSK_DIR/kiosk/index.html" ]] || { echo "HoLa_Kiosk page not found at: $KIOSK_DIR" >&2; exit 1; }

echo "==> HoLa_Dash : $DASH_DIR"
echo "==> HoLa_Kiosk : $KIOSK_DIR"
echo "==> install to : $TARGET (services run as $SERVICE_USER)"

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
  echo "WARNING: this targets a 64-bit Raspberry Pi; you are on $(uname -s)/$(uname -m)" >&2
  echo "         continuing anyway (--target will still be written)" >&2
fi

echo "==> installing system packages (cage, chromium, python venv)"
# DPkg::Lock::Timeout: post-boot apt maintenance (unattended-upgrades) can
# hold the dpkg lock for minutes — wait instead of failing.
APT_OPTS=(-o DPkg::Lock::Timeout=600 -qq)
apt-get "${APT_OPTS[@]}" update
apt-get "${APT_OPTS[@]}" install -y cage chromium || apt-get "${APT_OPTS[@]}" install -y cage chromium-browser
apt-get "${APT_OPTS[@]}" install -y python3-venv python3-pip rsync >/dev/null

CHROMIUM="$(command -v chromium || command -v chromium-browser || true)"
[[ -n "$CHROMIUM" ]] || { echo "chromium binary not found" >&2; exit 1; }
echo "==> chromium: $CHROMIUM"

echo "==> copying repos to $TARGET"
mkdir -p "$TARGET"
# dist ships with the repo when present — the backend then also serves the
# full dashboard UI on the same port (config/dist ownership follow the
# service user, no manual chown needed)
rsync -a --delete \
  --exclude '.venv' --exclude 'node_modules' --exclude '__pycache__' \
  --exclude '.pytest_cache' --exclude '.git' \
  --exclude '.env' --exclude 'data' \
  "$DASH_DIR/" "$TARGET/HoLa_Dash/"
# keep the installed kiosk config on re-runs (users edit /opt/hola/… in place)
KIOSK_EXCLUDES=(--exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache'
                 --exclude '.git' --exclude '.env' --exclude 'data')
if [[ -f "$TARGET/HoLa_Kiosk/config/settings.yaml" ]]; then
  KIOSK_EXCLUDES+=(--exclude 'config/settings.yaml')
fi
rsync -a --delete "${KIOSK_EXCLUDES[@]}" "$KIOSK_DIR/" "$TARGET/HoLa_Kiosk/"

echo "==> creating python venv"
python3 -m venv "$TARGET/venv"
"$TARGET/venv/bin/pip" install --upgrade --quiet pip
"$TARGET/venv/bin/pip" install --quiet "$TARGET/HoLa_Dash/backend"
"$TARGET/venv/bin/python" -c "import app, plugins; print('backend import OK')"

# kiosk extra: bandwidth measurement for the WAN row (hosts module)
"$TARGET/venv/bin/pip" install --quiet speedtest-cli \
  || echo "    WARNING: speedtest-cli not installed" >&2

mkdir -p "$TARGET/data"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$TARGET/data"

if [[ ! -f "$TARGET/HoLa_Kiosk/.env" ]]; then
  cp "$TARGET/HoLa_Kiosk/.env.example" "$TARGET/HoLa_Kiosk/.env"
  chown "$SERVICE_USER:$SERVICE_GROUP" "$TARGET/HoLa_Kiosk/.env"
  chmod 600 "$TARGET/HoLa_Kiosk/.env"
fi

# The HoLa-Dash backend resolves ${VAR} secrets from the .env at ITS repo
# root — keep one canonical .env in the kiosk repo and link it there.
if [[ ! -e "$TARGET/HoLa_Dash/.env" ]]; then
  ln -sfn "$TARGET/HoLa_Kiosk/.env" "$TARGET/HoLa_Dash/.env"
fi

echo "==> installing systemd units"
# the status-proxy target is user state: keep the value already deployed
# (the shipped template carries a placeholder)
PROXY_TARGET="$(sed -n 's/^Environment=STATUS_PROXY_TARGET=//p' /etc/systemd/system/hola-status-proxy.service 2>/dev/null | head -1)"
if [[ -z "$PROXY_TARGET" ]]; then
  PROXY_TARGET="$(sed -n 's/^Environment=STATUS_PROXY_TARGET=//p' "$SCRIPT_DIR/hola-status-proxy.service" | head -1)"
fi
for unit in hola-backend hola-kiosk hola-status-proxy; do
  sed -e "s|__TARGET__|$TARGET|g" \
      -e "s|__CHROMIUM__|$CHROMIUM|g" \
      -e "s|__SERVICE_USER__|$SERVICE_USER|g" \
      -e "s|__SERVICE_GROUP__|$SERVICE_GROUP|g" \
      -e "s|__STATUS_TARGET__|$PROXY_TARGET|g" \
      "$SCRIPT_DIR/$unit.service" > "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
# disable first so re-enabling regenerates the wants-symlink after a
# WantedBy= change in the unit templates (stale symlinks keep old targets)
systemctl disable hola-backend.service hola-kiosk.service hola-status-proxy.service 2>/dev/null || true
systemctl enable --now hola-backend.service
systemctl enable --now hola-status-proxy.service
systemctl enable hola-kiosk.service   # starts at boot + next restart (takes tty1)

echo "==> boot config (DSI panel + HiFiBerry DAC)"
CONFIG_TXT=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
  [[ -f "$candidate" ]] && CONFIG_TXT="$candidate" && break
done
if [[ -n "$CONFIG_TXT" ]]; then
  if ! grep -q "HoLa Kiosk (appended by deploy/install.sh" "$CONFIG_TXT"; then
    cat "$SCRIPT_DIR/config.txt.snippet" >> "$CONFIG_TXT"
    echo "    appended snippet to $CONFIG_TXT"
  else
    echo "    snippet already present in $CONFIG_TXT"
  fi
else
  echo "    WARNING: no config.txt found — add deploy/config.txt.snippet manually" >&2
fi

cat <<'EOF'

==> DONE. Next steps:

  1. sudo nano /opt/hola/HoLa_Kiosk/.env
       PROXMOX_TOKEN_NAME / PROXMOX_TOKEN_VALUE  (read-only token is enough)
       PIHOLE_PASSWORD                              (empty if Pi-hole has none)
  2. sudo nano /opt/hola/HoLa_Kiosk/config/settings.yaml
       Proxmox host, Prometheus URL, Pi-hole URL, host check IPs
  3. sudo reboot  (the kiosk takes over tty1 via cage + Chromium)

Troubleshooting:
  systemctl status hola-backend hola-kiosk
  journalctl -u hola-backend -e      # backend log
  journalctl -u hola-kiosk -e       # cage/Chromium log
EOF

#!/usr/bin/env bash
# Aura appliance installer for Debian 13 (trixie) on any x86_64 PC or Intel Mac (reference target: Mac mini 2012, A1347).
#
# Run as root on a fresh minimal Debian install (no desktop):
#   curl -fsSL https://raw.githubusercontent.com/<you>/aura/main/os/install.sh | bash
# or from a local checkout:
#   sudo bash os/install.sh
#
# Idempotent: safe to re-run. Environment overrides: AURA_REPO, AURA_BRANCH, AURA_SRC (local path).
set -euo pipefail

AURA_REPO="${AURA_REPO:-https://github.com/matteo-pollo/aura.git}"
AURA_BRANCH="${AURA_BRANCH:-main}"
AURA_SRC="${AURA_SRC:-}"
APP_DIR=/opt/aura
DATA_DIR=/var/lib/aura
APP_USER=aura
KIOSK_USER=tv
HOSTNAME_DEFAULT=aura

log() { printf '\033[1;33m[aura]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[aura] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (sudo bash os/install.sh)"
[[ -r /etc/os-release ]] && . /etc/os-release
[[ "${ID:-}" == "debian" ]] || log "warning: designed for Debian 13, detected ${PRETTY_NAME:-unknown}"
export DEBIAN_FRONTEND=noninteractive

# ------------------------------------------------------------------ 1. apt sources (contrib + non-free-firmware for Broadcom)
log "Configuring apt (contrib, non-free, non-free-firmware)"
if [[ -f /etc/apt/sources.list.d/debian.sources ]]; then
  sed -i 's/^Components: .*/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources
elif [[ -f /etc/apt/sources.list ]]; then
  sed -i -E 's/^(deb\s+\S+\s+\S+\s+main)(.*)$/\1 contrib non-free non-free-firmware/' /etc/apt/sources.list
fi
apt-get update -qq

# ------------------------------------------------------------------ 2. packages
log "Installing packages"
apt-get install -y -qq --no-install-recommends \
  ca-certificates curl gnupg git sudo \
  python3 python3-venv python3-pip \
  network-manager avahi-daemon libnss-mdns dnsmasq-base \
  firmware-linux firmware-linux-nonfree firmware-misc-nonfree firmware-iwlwifi firmware-realtek firmware-atheros \
  firmware-brcm80211 firmware-b43-installer b43-fwcutter firmware-amd-graphics firmware-intel-sound firmware-sof-signed \
  cage seatd libgl1-mesa-dri mesa-va-drivers mesa-vulkan-drivers i965-va-driver intel-media-va-driver libva2 libva-drm2 vainfo \
  pipewire pipewire-pulse pipewire-audio wireplumber alsa-utils pulseaudio-utils \
  mpv nodejs fonts-noto-core fonts-noto-color-emoji fonts-liberation \
  xdg-utils libu2f-udev libvulkan1 unzip plymouth plymouth-themes

# Google Chrome (Widevine DRM for Netflix / Prime / Canal+; Debian's chromium has no Widevine)
if ! command -v google-chrome >/dev/null 2>&1; then
  log "Installing Google Chrome"
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list
  apt-get update -qq
  apt-get install -y -qq google-chrome-stable
fi

# uv (fast Python package manager)
if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh
fi

# ------------------------------------------------------------------ 3. Wi-Fi (Mac mini 2012 = BCM4331 -> b43)
log "Wi-Fi drivers"
apt-get install -y -qq pciutils usbutils >/dev/null 2>&1 || true
# NVIDIA desktops: nouveau (in mesa) is enough for a 1080p kiosk; the proprietary driver is optional.
if lspci -nn 2>/dev/null | grep -qi 'nvidia'; then
  log "NVIDIA GPU detected: using the open nouveau driver (install nvidia-driver manually if needed)"
fi
if lspci -nn 2>/dev/null | grep -qi 'BCM4331\|14e4:4331'; then
  log "BCM4331 detected: using b43 (firmware installed by firmware-b43-installer)"
  echo "blacklist wl" > /etc/modprobe.d/aura-broadcom.conf
  echo "blacklist brcmsmac" >> /etc/modprobe.d/aura-broadcom.conf
  # b43 + hostapd/AP mode is flaky; NetworkManager handles it, we just make sure the module is loaded at boot
  echo "b43" > /etc/modules-load.d/aura-b43.conf
elif lspci -nn 2>/dev/null | grep -qi 'BCM4360\|14e4:43a0'; then
  log "BCM4360 detected (Mac mini 2014): installing broadcom-sta-dkms (wl)"
  apt-get install -y -qq "linux-headers-$(uname -r)" broadcom-sta-dkms || log "warning: broadcom-sta-dkms failed, check docs"
elif lspci -nn 2>/dev/null | grep -qi 'BCM4364\|14e4:4464'; then
  log "BCM4364 detected (T2 Mac): see https://github.com/frogro/bcm4364-wifi-wrapper (kernel >= 6.8 required)"
fi

# ------------------------------------------------------------------ 4. users
log "Users"
id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
id -u "$KIOSK_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$KIOSK_USER"
usermod -aG video,render,audio,input,seat "$KIOSK_USER" 2>/dev/null || usermod -aG video,render,audio,input "$KIOSK_USER"
usermod -aG audio,video "$APP_USER"
mkdir -p "$DATA_DIR" /run/aura
chown -R "$APP_USER:$APP_USER" "$DATA_DIR"

# ------------------------------------------------------------------ 5. application code
log "Application code -> $APP_DIR"
if [[ -n "$AURA_SRC" ]]; then
  mkdir -p "$APP_DIR"
  rsync -a --delete --exclude .venv --exclude data --exclude .git "$AURA_SRC"/ "$APP_DIR"/ 2>/dev/null || cp -a "$AURA_SRC"/. "$APP_DIR"/
elif [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch -q origin "$AURA_BRANCH" && git -C "$APP_DIR" reset -q --hard "origin/$AURA_BRANCH"
else
  git clone -q --depth 1 -b "$AURA_BRANCH" "$AURA_REPO" "$APP_DIR"
fi
cd "$APP_DIR"
uv venv -q --python 3 .venv
uv pip install -q --python .venv/bin/python -e .
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod +x os/scripts/*.sh

# ------------------------------------------------------------------ 6. system config
log "Hostname, mDNS, network"
CURRENT_HOST=$(hostname)
if [[ "$CURRENT_HOST" == "debian" || "$CURRENT_HOST" == "localhost" ]]; then
  hostnamectl set-hostname "$HOSTNAME_DEFAULT"
  sed -i "s/127.0.1.1.*/127.0.1.1\t$HOSTNAME_DEFAULT/" /etc/hosts || true
fi
# NetworkManager manages everything (disable ifupdown interfaces except lo)
if [[ -f /etc/network/interfaces ]]; then
  sed -i -E 's/^(auto|allow-hotplug|iface) +(en|eth|wl)/#&/' /etc/network/interfaces
fi
cat > /etc/NetworkManager/conf.d/aura.conf <<'EOF'
[main]
dns=default
[connection]
wifi.powersave=2
[device]
wifi.scan-rand-mac-address=no
EOF
# Captive-portal style DNS for the onboarding hotspot (NM's shared mode uses dnsmasq)
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
echo "address=/#/10.42.0.1" > /etc/NetworkManager/dnsmasq-shared.d/aura-captive.conf
systemctl enable --now NetworkManager avahi-daemon >/dev/null

log "sudoers for the service and kiosk users"
cat > /etc/sudoers.d/aura <<EOF
$APP_USER ALL=(root) NOPASSWD: /usr/bin/nmcli, /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/systemctl restart aura-kiosk.service, /usr/bin/systemctl restart aura.service, $APP_DIR/os/scripts/update.sh
EOF
chmod 440 /etc/sudoers.d/aura
# nmcli is called without sudo from python; allow the service user to control NM via polkit
cat > /etc/polkit-1/rules.d/50-aura-nm.rules <<EOF
polkit.addRule(function(action, subject) {
  if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 && subject.user === "$APP_USER") {
    return polkit.Result.YES;
  }
});
EOF

log "Power / console / boot"
# never sleep, never blank the console, quiet boot
mkdir -p /etc/systemd/logind.conf.d /etc/systemd/sleep.conf.d
printf '[Login]\nHandleLidSwitch=ignore\nIdleAction=ignore\nNAutoVTs=1\n' > /etc/systemd/logind.conf.d/aura.conf
printf '[Sleep]\nAllowSuspend=no\nAllowHibernation=no\n' > /etc/systemd/sleep.conf.d/aura.conf
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
if [[ -f /etc/default/grub ]]; then
  sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=1/' /etc/default/grub
  sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT=.*/GRUB_CMDLINE_LINUX_DEFAULT="quiet splash loglevel=3 rd.udev.log_level=3 vt.global_cursor_default=0 consoleblank=0"/' /etc/default/grub
  grep -q '^GRUB_CMDLINE_LINUX_DEFAULT' /etc/default/grub || echo 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash loglevel=3"' >> /etc/default/grub
  update-grub >/dev/null 2>&1 || true
fi
plymouth-set-default-theme -R spinner >/dev/null 2>&1 || true

log "Audio (PipeWire, HDMI default)"
mkdir -p /etc/wireplumber/wireplumber.conf.d
cp -f os/wireplumber-hdmi.conf /etc/wireplumber/wireplumber.conf.d/50-aura-hdmi.conf

log "systemd units"
cp -f os/systemd/aura.service /etc/systemd/system/aura.service
cp -f os/systemd/aura-kiosk.service /etc/systemd/system/aura-kiosk.service
cp -f os/systemd/aura-update.service /etc/systemd/system/aura-update.service
cp -f os/systemd/aura-update.timer /etc/systemd/system/aura-update.timer
sed -i "s#@APP_DIR@#$APP_DIR#g; s#@DATA_DIR@#$DATA_DIR#g; s#@APP_USER@#$APP_USER#g; s#@KIOSK_USER@#$KIOSK_USER#g" /etc/systemd/system/aura*.service
echo "d /run/aura 0775 $APP_USER $APP_USER -" > /etc/tmpfiles.d/aura.conf
systemd-tmpfiles --create /etc/tmpfiles.d/aura.conf
# the kiosk session runs as user 'tv' on tty1; disable the getty there
systemctl disable getty@tty1.service >/dev/null 2>&1 || true
systemctl daemon-reload
systemctl enable aura.service aura-kiosk.service aura-update.timer >/dev/null
systemctl restart aura.service
systemctl restart aura-kiosk.service || true

log "Done. Open http://$(hostname).local:8080/ (or the IP shown on the TV) from your phone."
log "Reboot recommended: sudo reboot"

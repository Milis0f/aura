#!/usr/bin/env bash
# Self-update: pull the latest code, sync dependencies, re-apply system files, restart services.
set -euo pipefail
APP_DIR=${APP_DIR:-/opt/aura}
cd "$APP_DIR"
# YouTube breaks extractors often: refresh yt-dlp every night, even when Aura itself did not change.
uv pip install -q --python .venv/bin/python -U "yt-dlp[default]" || true
if [[ -d .git ]]; then
  BRANCH=$(git rev-parse --abbrev-ref HEAD)
  OLD=$(git rev-parse HEAD)
  git fetch -q origin "$BRANCH"
  git reset -q --hard "origin/$BRANCH"
  NEW=$(git rev-parse HEAD)
  if [[ "$OLD" == "$NEW" ]]; then
    echo "already up to date ($NEW)"
    exit 0
  fi
  echo "updated $OLD -> $NEW"
fi
uv pip install -q --python .venv/bin/python -e . || .venv/bin/pip install -q -e .
# Boxes installed before the drives library still need its packages and polkit rules.
if ! command -v udisksctl >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1 || ! command -v qbittorrent-nox >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends udisks2 ntfs-3g exfatprogs dosfstools ffmpeg polkitd qbittorrent-nox || true
fi
mkdir -p /etc/polkit-1/rules.d
for rule in os/polkit/*.rules; do
  sed "s/@APP_USER@/aura/g" "$rule" > "/etc/polkit-1/rules.d/$(basename "$rule")"
done
chown -R aura:aura "$APP_DIR"
for u in aura aura-kiosk aura-update aura-qbittorrent; do
  [[ -f "os/systemd/$u.service" ]] && cp -f "os/systemd/$u.service" "/etc/systemd/system/$u.service"
done
sed -i "s#@APP_DIR@#$APP_DIR#g; s#@DATA_DIR@#/var/lib/aura#g; s#@APP_USER@#aura#g; s#@KIOSK_USER@#tv#g" /etc/systemd/system/aura*.service
systemctl daemon-reload
systemctl restart aura-qbittorrent.service 2>/dev/null || true
systemctl restart aura.service
sleep 2
systemctl restart aura-kiosk.service
echo "done"

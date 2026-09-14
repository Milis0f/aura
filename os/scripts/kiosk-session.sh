#!/usr/bin/env bash
# Kiosk session: cage (single-app Wayland compositor) running Google Chrome fullscreen on Aura.
# mpv, when spawned by the backend, opens a second Wayland window that cage stacks on top.
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$XDG_RUNTIME_DIR"
URL="${AURA_URL:-http://127.0.0.1:8080/tv/}"
PROFILE="$HOME/.config/aura-chrome"
mkdir -p "$PROFILE"

# Start the user audio stack (PipeWire) if the user service manager is not running it.
if ! pgrep -u "$(id -u)" -x pipewire >/dev/null 2>&1; then
  (pipewire & sleep 0.5; wireplumber & pipewire-pulse &) >/dev/null 2>&1 || true
fi

CHROME=$(command -v google-chrome-stable || command -v google-chrome || command -v chromium || true)
[[ -n "$CHROME" ]] || { echo "no chrome/chromium found" >&2; exit 1; }

# Clean crash flags so Chrome never shows the "restore pages" bar.
if [[ -f "$PROFILE/Default/Preferences" ]]; then
  sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' "$PROFILE/Default/Preferences" || true
fi

CHROME_FLAGS=(
  --kiosk "$URL"
  --user-data-dir="$PROFILE"
  --ozone-platform=wayland
  --enable-features=UseOzonePlatform,VaapiVideoDecodeLinuxGL,VaapiVideoDecoder,WebRTCPipeWireCapturer
  --enable-accelerated-video-decode
  --ignore-gpu-blocklist
  --no-first-run --no-default-browser-check --noerrdialogs --disable-infobars
  --disable-session-crashed-bubble --disable-translate --disable-features=TranslateUI
  --autoplay-policy=no-user-gesture-required
  --remote-debugging-port=9222 --remote-allow-origins=*
  --password-store=basic
  --disable-pinch --overscroll-history-navigation=0
  --start-fullscreen --window-size=1920,1080 --force-device-scale-factor=1
  --lang=fr-FR
  --audio-output-channels=2
)

exec cage -s -d -- "$CHROME" "${CHROME_FLAGS[@]}"

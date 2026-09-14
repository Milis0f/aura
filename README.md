# Aura

A living-room IPTV appliance for any PC or Intel Mac running Linux. Plug HDMI, boot, configure from your phone
or directly on screen with a mouse and keyboard. Liquid-glass UI, no black, no white.

- **Live TV** from Xtream Codes accounts, M3U playlists (URL or file) and free public bundles (iptv-org by
  country and category: France, sports, news, cinema, series, documentaries, music, kids, 24/7…)
- **EPG** (XMLTV, gzip) with now/next, timeline queries and fuzzy channel matching
- **Sports**: F1 calendar (Jolpica), UFC events (ufc.com), football / basketball / NFL / NHL / rugby fixtures
  (TheSportsDB, no key), boxing from the EPG; each event maps to the channels in *your* sources most likely to
  broadcast it
- **Movies / series**: VOD from Xtream or M3U, rows (continue watching, recent, top rated, per genre), rich detail
  pages (poster, backdrop, synopsis, cast, trailer, seasons/episodes, resume), poster back-fill. Metadata chain:
  TMDB (optional key) → keyless fallbacks (iTunes, Wikipedia, TVMaze). Free legal catalogue of public-domain
  classics from the Internet Archive (~300 films) for boxes without a VOD subscription
- **Web apps** (Netflix, Prime Video, Canal+, Disney+, YouTube, DAZN, F1 TV…) opened in the kiosk browser
  (Google Chrome + Widevine), driven from the phone as a trackpad/keyboard through the Chrome DevTools Protocol
- **Playback built for weak hardware**
  - every source of a channel (same name or `tvg-id` across playlists) is probed in parallel before playback;
    the fastest live one wins, dead links never reach the video element
  - direct CDN fetch when the origin sends CORS; otherwise a local proxy with a hot keep-alive pool, 3 s connect
    timeout, HLS rewriting, Range passthrough and short segment caching. Direct playback that fails on segment
    CORS silently retries through the proxy
  - hls.js and mpegts.js tuned for live (no JS stash buffer, fast retries), automatic failover to the next
    source, connection pre-warm while a channel is focused, single start per play (no double manifest load)
  - raw MPEG-TS goes to **mpv** with VA-API hardware decoding on low-core machines; RTMP/RTSP/UDP/MKV always do.
    Selectable in *Réglages › Lecture*, with a per-channel source tester
  - the probe reads only the first bytes, sniffs the real format (HLS behind an extension-less URL, MPEG-TS,
    MP4) and rejects web pages; YouTube live channels (the "Ⓨ" entries of public playlists) are resolved to
    their HLS manifest with yt-dlp and cached for three hours
- **Catalogue performance**: one SQLite connection per thread and bulk EPG queries (a 400-channel page went from
  2.47 s to 12 ms of database work), concurrent playlist refresh, M3U parsing off the event loop,
  relevance-ranked search, duplicate playlists collapsed
- **Phone remote & admin UI** (PWA): D-pad, trackpad, keyboard, browse & cast, network (Wi-Fi scan/connect,
  static IP, onboarding hotspot), sources, EPG, playback engine, TMDB key, audio output, update, reboot, logs
- **Appliance OS layer** for Debian 13: `cage` Wayland kiosk, autologin, PipeWire HDMI, Wi-Fi and GPU firmware for
  common PCs and Intel Macs, mDNS, quiet boot, nightly self-update, unattended preseed

User-facing install guide (French): [docs/INSTALL-FR.md](docs/INSTALL-FR.md).
Design notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Development (any OS)

```bash
uv venv && uv pip install -e ".[dev]"
uv run aura            # http://127.0.0.1:8080/tv/  (TV UI)  ·  /remote/ (phone UI)  ·  /api/docs
uv run pytest
```

Linux-only parts (nmcli, mpv, Chrome CDP, pactl) degrade to mocks or no-ops elsewhere, so the whole
catalogue/EPG/sports/UI stack runs on Windows or macOS. Data lives in `./data` (or `$AURA_DATA`).

## Layout

```
aura/
  main.py            FastAPI app, static mounts, background scheduler
  config.py          settings from env, free bundles, EPG presets, web apps
  models.py          frozen dataclasses (StreamItem, EpgProgramme, SportsEvent, Source)
  db.py              SQLite (WAL) schema, thread-local connections, helpers
  api/               REST + WebSocket routers (catalog, vod, player/proxy/remote, network/system)
  services/          m3u_parser, xtream, epg, tmdb, metadata (provider chain), archive_films, catalog,
                     probe (source health/latency/CORS), player (mpv IPC), kiosk (CDP), network (nmcli),
                     system, hub, sports/{f1,ufc,teamsports,matcher}
  static/tv/         TV kiosk UI (vanilla JS, spatial navigation, hls.js/mpegts.js)
  static/remote/     phone remote + admin UI (PWA)
os/
  install.sh         one-shot Debian 13 provisioning (idempotent)
  systemd/           aura.service, aura-kiosk.service, nightly update timer
  scripts/           kiosk-session.sh (cage + Chrome), update.sh
  preseed/           unattended Debian install answers
tests/               pytest (parsers, EPG, sports, matcher, VOD, probing, proxy, source racing, API)
```

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `AURA_DATA` | `/var/lib/aura` (Linux) / `./data` | SQLite DB, cache, uploads |
| `AURA_PORT` | `8080` | HTTP port |
| `AURA_MPV_SOCKET` | `/run/aura/mpv.sock` | mpv JSON IPC socket |
| `AURA_CHROME_PORT` | `9222` | Chrome remote debugging port |
| `AURA_HOTSPOT_SSID` / `_PASSWORD` | `Aura-Setup` / `aura123` | onboarding hotspot |
| `AURA_*_REFRESH_HOURS` | sources 12, EPG 6, sports 6 | scheduler cadence |

Runtime settings are stored in the DB and edited from `/remote/`: TMDB key, device name, setup flag,
`live_backend` (`auto` | `browser` | `mpv`) and `fast_start` (`1` probes sources before playing, `0` plays the
first URL as is).

## Legal

Aura plays streams the user provides. It ships no channels, no credentials and no DRM circumvention;
the bundled "free" playlists point to the community-maintained iptv-org repository of publicly available
streams and to public-domain films on the Internet Archive. Netflix & co. run in an unmodified Google Chrome
with the user's own subscription.

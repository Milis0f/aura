# Aura

A home media box for any PC or Intel Mac running Linux. Plug HDMI and boot: the TV shows your films, your series
and live channels. Plug a hard drive: its films and series appear on their own, with posters and resume points.
Everything is driven from a phone, from any computer at home, or on screen with a mouse and keyboard.
It also keeps the NAS side of the machine: files, downloads, accounts. Dark, quiet UI built for weak hardware.

## What it does

- **Films and series on your drives**
  - plug a USB drive: detected at once (udev), mounted (udisks2, NTFS and exFAT included), scanned
  - release names and folder layouts become films (title, year, versions) and series (seasons, episodes), with
    quality and languages (MULTI, VF, VOSTFR)
  - posters and synopses from TMDB (optional key) or keyless fallbacks (iTunes, Wikipedia, TVMaze), durations
    and codecs from ffprobe
  - resume points that survive unplugging, "Reprendre" rows, next episode chained automatically
  - films added by link, extra watched folders, safe eject, rescans when a download finishes
  - on the appliance, mpv plays straight from the disk with hardware decoding, every codec and track; elsewhere
    the TV page or the browser streams the file
- **Live TV** from Xtream Codes accounts, M3U playlists (URL or file) and free public bundles (iptv-org by country
  and category)
- **EPG** (XMLTV, gzip) with now/next, timeline queries and fuzzy channel matching
- **Sports**: F1 calendar (Jolpica), UFC events (ufc.com), football / basketball / NFL / NHL / rugby fixtures
  (TheSportsDB, no key), boxing from the EPG; each event maps to the channels in *your* sources likely to show it
- **IPTV films and series**: VOD from Xtream or M3U, rows, rich detail pages, resume; a free legal catalogue of
  public-domain classics from the Internet Archive
- **Web apps** (Netflix, Prime Video, Canal+, Disney+, YouTube, DAZN, F1 TV…) in the kiosk browser (Google Chrome +
  Widevine), driven from the phone as a trackpad and keyboard through the Chrome DevTools Protocol
- **Live playback built for weak hardware**: every source of a channel probed in parallel, fastest live one wins;
  direct CDN fetch when CORS allows, local keep-alive proxy otherwise; raw MPEG-TS to mpv on low-core machines;
  YouTube live pages resolved with yt-dlp
- **NAS** (merged from NAS Dashboard v2): file manager (browse, search, upload, rename, move, delete, Range
  streaming), downloads through qBittorrent, accounts with Argon2 passwords, TOTP two-factor, sessions, lockout
  and audit trail, `aura-manage` in a terminal, import of existing NAS Dashboard accounts
- **Appliance OS layer** for Debian 13: `cage` Wayland kiosk, autologin, PipeWire HDMI, Wi-Fi and GPU firmware for
  common PCs and Intel Macs, udisks2, mDNS, quiet boot, nightly self-update, unattended preseed

## Three screens, three zones

| Page | For | Account |
|---|---|---|
| `/tv/` | the TV (kiosk Chrome), D-pad navigation | never |
| `/remote/` | phone remote, browsing, setup wizard (PWA) | not at home |
| `/` | web app: library, drives, files, downloads, system, security | always |

Requests are sorted by origin: the machine itself, the home network (private ranges, Tailscale), the Internet
(anything else, or relayed by a reverse proxy). At home the TV, the remote and the library work without an account
so guests can watch; files, downloads and accounts always need one; from the Internet only the sign-in page answers.

User-facing install guide (French): [docs/INSTALL-FR.md](docs/INSTALL-FR.md).
Design notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Development (any OS)

```bash
uv venv && uv pip install -e ".[dev]"
uv run aura            # http://127.0.0.1:8000/  (web app) · /tv/ (TV) · /remote/ (phone) · /api/docs
uv run pytest
```

Linux-only parts (nmcli, udisksctl, lsblk, mpv, Chrome CDP, pactl) degrade to mocks or no-ops elsewhere, so the
catalogue, EPG, sports, library and every UI run on Windows or macOS. Point the library at a folder of videos to try
it without a drive: `AURA_LIBRARY_DIRS=/path/to/videos uv run aura`. Data lives in `./data` (or `$AURA_DATA`).

## Layout

```
aura/
  main.py            FastAPI app, static mounts, background tasks (catalogue, drives, library, downloads, mpv)
  config.py          settings from env (AURA_* and NAS Dashboard names), free bundles, EPG presets, web apps
  cli.py             aura-manage: accounts from a terminal, NAS Dashboard import
  models.py          frozen dataclasses (StreamItem, EpgProgramme, SportsEvent, Source)
  db.py              SQLite (WAL) schema, thread-local connections, helpers
  api/               guard (zones, CSRF, security headers), auth, catalog, vod, library (+ drives), files,
                     torrents, player (+ stream proxy, remote input), system (+ WebSocket)
  services/
    storage          drives: lsblk, udisksctl mount and eject, udev monitor, watched folders
    library_scan     incremental walk of a volume
    mediaparse       file and folder names -> film or episode, year, quality, languages
    library          films, series, versions, resume points, links
    library_jobs     drive events, scans, posters, ffprobe durations, finished downloads
    accounts         Argon2 accounts, TOTP, sessions, lockout, audit, NAS Dashboard import
    security, files, torrents
    m3u_parser, xtream, epg, tmdb, metadata, archive_films, catalog, probe, resolver, player (mpv IPC),
    kiosk (CDP), network (nmcli), system, hub, textutil, sports/{f1,ufc,teamsports,matcher}
  static/tv/         TV kiosk UI (vanilla JS, spatial navigation, hls.js/mpegts.js) + tv-library.js
  static/remote/     phone remote and setup (PWA)
  static/web/        web app (ES modules): library, drives, files, downloads, system, accounts
  static/shared/     design tokens, Geist fonts, SVG icons
os/
  install.sh         one-shot Debian 13 provisioning (idempotent, takes over NAS Dashboard)
  systemd/           aura.service, aura-kiosk.service, nightly update timer
  polkit/            NetworkManager and udisks2 rules for the service user
  scripts/           kiosk-session.sh (cage + Chrome), update.sh
  nginx-aura.conf    optional HTTPS reverse proxy for access from the Internet
  preseed/           unattended Debian install answers
tests/               pytest (parsers, EPG, sports, VOD, probing, proxy, library, drives, accounts, files, API, OS files)
```

## Configuration (environment)

On the appliance these go in `/etc/aura.env`. NAS Dashboard names (`FILE_ROOTS`, `MEDIA_ROOT`, `QB_URL`…) are
accepted as they are.

| Variable | Default | Purpose |
|---|---|---|
| `AURA_DATA` | `/var/lib/aura` (Linux) / `./data` | SQLite DB, cache |
| `AURA_PORT`, then `PORT` | `8000` | HTTP port (8080 belongs to qBittorrent on a NAS) |
| `AURA_HOST` | `0.0.0.0` | bind address |
| `AURA_LIBRARY_DIRS` | | extra library folders, separated by `:` (`;` on Windows) |
| `AURA_FILE_ROOTS` | | file manager roots, `Name:/path,Name:/path` |
| `AURA_MEDIA_ROOT` | | media folder: file manager, library, download destination |
| `AURA_QB_URL` / `_QB_USER` / `_QB_PASS` | `http://127.0.0.1:8080` | qBittorrent Web UI |
| `AURA_JELLYFIN_URL` | | shows a Jellyfin link in the web app |
| `AURA_SECURE_COOKIE` | `auto` | `Secure` session cookie only over HTTPS |
| `AURA_SESSION_TTL` / `_IDLE_TTL` | `43200` / `86400` | session lifetime, idle timeout (seconds) |
| `AURA_MAX_FAILS` / `_LOCKOUT_SECONDS` | `8` / `600` | sign-in lockout |
| `AURA_AUTOMOUNT` | `1` | mount removable drives when they are plugged in |
| `AURA_MANAGE_NETWORK` | `1` | `0` on OpenMediaVault, which owns the network configuration |
| `AURA_MPV_SOCKET` | `/run/aura/mpv.sock` | mpv JSON IPC socket |
| `AURA_CHROME_PORT` | `9222` | Chrome remote debugging port |
| `AURA_HOTSPOT_SSID` / `_PASSWORD` | `Aura-Setup` / `aura123` | onboarding hotspot |
| `AURA_*_REFRESH_HOURS` | sources 12, EPG 6, sports 6 | scheduler cadence |

Runtime settings are stored in the DB and edited from the remote or the web app: TMDB key, device name, setup flag,
`live_backend` (`auto` | `browser` | `mpv`), `fast_start`, `ui_accent`, `autoplay_next`, `automount`.

## Legal

Aura plays streams and files the user provides. It ships no channels, no credentials, no DRM circumvention and no
torrent search; the downloads page drives the user's own qBittorrent. The bundled "free" playlists point to the
community-maintained iptv-org repository of publicly available streams and to public-domain films on the Internet
Archive. Netflix & co. run in an unmodified Google Chrome with the user's own subscription.

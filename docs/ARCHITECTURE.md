# Aura architecture

## Runtime on the appliance

```
tty1 ── aura-kiosk.service (user tv)
         └─ cage (wlroots kiosk compositor)
              ├─ google-chrome --kiosk http://127.0.0.1:8000/tv/  --remote-debugging-port=9222
              └─ mpv (spawned on demand by the backend, stacked on top while it runs)

aura.service (user aura, /etc/aura.env) ── uvicorn ── FastAPI :8000
    ├─ /           web app       (library, drives, files, downloads, system, security; account)
    ├─ /tv/        TV UI         (WebSocket role=tv)
    ├─ /remote/    phone remote  (WebSocket role=remote)
    ├─ /api/…      REST, behind the access gate
    ├─ /api/proxy  stream proxy (only when the origin has no CORS; HLS rewriting, Range passthrough)
    ├─ background  sources / EPG / sports refresh, drive watcher, library scans, posters and durations,
    │              finished-download rescans, mpv watchdog, onboarding hotspot
    └─ SQLite (WAL)  /var/lib/aura/aura.db
```

NetworkManager owns networking and udisks2 owns mounts: the backend calls `nmcli` and `udisksctl` directly, polkit
rules in `os/polkit/` let the service user do it without sudo. Avahi publishes the mDNS name, PipeWire routes audio
to HDMI, a nightly timer runs `update.sh`. qBittorrent, when the machine also serves as a NAS, keeps port 8080;
Aura listens on 8000, the port NAS Dashboard used, so an existing reverse proxy keeps working.

## Access zones and accounts

`services/security.py` sorts every request into one of three zones:

| Zone | Who | Without an account | With an account |
|---|---|---|---|
| local | loopback: the kiosk Chrome, mpv | TV, remote, library, playback, file contents for playback | everything the account allows |
| lan | private ranges, link-local, `100.64.0.0/10` (Tailscale) | TV, remote, library, playback, network, settings | + files, downloads, account settings |
| remote | any other address, and every request carrying `X-Forwarded-For` or `Forwarded` | the web app shell and the sign-in answers | everything the account allows |

A reverse proxy on the same machine relays public traffic from 127.0.0.1, so forwarded headers win over the socket
peer: nginx traffic is never mistaken for the kiosk. uvicorn runs with `proxy_headers=False` for the same reason.

`api/guard.py` holds the `Gate` middleware (refuses anonymous remote traffic before any router runs, closes remote
WebSockets with code 4401, adds CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy` and HSTS over HTTPS) and the
route dependencies: `home_or_account`, `local_or_account`, `need_account` (a CSRF header on every unsafe method),
`need_write`, `need_torrent`.

Accounts (`services/accounts.py`) keep NAS Dashboard v2's model: Argon2id hashes, optional TOTP, server-side sessions
with absolute and idle expiry, lockout after repeated failures, an audit trail. The first account can only be
created from the machine or the home network. `aura-manage` does the same from a terminal and imports a NAS
Dashboard database with the same hashes and TOTP secrets, so passwords and authenticator apps keep working.

## Drives and the library

**Detection.** `services/storage.py` lists block devices with `lsblk --json`, keeps partitions that carry a readable
filesystem and flags system ones (mounted on `/`, `/boot`…, EFI and recovery labels, FAT partitions under 1 GiB);
system partitions never reach the API and cannot be mounted or ejected. Removable data partitions are mounted with
`udisksctl` as soon as they appear; NTFS, exFAT and HFS+ volumes left dirty by another OS fall back to read-only.
`udevadm monitor` wakes the watcher on plug and unplug, a 20 s poll backs it up. Folders the owner configures
(`AURA_FILE_ROOTS`, `AURA_MEDIA_ROOT`, `AURA_LIBRARY_DIRS`, folders watched from the web app) are volumes too,
unless a scanned disk already contains them. OpenMediaVault mounts (`/srv/dev-disk-by-…`) are read, never ejected.

**Scanning.** `library_jobs.LibraryJobs` reacts to the watcher: a new volume is scanned at once and announced over the
WebSocket (`drive_added`, `library_scan`, `library_changed`); at boot only volumes not scanned in the last six hours
are, and everything is rescanned every six hours. `library_scan` walks the volume in a worker thread, skips videos
under 25 MB (samples, trailers) and unfinished downloads, compares size and mtime with the database and hands only
the changes to `library.apply_scan`, which writes them in one transaction. A mount point that suddenly looks empty
(drive pulled between listing and walking) aborts the scan instead of wiping that drive's titles. `mediaparse`
turns release names and folder layouts into a film (title, year) or an episode (show, season, episode, title) with
quality and languages, and puts back the apostrophes release names drop ("d'Amélie", "Ocean's").

**Model.** One row per film, series or link (`media_items`), one per video file (`media_files`: an episode, or one
version of a film), one resume point per file (`media_progress`). Ids derive from content (drive + path, kind +
title + year), so unplugging a drive or rescanning never loses a resume point: the titles of an unplugged drive
leave the lists and come back with their progress. `library.default_file` decides what "Lire" starts: the file in
progress, else the episode after the last one finished, else the first episode or the best version.

**Enrichment.** Artwork next to the video (`poster`, `folder`, `cover`, `affiche`, or `<name>-poster`) is used first
and served from the drive. A metadata loop fills the rest through `services/metadata.py`: TMDB with a key,
otherwise iTunes and Wikipedia for films and TVMaze for series, always card-sized images because a Mac mini decodes
every poster of a row. Then `ffprobe` reads durations, codecs and heights, four files at a time.

**Playback.** `POST /api/library/play` resolves the file. On the appliance mpv plays it straight from the disk
(hardware decoding, every container and codec, audio and subtitle tracks; `mpv-input.conf` in the data directory
maps a remote-sized set of keys) while the TV page shows a "Lecture en cours" card and forwards its keys to mpv. Without mpv the TV page
plays `/api/library/stream/{id}` with Range requests. The web app can also play in the browser it runs in. Progress
comes back from mpv polling, the TV WebSocket or the web app and is saved every few seconds; a finished episode
chains the next one unless `autoplay_next` is off. When qBittorrent finishes a download, the volume it landed on is
rescanned so the film shows up without touching anything.

## Control flows

**Phone → TV.** The phone posts to `/api/remote/*`. If the kiosk browser is on Aura, the key goes over the TV
WebSocket and the TV page's spatial navigation handles it. If the kiosk has been navigated to a web app (Netflix…),
or mpv is running, the backend injects input through CDP (`Input.dispatchKeyEvent` / `dispatchMouseEvent`) or the
mpv IPC socket. The same split applies to text entry and the trackpad. `POST /api/remote/navigate/{view}` opens a TV
screen (`home`, `library`, `live`, `sports`…), `POST /api/apps/{key}` navigates Chrome via `Page.navigate`,
`POST /api/apps-home` brings it back to `/tv/`.

**Live playback.** `POST /api/play` runs four steps before anything reaches the screen:

1. Candidates are the requested stream plus the same channel in other playlists (normalised name or `tvg-id`),
   capped at five.
2. `services/probe.py` probes them in parallel within a 3.5 s budget. It streams the response and stops after the
   first bytes, because a live MPEG-TS server that ignores `Range` never ends its reply. The bytes decide the format
   (`#EXTM3U` is HLS even without an extension, `0x47` is MPEG-TS, `ftyp` is MP4) and reject web pages. Verdicts are
   cached (90 s alive, 5 min dead). YouTube pages are skipped here and resolved with yt-dlp (`services/resolver.py`)
   only when no direct stream answers.
3. The fastest live candidate wins. If its origin sends `Access-Control-Allow-Origin`, the TV page fetches from the
   CDN directly; otherwise `play_url` points at the local proxy.
4. The engine is chosen by `player.decide_backend`: `.m3u8`/`.mp4` stay in the page; MKV/AVI/DASH/RTMP/RTSP/UDP go
   to mpv; raw MPEG-TS goes to mpv when the machine has four cores or fewer (mpegts.js demuxes in JavaScript, which an
   Ivy Bridge Mac mini cannot sustain at 1080p), unless the `live_backend` setting forces one engine.

The response and the WebSocket broadcast carry the same token, so the TV page starts the stream once even though it
receives the order twice. In the page, hls.js and mpegts.js run with live-oriented settings (no stash buffer, 400 ms
retries, 6 s manifest timeout). A network failure on a direct stream is replayed through the proxy first, then the
next alternative takes over with a "source 2…" toast. Focusing a channel for 350 ms pre-opens its connection
(`POST /api/player/warm`). Progress is reported back over the WebSocket so the phone shows position and duration
and VOD resume works. `GET /api/player/probe` exposes the per-source verdicts to the phone's source tester.

**Catalogue.** `catalog.refresh_source` fetches and parses (M3U or Xtream API), then replaces the source's rows in
one transaction. Parsing and the bulk insert run in worker threads, and all sources refresh concurrently (four at a
time). A playlist URL is registered once; duplicates left by older versions are collapsed at startup. Channel kind
(live/vod/series) is inferred from the URL shape first (`/movie/`, `/series/`, file extension), then from group
hints, never from group hints alone for `.m3u8` streams (public playlists tag live movie channels as "movies").
Search ranks exact names, then prefixes, then substrings, shorter names first.

**EPG.** XMLTV is stream-parsed with `iterparse` (memory-flat for 50 MB guides), rows older than 2 days are dropped.
Channels map to EPG ids by exact `tvg-id`, then by a loose normalised display name. Attaching now/next to a page of
channels is three queries in total, whatever the page size. Sports matching searches programme titles in the event
window for the event's keywords.

**Sports.** F1 comes from Jolpica (`api.jolpi.ca/ergast/f1/current.json`), expanded into sessions with local
durations. UFC is scraped from ufc.com event cards (headline, main-card timestamp, location) with the event number
recovered from the slug. Football, basketball, NFL, NHL and rugby come from TheSportsDB's free tier, which caps each
response at a few rows: coverage is the next fixture of ten competitions plus the next three days per sport, merged
on event id. Boxing and "other" come from EPG title keywords. Events are cached in the DB so the UI works offline
and while upstreams fail.

**IPTV films and series.** `/api/vod/home` builds rows from the catalogue (continue watching from history, recent by
`added`, top rated, one row per group) plus an hourly-rotating hero. `/api/vod/{id}` merges three layers: the
catalogue row (name, logo, group), the provider's own info (Xtream `get_vod_info` / `get_series_info`: plot,
backdrop, cast, trailer, episodes) and external metadata through `services/metadata.py`. Everything is cached in
SQLite. `archive://<id>` items (Internet Archive public-domain films) are resolved to the best MP4 at play time.

## Performance notes

Measured on the development machine against a 7 400-channel catalogue.

| Path | Before | After | Change |
|---|---|---|---|
| Database work for a 400-channel page with now/next | 2 470 ms | 12 ms | thread-local connection instead of one per query |
| Parsing the 11 000-entry public index | 348 ms on the event loop | same cost, in a worker thread | video proxy keeps flowing during refresh |
| Refreshing six playlists | sum of all | slowest one | concurrent refresh |

The pre-fix cost mattered beyond the page itself: the database work held the interpreter while the proxy was
feeding video segments from the same process, which is what turned a slow list into a stuttering stream. Library
scans, `ffprobe` and M3U parsing run off the event loop for the same reason.

Two HTTP clients exist on purpose. The general client (catalogues, EPG, metadata) is patient. The stream client fails
a connection in 3 s, keeps 32 connections alive for 90 s and allows 64 in parallel, because re-handshaking TLS for
every six-second segment is what makes cheap boxes stutter.

On the TV page, `backdrop-filter` is reserved for a few fixed layers (the HD 4000 cannot blur every card), entrance
animations move with `transform` only, and UI files are served with `Cache-Control: no-cache` so the kiosk picks up
a nightly update.

## Security posture

- At home the TV, the remote and the library work without an account: guests must be able to watch. Files,
  downloads and account settings always need an account, with a CSRF token on every change.
- From the Internet nothing answers without an account except the sign-in. Put Aura behind HTTPS
  (`os/nginx-aura.conf`, or a mesh VPN such as Tailscale) rather than forwarding port 8000; nginx rate-limits
  sign-ins and the app locks accounts after repeated failures.
- File manager paths are resolved against the configured roots and refused when they escape them. System
  partitions cannot be mounted or ejected, system folders cannot be added to the library.
- The stream proxy only forwards `http(s)` URLs and never returns upstream hop-by-hop headers; it exists because most
  IPTV origins do not send CORS headers. When they do, the browser fetches directly.
- Probing sends a single small request per candidate and reads at most the first kilobyte.
- Secrets (Xtream password, TMDB key, qBittorrent password) live in SQLite or `/etc/aura.env` (mode 600), are never
  logged and are masked in API responses. Session cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` over HTTPS.
- Chrome's debugging port binds to localhost only.

## Where to extend

- `config.WEB_APPS`: add a web app tile.
- `services/sports/`: add a sport; produce `SportsEvent`s with keywords, the matcher does the rest.
- `services/mediaparse.py`: teach the library a naming scheme (add a case to `tests/test_mediaparse.py` first).
- `services/storage.py::READABLE_FS`: accept another filesystem.
- `services/player.py::decide_backend`: force mpv for a URL pattern; `MPV_INPUT` changes mpv's keys.
- `services/probe.py`: change what counts as a live source (e.g. require a first segment, not just the playlist).
- `static/tv/tv.js::views`: add a TV screen; anything with class `f` is focusable by D-pad.
- CEC: a `cec-client` listener translating to `POST /api/remote/key` is all that is needed.

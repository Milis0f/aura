# Aura architecture

## Runtime on the appliance

```
tty1 ── aura-kiosk.service (user tv)
         └─ cage (wlroots kiosk compositor)
              ├─ google-chrome --kiosk http://127.0.0.1:8080/tv/  --remote-debugging-port=9222
              └─ mpv (spawned on demand by the backend, stacked on top while it runs)

aura.service (user aura) ── uvicorn ── FastAPI
    ├─ /tv/      TV UI          (WebSocket role=tv)
    ├─ /remote/  phone UI       (WebSocket role=remote)
    ├─ /api/…    REST
    ├─ /api/proxy  stream proxy (only when the origin has no CORS; HLS rewriting, Range passthrough)
    ├─ scheduler  sources / EPG / sports refresh, mpv watchdog, onboarding hotspot
    └─ SQLite (WAL)  /var/lib/aura/aura.db
```

NetworkManager owns networking (nmcli from the backend, polkit rule for the service user),
avahi publishes the mDNS name, PipeWire routes audio to HDMI, a nightly timer runs `update.sh`.

## Control flows

**Phone → TV.** The phone posts to `/api/remote/*`. If the kiosk browser is on Aura, the key
goes over the TV WebSocket and the TV page's spatial-navigation handles it. If the kiosk has been
navigated to a web app (Netflix…), or mpv is running, the backend injects input through CDP
(`Input.dispatchKeyEvent` / `dispatchMouseEvent`) or the mpv IPC socket. The same split applies to
text entry and the trackpad. `POST /api/apps/{key}` navigates Chrome via `Page.navigate`;
`POST /api/apps-home` brings it back to `/tv/`.

**Playback.** `POST /api/play` runs four steps before anything reaches the screen:

1. Candidates are the requested stream plus the same channel in other playlists (normalised name or
   `tvg-id`), capped at five.
2. `services/probe.py` probes them in parallel within a 3.5 s budget. It streams the response and stops after
   the first bytes, because a live MPEG-TS server that ignores `Range` never ends its reply. The bytes decide
   the format (`#EXTM3U` is HLS even without an extension, `0x47` is MPEG-TS, `ftyp` is MP4) and reject web
   pages. Verdicts are cached (90 s alive, 5 min dead). YouTube pages are skipped here and resolved with yt-dlp
   (`services/resolver.py`) only when no direct stream answers.
3. The fastest live candidate wins. If its origin sends `Access-Control-Allow-Origin`, the TV page fetches
   from the CDN directly; otherwise `play_url` points at the local proxy.
4. The engine is chosen by `player.decide_backend`: `.m3u8`/`.mp4` stay in the page; MKV/AVI/DASH/RTMP/RTSP/UDP go
   to mpv; raw MPEG-TS goes to mpv when the machine has four cores or fewer (mpegts.js demuxes in
   JavaScript, which an Ivy Bridge Mac mini cannot sustain at 1080p), unless the `live_backend` setting
   forces one engine.

The response and the WebSocket broadcast carry the same token, so the TV page starts the stream once even
though it receives the order twice. In the page, hls.js and mpegts.js run with live-oriented settings (no
stash buffer, 400 ms retries, 6 s manifest timeout). A network failure on a direct stream is replayed through
the proxy first, then the next alternative takes over with a "source 2…" toast. Focusing a channel for 350 ms
pre-opens its connection (`POST /api/player/warm`). Progress is reported back over the WebSocket so the
phone shows position/duration and VOD resume works. `GET /api/player/probe` exposes the per-source verdicts
to the phone's source tester.

**Catalogue.** `catalog.refresh_source` fetches and parses (M3U or Xtream API), then replaces the
source's rows in one transaction. Parsing and the bulk insert run in worker threads, and all sources refresh
concurrently (four at a time). A playlist URL is registered once; duplicates left by older versions are
collapsed at startup. Channel kind (live/vod/series) is inferred from the URL shape first (`/movie/`,
`/series/`, file extension), then from group hints, never from group hints alone for `.m3u8` streams (public
playlists tag live movie channels as "movies"). Search ranks exact names, then prefixes, then substrings,
shorter names first.

**EPG.** XMLTV is stream-parsed with `iterparse` (memory-flat for 50 MB guides), rows older than
2 days are dropped. Channels map to EPG ids by exact `tvg-id`, then by a loose normalised display
name. Attaching now/next to a page of channels is three queries in total, whatever the page size.
Sports matching searches programme titles in the event window for the event's keywords.

**Sports.** F1 comes from Jolpica (`/ergast/f1/current.json`), expanded into sessions with local
durations. UFC is scraped from ufc.com event cards (headline, main-card timestamp, location) with
the event number recovered from the slug. Football, basketball, NFL, NHL and rugby come from TheSportsDB's
free tier, which caps each response at a few rows: coverage is the next fixture of ten competitions plus the
next three days per sport, merged on event id. Boxing and "other" come from EPG title keywords. Events are
cached in the DB so the UI works offline and while upstreams fail.

**Movies / series.** `/api/vod/home` builds rows from the catalogue (continue watching from history,
recent by `added`, top rated, one row per group) plus an hourly-rotating hero. `/api/vod/{id}` merges three
layers: the catalogue row (name, logo, group), the provider's own info (Xtream `get_vod_info` /
`get_series_info`: plot, backdrop, cast, trailer, episodes) and external metadata through
`services/metadata.py` — TMDB when a key is set, otherwise keyless fallbacks (iTunes for movies, Wikipedia
REST summaries, TVMaze for series). Everything is cached in SQLite. `archive://<id>` items (Internet Archive
public-domain films) are resolved to the best MP4 at play time.

## Performance notes

Measured on the development machine against a 7 400-channel catalogue.

| Path | Before | After | Change |
|---|---|---|---|
| Database work for a 400-channel page with now/next | 2 470 ms | 12 ms | thread-local connection instead of one per query |
| Parsing the 11 000-entry public index | 348 ms on the event loop | same cost, in a worker thread | video proxy keeps flowing during refresh |
| Refreshing six playlists | sum of all | slowest one | concurrent refresh |

The pre-fix cost mattered beyond the page itself: the database work held the interpreter while the proxy
was feeding video segments from the same process, which is what turned a slow list into a stuttering stream.

Two HTTP clients exist on purpose. The general client (catalogues, EPG, metadata) is patient. The stream
client fails a connection in 3 s, keeps 32 connections alive for 90 s and allows 64 in parallel, because
re-handshaking TLS for every six-second segment is what makes cheap boxes stutter.

## Security posture

- The API has no authentication: it is an appliance on a home LAN. Do not expose port 8080 to the
  Internet. (A LAN-only bind plus optional PIN is the natural next step.)
- The proxy only forwards `http(s)` URLs and never returns upstream hop-by-hop headers; it exists
  because most IPTV origins do not send CORS headers. When they do, the browser fetches directly.
- Probing sends a single small request per candidate and never follows content beyond 1 KB.
- Secrets (Xtream password, TMDB key) are stored in SQLite, never logged, masked in API responses.
- Chrome's debugging port binds to localhost only.

## Where to extend

- `config.WEB_APPS` — add a web app tile.
- `services/sports/` — add a sport: produce `SportsEvent`s with keywords; the matcher does the rest.
- `services/player.py::decide_backend` — force mpv for a URL pattern.
- `services/probe.py` — change what counts as a live source (e.g. require a first segment, not just the playlist).
- `static/tv/tv.js::views` — add a TV screen; anything with class `f` is focusable by D-pad.
- CEC: a `cec-client` listener translating to `POST /api/remote/key` is all that is needed.

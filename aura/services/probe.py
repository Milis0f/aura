"""Stream health probing: is it alive, how fast, and can the browser read it directly?

Why this exists
---------------
An IPTV playlist is mostly dead links. Discovering that by handing a URL to the video element and
waiting for its 30 s timeout is what makes a player feel broken. Probing costs ~200 ms and lets us
pick the fastest working source before playback starts.

It also answers a second question that halves latency: does the origin send CORS headers? If it
does, the browser can fetch segments straight from the CDN and the local proxy leaves the hot path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx

from .. import db
from ..config import SETTINGS

log = logging.getLogger(__name__)

PROBE_TTL = 90          # seconds; live streams die often, don't trust an old verdict
PROBE_TTL_DEAD = 300    # remember failures a bit longer
DEFAULT_TIMEOUT = 3.0


@dataclass(frozen=True)
class ProbeResult:
    url: str
    ok: bool
    latency_ms: int
    status: int = 0
    cors: bool = False
    content_type: str = ""
    error: str = ""
    format: str = ""  # hls | mpegts | mp4 | '' — sniffed from the first bytes, not trusted from the URL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_playlist(url: str, content_type: str = "") -> bool:
    path = urlparse(url).path.lower()
    return path.endswith((".m3u8", ".m3u")) or "mpegurl" in content_type


def _cors_ok(headers: httpx.Headers) -> bool:
    origin = headers.get("access-control-allow-origin", "")
    return origin == "*" or origin.startswith("http")


def _cache_key(url: str) -> str:
    return f"probe:{url}"


def cached(url: str) -> ProbeResult | None:
    raw = db.cache_get(_cache_key(url), PROBE_TTL_DEAD)
    if not raw:
        return None
    res = ProbeResult(**raw)
    age_ok = db.cache_get(_cache_key(url), PROBE_TTL) is not None
    if res.ok and not age_ok:
        return None  # a stale "alive" verdict is worthless for live TV
    return res


async def probe_url(http: httpx.AsyncClient, url: str, timeout: float = DEFAULT_TIMEOUT, use_cache: bool = True) -> ProbeResult:
    """One cheap request: the playlist itself, or the first bytes of a segment/file."""
    if use_cache:
        hit = cached(url)
        if hit is not None:
            return hit
    headers = {"User-Agent": SETTINGS.user_agent, "Origin": "http://localhost"}
    started = time.perf_counter()
    result: ProbeResult
    try:
        playlist_url = _is_playlist(url)
        request_headers = headers if playlist_url else {**headers, "Range": "bytes=0-1023"}
        # Stream and stop after the first bytes: a live MPEG-TS server that ignores Range never ends
        # its response, and reading it whole would burn the entire race budget.
        async with http.stream("GET", url, headers=request_headers, timeout=timeout, follow_redirects=True) as r:
            head = b""
            limit = 4096 if playlist_url else 512
            async for part in r.aiter_bytes():
                head += part
                if len(head) >= limit:
                    break
            status, response_headers = r.status_code, r.headers
        ctype = response_headers.get("content-type", "").lower()
        start = head.lstrip()[:16]
        fmt = ""
        if start.startswith(b"#EXTM3U"):
            fmt = "hls"
        elif head[:1] == bytes([0x47]):
            fmt = "mpegts"
        elif head[4:8] == b"ftyp" or "mp4" in ctype:
            fmt = "mp4"
        # A web page (YouTube channel page, provider login, captive portal) answers 200 but is no stream.
        is_page = ctype.startswith("text/html") or start.lower().startswith((b"<!doctype", b"<html"))
        if playlist_url:
            ok = status < 400 and fmt == "hls"
        else:
            ok = status < 400 and bool(head) and not is_page
        result = ProbeResult(
            url=url,
            ok=ok,
            latency_ms=int((time.perf_counter() - started) * 1000),
            status=status,
            cors=_cors_ok(response_headers),
            content_type=ctype.split(";")[0].strip(),
            error="" if ok else ("page web" if is_page else f"HTTP {status}"),
            format=fmt if ok else "",
        )
    except (httpx.HTTPError, ValueError, UnicodeDecodeError) as exc:
        result = ProbeResult(
            url=url,
            ok=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error=type(exc).__name__,
        )
    db.cache_set(_cache_key(url), result.to_dict())
    return result


async def race(
    http: httpx.AsyncClient,
    urls: Iterable[str],
    timeout: float = DEFAULT_TIMEOUT,
    budget: float = 4.0,
    use_cache: bool = True,
) -> list[ProbeResult]:
    """Probe every candidate in parallel; return the working ones fastest-first.

    `budget` caps the whole operation so a slow candidate never delays playback.
    """
    targets = [u for u in dict.fromkeys(urls) if u]
    if not targets:
        return []
    tasks = [asyncio.create_task(probe_url(http, u, timeout, use_cache)) for u in targets]
    done, pending = await asyncio.wait(tasks, timeout=budget)
    for t in pending:
        t.cancel()
    results = [t.result() for t in done if not t.cancelled() and not t.exception()]
    results.sort(key=lambda r: (not r.ok, r.latency_ms))
    return results


async def first_alive(
    http: httpx.AsyncClient, urls: Iterable[str], timeout: float = DEFAULT_TIMEOUT, budget: float = 4.0
) -> ProbeResult | None:
    """Fastest working candidate, or None when every one of them is dead."""
    results = await race(http, urls, timeout, budget)
    return next((r for r in results if r.ok), None)


async def warm(http: httpx.AsyncClient, url: str) -> bool:
    """Open the connection (DNS + TCP + TLS) ahead of playback so the first segment is instant."""
    try:
        async with http.stream(
            "GET",
            url,
            headers={"User-Agent": SETTINGS.user_agent, "Range": "bytes=0-1"},
            timeout=2.0,
            follow_redirects=True,
        ) as r:
            async for _ in r.aiter_raw():
                break  # one chunk is enough: the connection is open
        return True
    except httpx.HTTPError:
        return False

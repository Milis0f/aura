"""Turn web page URLs into playable manifests.

Public playlists list many free live channels as their YouTube page ("Ⓨ" entries in iptv-org). A video
element cannot play a page, so the page is resolved with yt-dlp into the HLS master manifest of the current
live video. Manifests stay valid for about six hours; they are cached for three.

yt-dlp needs its EJS component and a JavaScript runtime for YouTube; Node is used when present.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import Any

from .. import db

log = logging.getLogger(__name__)

CACHE_TTL = 3 * 3600
EXTRACT_TIMEOUT = 25.0
_YOUTUBE = re.compile(r"^https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/", re.IGNORECASE)


def needs_resolve(url: str) -> bool:
    """True for URLs that point at a web page rather than a stream."""
    return bool(_YOUTUBE.match(url or ""))


def _options() -> dict[str, Any]:
    opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}
    if shutil.which("node"):
        opts["js_runtimes"] = {"node": {}}
    return opts


def _hls_manifest(info: dict[str, Any]) -> str:
    formats = [f for f in info.get("formats") or [] if str(f.get("protocol", "")).startswith("m3u8")]
    master = next((f["manifest_url"] for f in formats if f.get("manifest_url")), "")
    if master:
        return str(master)
    variants = [f for f in formats if f.get("url")]
    if variants:
        return str(max(variants, key=lambda f: f.get("height") or 0)["url"])
    raise ValueError("aucun flux HLS : la chaîne n'est peut-être pas en direct")


def _extract_manifest(url: str) -> str:
    """Blocking yt-dlp call. Channel '/live' pages redirect to the current live video first."""
    try:
        import yt_dlp
    except ImportError as exc:  # optional at runtime, installed by default
        raise RuntimeError("yt-dlp n'est pas installé") from exc
    with yt_dlp.YoutubeDL(_options()) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
        for _ in range(3):
            if info.get("_type") in ("url", "url_transparent") and info.get("url"):
                info = ydl.extract_info(info["url"], download=False, process=False)
            else:
                break
    return _hls_manifest(info)


async def resolve(url: str) -> str:
    """Page URL -> HLS manifest URL, cached. Raises when the page has no live stream."""
    key = f"resolve:{url}"
    cached = db.cache_get(key, CACHE_TTL)
    if cached:
        return str(cached)
    manifest = await asyncio.wait_for(asyncio.to_thread(_extract_manifest, url), EXTRACT_TIMEOUT)
    db.cache_set(key, manifest)
    log.info("resolved %s", url)
    return manifest

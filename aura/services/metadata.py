"""Metadata provider chain for movies and series.

Order: TMDB (best, needs a free key) -> keyless fallbacks:
  movies : iTunes Search (poster, synopsis, genre) -> Wikipedia REST summary (fr, en)
  series : TVMaze (poster, synopsis, genres, rating)
Every lookup is cached in the DB (30 days). Returned dict is provider-agnostic:
  {title, year, poster, backdrop, overview, rating, genres, runtime, trailer, source}
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from .. import db
from . import tmdb

log = logging.getLogger(__name__)

CACHE_TTL = 30 * 86400
_TAGS = re.compile(r"<[^>]+>")
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 Aura/1.0"}


def _clean(s: str | None) -> str:
    return _TAGS.sub("", s or "").strip()


async def _json(http: httpx.AsyncClient, url: str, **params: Any) -> Any:
    try:
        r = await http.get(url, params=params or None, headers=_UA, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


async def itunes_movie(http: httpx.AsyncClient, title: str, year: str = "") -> dict[str, Any]:
    for country in ("fr", "us"):
        data = await _json(http, "https://itunes.apple.com/search", term=title, country=country, media="movie", entity="movie", limit=5)
        for r in (data or {}).get("results", []) if data else []:
            ry = str(r.get("releaseDate", ""))[:4]
            if year and ry and abs(int(ry) - int(year)) > 1:
                continue
            art = str(r.get("artworkUrl100", "")).replace("100x100bb", "600x900bb")
            return {
                "title": r.get("trackName", title),
                "year": ry,
                "poster": art,
                "backdrop": "",
                "overview": r.get("longDescription") or r.get("shortDescription") or "",
                "rating": None,
                "genres": [r["primaryGenreName"]] if r.get("primaryGenreName") else [],
                "runtime": int(r["trackTimeMillis"] / 60000) if r.get("trackTimeMillis") else 0,
                "trailer": r.get("previewUrl", ""),
                "source": "itunes",
            }
    return {}


async def tvmaze_show(http: httpx.AsyncClient, title: str) -> dict[str, Any]:
    r = await _json(http, "https://api.tvmaze.com/singlesearch/shows", q=title)
    if not r or not isinstance(r, dict):
        return {}
    img = r.get("image") or {}
    return {
        "title": r.get("name", title),
        "year": str(r.get("premiered") or "")[:4],
        # "original" can be a 4000 px scan: decoding a row of those stalls a Mac mini's GPU for a 200 px card
        "poster": img.get("medium") or img.get("original") or "",
        "backdrop": "",
        "overview": _clean(r.get("summary")),
        "rating": (r.get("rating") or {}).get("average"),
        "genres": r.get("genres") or [],
        "runtime": r.get("averageRuntime") or r.get("runtime") or 0,
        "trailer": "",
        "source": "tvmaze",
    }


_WIKI_THUMB_WIDTH = re.compile(r"/\d+px-")


def _wiki_image(summary: dict[str, Any]) -> str:
    """A poster-sized image: the thumbnail rescaled to 500 px wide, the original only when there is no thumbnail."""
    thumb = (summary.get("thumbnail") or {}).get("source", "")
    if thumb:
        return _WIKI_THUMB_WIDTH.sub("/500px-", thumb, count=1)
    return (summary.get("originalimage") or {}).get("source", "")


async def wikipedia_summary(http: httpx.AsyncClient, title: str, year: str = "") -> dict[str, Any]:
    for lang in ("fr", "en"):
        for t in ([f"{title} (film, {year})", f"{title} (film)", title] if lang == "fr" else [f"{title} ({year} film)", f"{title} (film)", title]):
            if not year and "year" in t:
                continue
            r = await _json(http, f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(t)}")
            if not r or r.get("type") == "disambiguation" or not r.get("extract"):
                continue
            desc = (r.get("description") or "").lower()
            if "film" not in desc and "movie" not in desc and "série" not in desc and "series" not in desc:
                continue
            return {
                "title": r.get("title", title).split(" (")[0],
                "year": year,
                "poster": _wiki_image(r),
                "backdrop": "",
                "overview": r.get("extract", ""),
                "rating": None,
                "genres": [],
                "runtime": 0,
                "trailer": "",
                "source": f"wikipedia-{lang}",
            }
    return {}


async def lookup(http: httpx.AsyncClient, name: str, media: str = "movie", tmdb_id: Any = None) -> dict[str, Any]:
    """Best available metadata for a catalogue title. Never raises, may return {}."""
    title, year = tmdb.clean_title(name)
    key = f"meta:{media}:{title.lower()}:{year}:{tmdb_id or ''}"
    cached = db.cache_get(key, CACHE_TTL)
    if cached is not None:
        return cached
    result: dict[str, Any] = {}
    if tmdb.api_key():
        t = await tmdb.enrich(http, name, media, tmdb_id)
        if t.get("title"):
            result = {**t, "runtime": 0, "trailer": "", "source": "tmdb"}
    if not result and title:
        if media == "tv":
            result = await tvmaze_show(http, title)
        else:
            result = await itunes_movie(http, title, year) or await wikipedia_summary(http, title, year)
    db.cache_set(key, result)
    return result


async def details(http: httpx.AsyncClient, name: str, media: str, tmdb_id: Any = None) -> dict[str, Any]:
    """Richer details when TMDB is available (trailer, cast, runtime), else same as lookup."""
    base = await lookup(http, name, media, tmdb_id)
    if base.get("source") == "tmdb" and base.get("tmdb_id"):
        full = await tmdb.details(http, media, int(base["tmdb_id"]))
        if full:
            return {**base, **{k: v for k, v in full.items() if v}}
    return base

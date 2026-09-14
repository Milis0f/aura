"""TMDB enrichment (posters, year, overview, trailers). Cached in the generic cache table.

API key is stored in settings under 'tmdb_api_key'. Without a key every call returns {}.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from .. import db

log = logging.getLogger(__name__)

BASE = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"
CACHE_TTL = 30 * 86400

_YEAR = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
_EXT = re.compile(r"\.(mkv|mp4|avi|mov|m4v|webm|ts|wmv|flv)$", re.I)
_DOMAIN = re.compile(r"\b[\w-]+\.(com|net|org|tv|cc|me|io|to|xyz)\b", re.I)
_JUNK = re.compile(
    r"\b(fr|vf|vff|vfq|vo|vost|vostfr|multi|truefrench|french|english|"
    r"2160p|1080p|1080i|720p|480p|4k|uhd|hd|sd|hdr|hdr10|dv|sdr|"
    r"bluray|blu-ray|brrip|br|bdrip|webrip|web-dl|webdl|web|hdrip|dvdrip|dvd|hdtv|cam|ts|"
    r"x264|x265|h264|h265|hevc|avc|xvid|divx|aac|ac3|dts|dd5\.?1|5\.1|10bit|remux|proper|repack|extended|unrated|"
    r"director'?s cut|final cut|imax)\b",
    re.I,
)


_PAREN_YEAR = re.compile(r"\(\s*((?:19|20)\d{2})\s*\)")


def clean_title(name: str) -> tuple[str, str]:
    """'FR - Interstellar (2014) 1080p' -> ('Interstellar', '2014').
    'Double.Indemnity.1944.720p.BrRip.x265.HEVCBay.com.mkv' -> ('Double Indemnity', '1944').
    'Blade Runner 2049 (2017)' -> ('Blade Runner 2049', '2017')."""
    t = _EXT.sub("", (name or "").strip())
    release_style = t.count(".") >= 2 or "_" in t
    t = _DOMAIN.sub(" ", t)
    if release_style:
        t = t.replace(".", " ").replace("_", " ")
    t = re.sub(r"^\s*[A-Z]{2,3}\s*[-|:]\s*", "", t)  # leading language tag 'FR - '
    year = ""
    m = _PAREN_YEAR.search(t)
    if m:
        year = m.group(1)
        t = t[: m.start()] + " " + t[m.end():]
    else:
        years = list(_YEAR.finditer(t))
        if years:
            last = years[-1]
            year = last.group(0)
            t = t[: last.start()] + " " + t[last.end():]
    t = _JUNK.sub(" ", t)
    if release_style:
        t = re.sub(r"\s*-\s*[A-Za-z0-9]+\s*$", "", t)  # trailing release group '-GROUP'
    t = re.sub(r"[\[\]{}()|]+", " ", t)
    t = re.sub(r"\s*[-:]\s*$", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -:.")
    return t, year


def api_key() -> str:
    return db.get_setting("tmdb_api_key", "").strip()


async def _get(http: httpx.AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    key = api_key()
    if not key:
        return {}
    cache_key = f"tmdb:{path}:{sorted(params.items())}"
    cached = db.cache_get(cache_key, CACHE_TTL)
    if cached is not None:
        return cached
    try:
        r = await http.get(
            f"{BASE}{path}", params={"api_key": key, "language": "fr-FR", **params}, timeout=15
        )
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("TMDB %s failed: %s", path, exc)
        return {}
    db.cache_set(cache_key, data)
    return data


def _poster(p: str | None) -> str:
    return f"{IMG}{p}" if p else ""


def _summ(r: dict[str, Any], media: str) -> dict[str, Any]:
    title = r.get("title") or r.get("name") or ""
    date = r.get("release_date") or r.get("first_air_date") or ""
    return {
        "tmdb_id": r.get("id"),
        "media": media,
        "title": title,
        "year": date[:4],
        "poster": _poster(r.get("poster_path")),
        "backdrop": (f"https://image.tmdb.org/t/p/w1280{r['backdrop_path']}" if r.get("backdrop_path") else ""),
        "overview": r.get("overview") or "",
        "rating": r.get("vote_average"),
        "genres": [g.get("name") for g in r.get("genres", [])] if r.get("genres") else [],
    }


async def search(http: httpx.AsyncClient, query: str, media: str = "multi") -> list[dict[str, Any]]:
    path = {"movie": "/search/movie", "tv": "/search/tv"}.get(media, "/search/multi")
    data = await _get(http, path, query=query)
    out = []
    for r in data.get("results", []):
        mt = r.get("media_type") or media
        if mt not in ("movie", "tv"):
            continue
        out.append(_summ(r, mt))
    return out


async def enrich(http: httpx.AsyncClient, name: str, media: str = "movie", tmdb_id: Any = None) -> dict[str, Any]:
    """Best-effort metadata for one catalogue entry."""
    if tmdb_id and str(tmdb_id).isdigit():
        data = await _get(http, f"/{media}/{tmdb_id}")
        if data.get("id"):
            return _summ(data, media)
    title, year = clean_title(name)
    if not title:
        return {}
    params: dict[str, Any] = {"query": title}
    if year:
        params["year" if media == "movie" else "first_air_date_year"] = year
    data = await _get(http, f"/search/{media}", **params)
    results = data.get("results") or []
    if not results and year:
        data = await _get(http, f"/search/{media}", query=title)
        results = data.get("results") or []
    return _summ(results[0], media) if results else {}


async def details(http: httpx.AsyncClient, media: str, tmdb_id: int) -> dict[str, Any]:
    data = await _get(http, f"/{media}/{tmdb_id}", append_to_response="videos,credits")
    if not data.get("id"):
        return {}
    d = _summ(data, media)
    trailer = next(
        (v for v in data.get("videos", {}).get("results", []) if v.get("site") == "YouTube" and v.get("type") == "Trailer"),
        None,
    )
    d["trailer"] = f"https://www.youtube.com/watch?v={trailer['key']}" if trailer else ""
    d["cast"] = [c.get("name") for c in data.get("credits", {}).get("cast", [])[:8]]
    d["runtime"] = data.get("runtime") or (data.get("episode_run_time") or [0])[0]
    return d


async def trending(http: httpx.AsyncClient, media: str = "movie") -> list[dict[str, Any]]:
    data = await _get(http, f"/trending/{media}/week")
    return [_summ(r, media) for r in data.get("results", [])[:20]]

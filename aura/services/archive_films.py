"""Free, legal feature films from the Internet Archive (public domain / freely licensed).

Catalogue: advancedsearch over collection:feature_films sorted by downloads, filtered to items that
have an H.264/MPEG4 file. Items are stored with url 'archive://<identifier>' and resolved to the best
MP4 at play time (one metadata call, cached), so refreshing the source stays cheap.
Poster: https://archive.org/services/img/<identifier>
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from .. import db
from ..models import StreamItem

log = logging.getLogger(__name__)

SEARCH = "https://archive.org/advancedsearch.php"
QUERY = "collection:feature_films AND mediatype:movies AND (format:h.264 OR format:MPEG4)"
FIELDS = ("identifier", "title", "year", "downloads", "description", "runtime", "avg_rating", "subject", "creator")
GROUP = "Films libres de droits"
_YEAR = re.compile(r"(18|19|20)\d{2}")


def _year(doc: dict[str, Any]) -> str:
    for k in ("year", "date"):
        v = doc.get(k)
        if isinstance(v, list):
            v = v[0] if v else ""
        m = _YEAR.search(str(v or ""))
        if m:
            return m.group(0)
    return ""


def _first(v: Any) -> str:
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def docs_to_items(docs: list[dict[str, Any]], source_id: str) -> list[StreamItem]:
    out: list[StreamItem] = []
    for d in docs:
        ident = d.get("identifier")
        title = _first(d.get("title")).strip()
        if not ident or not title:
            continue
        desc = re.sub(r"<[^>]+>", "", _first(d.get("description")))[:600]
        out.append(
            StreamItem.make(
                name=title,
                url=f"archive://{ident}",
                group=GROUP,
                logo=f"https://archive.org/services/img/{ident}",
                kind="vod",
                source_id=source_id,
                extra={
                    "year": _year(d),
                    "plot": desc,
                    "runtime": _first(d.get("runtime")),
                    "rating": d.get("avg_rating"),
                    "director": _first(d.get("creator")),
                    "downloads": d.get("downloads"),
                    "archive_id": ident,
                },
            )
        )
    return out


async def fetch_catalogue(http: httpx.AsyncClient, source_id: str, rows: int = 300) -> list[StreamItem]:
    params: list[tuple[str, str]] = [("q", QUERY), ("sort[]", "downloads desc"), ("rows", str(rows)), ("page", "1"), ("output", "json")]
    params += [("fl[]", f) for f in FIELDS]
    r = await http.get(SEARCH, params=params, timeout=40)
    r.raise_for_status()
    docs = r.json().get("response", {}).get("docs", [])
    return docs_to_items(docs, source_id)


def pick_file(files: list[dict[str, Any]]) -> str:
    """Best MP4: prefer h.264 original, then 512Kb MPEG4, else any .mp4; largest first."""
    mp4s = [f for f in files if str(f.get("name", "")).lower().endswith(".mp4")]
    if not mp4s:
        return ""

    def rank(f: dict[str, Any]) -> tuple[int, int]:
        fmt = str(f.get("format", "")).lower()
        score = 2 if "h.264" in fmt else 1 if "mpeg4" in fmt else 0
        try:
            size = int(f.get("size") or 0)
        except ValueError:
            size = 0
        return (score, size)

    mp4s.sort(key=rank, reverse=True)
    return str(mp4s[0]["name"])


async def resolve(http: httpx.AsyncClient, identifier: str) -> str:
    """archive://id -> direct https MP4 URL (cached 7 days)."""
    key = f"archive:url:{identifier}"
    cached = db.cache_get(key, 7 * 86400)
    if cached:
        return str(cached)
    r = await http.get(f"https://archive.org/metadata/{identifier}", timeout=30)
    r.raise_for_status()
    data = r.json()
    name = pick_file(data.get("files") or [])
    if not name:
        raise ValueError("aucun fichier MP4 pour cet élément")
    url = f"https://archive.org/download/{identifier}/{httpx.URL(name).path if False else name}"
    db.cache_set(key, url)
    return url

"""Search torrent metadata on the indexers the owner configured.

The box searches, never the browser: no CORS to fight, the indexer key never leaves the machine, and the
TV can reuse the same answers later. Aura ships no index of its own. The Internet Archive is always
available (public, legal, no key, every item exposes a .torrent); anything else is a Jackett or Prowlarr
instance the owner points us at through AURA_INDEXER_URL.

Results are metadata only: the download itself goes to qBittorrent, which Aura installs on the box.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import SETTINGS

log = logging.getLogger(__name__)

ARCHIVE_SEARCH = "https://archive.org/advancedsearch.php"
ARCHIVE_FIELDS = ("identifier", "title", "item_size", "publicdate", "downloads")
MIN_QUERY = 2
TIMEOUT_SECONDS = 12.0
DEFAULT_LIMIT = 30

# Indexers disagree on case and spelling; read every field through its known aliases.
ALIASES: dict[str, tuple[str, ...]] = {
    "id": ("id", "guid", "Guid", "infoHash", "info_hash", "InfoHash"),
    "name": ("name", "title", "Title"),
    "size": ("sizeBytes", "size_bytes", "size", "Size", "length"),
    "seeders": ("seeders", "Seeders", "seeds"),
    "leechers": ("leechers", "Leechers", "peers", "Peers"),
    "published": ("publishedAt", "published_at", "publishDate", "PublishDate", "pubDate", "date"),
    "torrent": ("torrentUrl", "torrent_url", "downloadUrl", "download_url", "Link", "link"),
    "magnet": ("magnetUrl", "magnet_url", "magnetUri", "MagnetUri", "magnet"),
    "details": ("detailsUrl", "details_url", "infoUrl", "info_url", "Details", "comments"),
    "indexer": ("indexer", "Indexer", "tracker", "Tracker", "site"),
}


def _pick(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, "", []):
            return value
    return None


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _date(value: Any) -> str:
    """Kept as the indexer wrote it; the UI only ever displays it."""
    text = _text(value)
    return text[:40]


def http_url(value: Any) -> str:
    """Links come from a third party and end up in an href or in qBittorrent: http(s) only."""
    text = _text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    return text if parsed.scheme in ("http", "https") and parsed.netloc else ""


def magnet_url(value: Any) -> str:
    text = _text(value)
    return text if text.lower().startswith("magnet:?") else ""


def _digest(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def normalise(raw: Any, indexer_fallback: str = "") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = _text(_pick(raw, ALIASES["name"]))
    if not name:
        return None  # a row without a name is noise, not a result
    torrent = http_url(_pick(raw, ALIASES["torrent"]))
    magnet = magnet_url(_pick(raw, ALIASES["magnet"]))
    if not torrent and not magnet:
        return None  # nothing to hand to qBittorrent
    size = _number(_pick(raw, ALIASES["size"]))
    return {
        "id": _text(_pick(raw, ALIASES["id"])) or _digest(name, str(size or ""), torrent or magnet),
        "name": name[:300],
        "size": size,
        "seeders": _number(_pick(raw, ALIASES["seeders"])),
        "leechers": _number(_pick(raw, ALIASES["leechers"])),
        "published": _date(_pick(raw, ALIASES["published"])),
        "torrent_url": torrent,
        "magnet": magnet,
        "details_url": http_url(_pick(raw, ALIASES["details"])),
        "indexer": _text(_pick(raw, ALIASES["indexer"])) or indexer_fallback,
    }


def _records(payload: Any) -> list[Any]:
    """Accepts { results: [...] }, { Results: [...] } (Jackett) or a bare array (Prowlarr)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "Results", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    raise ValueError("aucune liste de résultats dans la réponse")


def indexer_url() -> str:
    return SETTINGS.indexer_url


def sources() -> list[dict[str, str]]:
    """What this box can search right now, in the order results are merged."""
    found = [{"key": "archive", "name": "Internet Archive", "kind": "public"}]
    if SETTINGS.indexer_url:
        host = urlparse(SETTINGS.indexer_url).netloc or SETTINGS.indexer_url
        found.append({"key": "indexer", "name": host, "kind": "self-hosted"})
    return found


async def _search_archive(http: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    params: list[tuple[str, str]] = [("q", query)]
    params += [("fl[]", field) for field in ARCHIVE_FIELDS]
    params += [("rows", str(limit)), ("page", "1"), ("output", "json")]
    response = await http.get(ARCHIVE_SEARCH, params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    docs = (response.json() or {}).get("response", {}).get("docs", [])
    results = []
    for doc in docs if isinstance(docs, list) else []:
        identifier = _text(doc.get("identifier")) if isinstance(doc, dict) else ""
        if not identifier:
            continue
        results.append({
            "id": f"ia:{identifier}",
            "name": _text(doc.get("title")) or identifier,
            "size": _number(doc.get("item_size")),
            # The Internet Archive publishes no swarm counts: an em dash in the table beats an invented number.
            "seeders": None,
            "leechers": None,
            "published": _date(doc.get("publicdate")),
            "torrent_url": f"https://archive.org/download/{identifier}/{identifier}_archive.torrent",
            "magnet": "",
            "details_url": f"https://archive.org/details/{identifier}",
            "indexer": "Internet Archive",
        })
    return results


async def _search_indexer(http: httpx.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
    """Jackett and Prowlarr both answer JSON on their own REST endpoint."""
    base = SETTINGS.indexer_url.rstrip("/")
    prowlarr = "/api/v1" in base
    url = base if prowlarr else f"{base}/api/v2.0/indexers/all/results"
    params = {
        ("query" if prowlarr else "Query"): query,
        ("limit" if prowlarr else "Limit"): str(limit),
    }
    if SETTINGS.indexer_key:
        params["apikey"] = SETTINGS.indexer_key
    response = await http.get(url, params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    rows = _records(response.json())
    return [row for row in (normalise(raw) for raw in rows) if row]


SEARCHERS = {"archive": _search_archive, "indexer": _search_indexer}


def _rank(result: dict[str, Any]) -> tuple[int, int]:
    return (result.get("seeders") or -1, result.get("size") or 0)


def dedupe(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same release comes back from several indexers: keep the copy with the healthiest swarm."""
    best: dict[str, dict[str, Any]] = {}
    for result in results:
        key = (result["magnet"] or result["torrent_url"] or f"{result['name']}|{result['size']}").lower()
        current = best.get(key)
        if current is None or _rank(result) > _rank(current):
            best[key] = result
    return list(best.values())


async def search(http: httpx.AsyncClient, query: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Every configured source at once. A source that fails is reported, it never empties the answer."""
    query = query.strip()
    limit = max(1, min(limit, 100))
    if len(query) < MIN_QUERY:
        return {"results": [], "sources": sources(), "errors": []}

    configured = sources()
    tasks = [SEARCHERS[source["key"]](http, query, limit) for source in configured]
    answers = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for source, answer in zip(configured, answers):
        if isinstance(answer, BaseException):
            log.info("search on %s failed: %s", source["name"], answer)
            errors.append({"source": source["name"], "message": _explain(answer)})
            continue
        results.extend(answer)

    results = dedupe(results)
    results.sort(key=_rank, reverse=True)
    return {"results": results[:limit], "sources": configured, "errors": errors}


def _explain(error: BaseException) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return f"clé d'API refusée ({status})"
        return f"a répondu {status}"
    if isinstance(error, httpx.TimeoutException):
        return "délai dépassé"
    if isinstance(error, httpx.HTTPError):
        return "injoignable"
    return str(error)[:120]

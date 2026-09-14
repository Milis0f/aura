"""Sources (playlists) and channel catalogue: add/refresh sources, query channels, favorites, history."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .. import db
from ..config import FREE_BUNDLES, SETTINGS
from ..models import Source, StreamItem
from . import archive_films, epg, m3u_parser, xtream
from .textutil import fold, loose_key, normalize

log = logging.getLogger(__name__)


# ---- sources --------------------------------------------------------------

def _row_to_source(r: dict[str, Any]) -> Source:
    return Source(
        id=r["id"],
        type=r["type"],
        name=r["name"],
        url=r["url"],
        username=r["username"],
        password=r["password"],
        epg_url=r["epg_url"],
        enabled=bool(r["enabled"]),
        last_refresh=r["last_refresh"],
        status=r["status"],
        item_count=r["item_count"],
    )


def list_sources() -> list[Source]:
    return [_row_to_source(r) for r in db.query("SELECT * FROM sources ORDER BY created")]


def get_source(source_id: str) -> Source | None:
    r = db.query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    return _row_to_source(r) if r else None


NAMES_VERSION = "2"


def migrate_names() -> int:
    """Recompute stored name keys after a change to the normalisation rules. Runs once per version."""
    if db.get_setting("names_version", "") == NAMES_VERSION:
        return 0
    rows = db.query("SELECT rowid, name FROM channels")
    epg_rows = db.query("SELECT rowid, display_name FROM epg_channels")
    with db.transaction() as con:
        con.executemany(
            "UPDATE channels SET name_norm=?, name_fold=? WHERE rowid=?",
            [(normalize(r["name"]), fold(r["name"]), r["rowid"]) for r in rows],
        )
        con.executemany(
            "UPDATE epg_channels SET display_norm=? WHERE rowid=?",
            [(loose_key(r["display_name"]), r["rowid"]) for r in epg_rows],
        )
    db.set_setting("names_version", NAMES_VERSION)
    return len(rows)


def purge_invalid_channels() -> int:
    """Delete rows whose URL has no scheme: leftovers of pages parsed as playlists by older versions."""
    return db.execute(
        "DELETE FROM channels WHERE url NOT GLOB '[a-zA-Z]*://?*' OR instr(url, ' ') > 0 OR instr(url, '<') > 0 "
        "OR instr(url, '>') > 0 OR instr(url, char(34)) > 0 OR instr(url, char(39)) > 0"
    )


def dedupe_sources() -> int:
    """Remove playlists registered more than once (same URL). Keeps the oldest registration.

    Duplicates add nothing but identical rows, and they pollute the failover list with the same
    dead URL several times.
    """
    rows = db.query(
        "SELECT id FROM sources s WHERE url<>'' AND type IN ('m3u_url','free','archive') "
        "AND EXISTS (SELECT 1 FROM sources o WHERE o.url=s.url AND (o.created < s.created OR (o.created = s.created AND o.id < s.id)))"
    )
    for r in rows:
        delete_source(r["id"])
    return len(rows)


def find_source_by_url(url: str) -> Source | None:
    row = db.query_one("SELECT * FROM sources WHERE url=? LIMIT 1", (url.strip(),)) if url.strip() else None
    return _row_to_source(row) if row else None


def add_source(
    type_: str, name: str, url: str = "", username: str = "", password: str = "", epg_url: str = ""
) -> Source:
    existing = find_source_by_url(url) if type_ in ("m3u_url", "free", "archive") else None
    if existing:
        return existing
    sid = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO sources(id,type,name,url,username,password,epg_url,enabled,created) "
        "VALUES(?,?,?,?,?,?,?,1,?)",
        (sid, type_, name.strip() or type_, url.strip(), username, password, epg_url.strip(), int(time.time())),
    )
    src = get_source(sid)
    assert src is not None
    return src


def add_free_bundle(key: str) -> Source | None:
    bundle = next((b for b in FREE_BUNDLES if b["key"] == key), None)
    if not bundle:
        return None
    existing = db.query_one("SELECT id FROM sources WHERE type IN ('free','archive') AND url=?", (bundle["url"],))
    if existing:
        return get_source(existing["id"])
    return add_source(bundle.get("type", "free"), bundle["name"], bundle["url"])


def update_source(source_id: str, **fields: Any) -> Source | None:
    allowed = {"name", "url", "username", "password", "epg_url", "enabled"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if sets:
        cols = ", ".join(f"{k}=?" for k in sets)
        db.execute(f"UPDATE sources SET {cols} WHERE id=?", (*sets.values(), source_id))
    return get_source(source_id)


def delete_source(source_id: str) -> None:
    with db.transaction() as con:
        con.execute("DELETE FROM channels WHERE source_id=?", (source_id,))
        con.execute("DELETE FROM sources WHERE id=?", (source_id,))


def _set_status(source_id: str, status: str, count: int | None = None) -> None:
    if count is None:
        db.execute("UPDATE sources SET status=? WHERE id=?", (status, source_id))
    else:
        db.execute(
            "UPDATE sources SET status=?, item_count=?, last_refresh=? WHERE id=?",
            (status, count, int(time.time()), source_id),
        )


def replace_channels(source_id: str, items: list[StreamItem] | tuple[StreamItem, ...]) -> int:
    with db.transaction() as con:
        con.execute("DELETE FROM channels WHERE source_id=?", (source_id,))
        con.executemany(
            "INSERT OR REPLACE INTO channels(id,source_id,name,name_norm,name_fold,group_name,url,logo,tvg_id,kind,extra,sort) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    it.id,
                    source_id,
                    it.name,
                    normalize(it.name),
                    fold(it.name),
                    it.group,
                    it.url,
                    it.logo,
                    it.tvg_id,
                    it.kind,
                    json.dumps(it.extra, ensure_ascii=False),
                    i,
                )
                for i, it in enumerate(items)
            ],
        )
    return len(items)


async def refresh_source(http: httpx.AsyncClient, src: Source) -> Source:
    """Fetch + parse one source and replace its channels. Never raises; status is recorded."""
    _set_status(src.id, "refreshing")
    try:
        if src.type == "xtream":
            count, epg_url = await _refresh_xtream(http, src)
        elif src.type == "archive":
            items = await archive_films.fetch_catalogue(http, src.id)
            count, epg_url = await asyncio.to_thread(replace_channels, src.id, items), ""
        elif src.type == "m3u_file":
            text = await asyncio.to_thread(Path(src.url).read_text, encoding="utf-8", errors="replace")
            parsed = await asyncio.to_thread(m3u_parser.parse_m3u, text, src.id)
            count = await asyncio.to_thread(replace_channels, src.id, parsed.items)
            epg_url = parsed.epg_url
        else:
            r = await http.get(src.url, follow_redirects=True)
            r.raise_for_status()
            head = r.text[:65536]
            if not head.lstrip().startswith("#EXTM3U") and "#EXTINF" not in head:
                raise ValueError("cette adresse renvoie une page web, pas une playlist M3U")
            parsed = await asyncio.to_thread(m3u_parser.parse_m3u, r.text, src.id)
            count = await asyncio.to_thread(replace_channels, src.id, parsed.items)
            epg_url = parsed.epg_url
        if epg_url and not src.epg_url:
            db.execute("UPDATE sources SET epg_url=? WHERE id=?", (epg_url, src.id))
        _set_status(src.id, "ok", count)
        log.info("source %s refreshed: %d items", src.name, count)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        log.warning("source %s failed: %s", src.name, exc)
        _set_status(src.id, f"erreur: {exc}"[:200], 0)
    refreshed = get_source(src.id)
    assert refreshed is not None
    return refreshed


async def _refresh_xtream(http: httpx.AsyncClient, src: Source) -> tuple[int, str]:
    creds = xtream.XtreamCreds.from_any(src.url, src.username, src.password)
    client = xtream.XtreamClient(creds, http)
    auth = await client.auth()
    ext = xtream.pick_live_ext(auth.get("user_info", {}))
    items: list[StreamItem] = []
    for kind, fn, conv in (
        ("live", client.live_streams, lambda s, c: xtream.live_to_items(creds, s, c, ext, src.id)),
        ("vod", client.vod_streams, lambda s, c: xtream.vod_to_items(creds, s, c, src.id)),
        ("series", client.series, lambda s, c: xtream.series_to_items(creds, s, c, src.id)),
    ):
        try:
            cats = await client.categories(kind)
            streams = await fn()
            items.extend(conv(streams, cats))
        except Exception as exc:  # noqa: BLE001
            log.warning("xtream %s %s failed: %s", src.name, kind, exc)
    count = await asyncio.to_thread(replace_channels, src.id, items)
    return count, creds.epg_url


async def refresh_all_sources(http: httpx.AsyncClient, concurrency: int = 4) -> list[Source]:
    """Refresh every enabled source concurrently (was sequential: 6 sources = 6x the wait)."""
    sem = asyncio.Semaphore(concurrency)

    async def one(src: Source) -> Source:
        async with sem:
            return await refresh_source(http, src)

    sources = [s for s in list_sources() if s.enabled]
    if not sources:
        return []
    return list(await asyncio.gather(*(one(s) for s in sources)))


# ---- EPG sources ----------------------------------------------------------

def list_epg_sources() -> list[dict[str, Any]]:
    return db.query("SELECT * FROM epg_sources ORDER BY priority, name")


def add_epg_source(name: str, url: str, priority: int = 100) -> dict[str, Any]:
    existing = db.query_one("SELECT * FROM epg_sources WHERE url=?", (url,))
    if existing:
        return existing
    eid = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO epg_sources(id,name,url,enabled,priority) VALUES(?,?,?,1,?)", (eid, name, url, priority)
    )
    row = db.query_one("SELECT * FROM epg_sources WHERE id=?", (eid,))
    assert row is not None
    return row


def delete_epg_source(eid: str) -> None:
    with db.transaction() as con:
        con.execute("DELETE FROM epg_channels WHERE epg_source_id=?", (eid,))
        con.execute("DELETE FROM epg_programmes WHERE epg_source_id=?", (eid,))
        con.execute("DELETE FROM epg_sources WHERE id=?", (eid,))


async def refresh_epg(http: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Refresh every enabled EPG source plus the EPG URLs advertised by playlists/Xtream."""
    urls: list[tuple[str, str, str]] = [(r["id"], r["name"], r["url"]) for r in list_epg_sources() if r["enabled"]]
    for src in list_sources():
        if src.enabled and src.epg_url:
            urls.append((f"src-{src.id}", f"EPG {src.name}", src.epg_url))
    results = []
    for eid, name, url in urls:
        try:
            data = await epg.fetch_bytes(http, url)
            n_ch, n_pr = epg.store_epg(eid, data)
            status = f"ok ({n_ch} chaînes, {n_pr} programmes)"
        except Exception as exc:  # noqa: BLE001
            status = f"erreur: {exc}"[:200]
            log.warning("EPG %s failed: %s", name, exc)
        db.execute(
            "UPDATE epg_sources SET status=?, last_refresh=? WHERE id=?", (status, int(time.time()), eid)
        )
        results.append({"id": eid, "name": name, "status": status})
    epg.purge_old()
    db.set_setting("epg_last_refresh", str(int(time.time())))
    return results


# ---- channels -------------------------------------------------------------

def _row_to_channel(r: dict[str, Any]) -> dict[str, Any]:
    try:
        extra = json.loads(r.get("extra") or "{}")
    except json.JSONDecodeError:
        extra = {}
    return {
        "id": r["id"],
        "source_id": r["source_id"],
        "name": r["name"],
        "group": r["group_name"],
        "group_name": r["group_name"],
        "url": r["url"],
        "logo": r["logo"],
        "tvg_id": r["tvg_id"],
        "kind": r["kind"],
        "extra": extra,
    }


SORTS = {
    "default": "c.source_id, c.sort",
    "added": "CAST(json_extract(c.extra, '$.added') AS INTEGER) DESC, c.sort DESC",
    "rating": "CAST(json_extract(c.extra, '$.rating') AS REAL) DESC, c.sort",
    "year": "json_extract(c.extra, '$.year') DESC, c.sort",
    "name": "c.name_norm",
    "popular": "CAST(json_extract(c.extra, '$.downloads') AS INTEGER) DESC, c.sort",
}


def _where(kind: str, group: str | None, q: str | None, source_id: str | None) -> tuple[str, list[Any]]:
    sql = " FROM channels c JOIN sources s ON s.id=c.source_id WHERE s.enabled=1 AND c.kind=?"
    params: list[Any] = [kind]
    if group:
        sql += " AND c.group_name=?"
        params.append(group)
    if source_id:
        sql += " AND c.source_id=?"
        params.append(source_id)
    if q:
        key = fold(q)
        if key:
            sql += " AND c.name_fold LIKE ?"
            params.append(f"%{key}%")
        else:  # punctuation-only query: plain substring on the raw name
            sql += " AND lower(c.name) LIKE ?"
            params.append(f"%{q.lower().strip()}%")
    return sql, params


def channels(
    kind: str = "live",
    group: str | None = None,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
    source_id: str | None = None,
    sort: str = "default",
) -> list[dict[str, Any]]:
    where, params = _where(kind, group, q, source_id)
    if q and fold(q) and sort == "default":
        # Relevance: exact name, then names starting with the query, then a word starting with it,
        # then any substring; shorter names first inside a tier.
        key = fold(q)
        order = (
            "CASE WHEN c.name_fold = ? THEN 0 WHEN c.name_fold LIKE ? THEN 1 "
            "WHEN c.name_fold LIKE ? THEN 2 ELSE 3 END, length(c.name), c.source_id, c.sort"
        )
        params = params + [key, key + "%", "% " + key + "%"]
        sql = f"SELECT c.*{where} ORDER BY {order} LIMIT ? OFFSET ?"
        return [_row_to_channel(r) for r in db.query(sql, tuple(params + [limit, offset]))]
    order = SORTS.get(sort, SORTS["default"])
    sql = f"SELECT c.*{where} ORDER BY {order} LIMIT ? OFFSET ?"
    return [_row_to_channel(r) for r in db.query(sql, tuple(params + [limit, offset]))]


def count_channels(kind: str, group: str | None = None, q: str | None = None, source_id: str | None = None) -> int:
    where, params = _where(kind, group, q, source_id)
    row = db.query_one(f"SELECT COUNT(*) AS n{where}", tuple(params))
    return int(row["n"]) if row else 0


def groups(kind: str = "live") -> list[dict[str, Any]]:
    return db.query(
        "SELECT c.group_name AS name, COUNT(*) AS count FROM channels c JOIN sources s ON s.id=c.source_id "
        "WHERE s.enabled=1 AND c.kind=? GROUP BY c.group_name ORDER BY MIN(c.source_id), MIN(c.sort)",
        (kind,),
    )


def get_channel(channel_id: str) -> dict[str, Any] | None:
    r = db.query_one("SELECT * FROM channels WHERE id=? LIMIT 1", (channel_id,))
    return _row_to_channel(r) if r else None


def alternatives(channel: dict[str, Any], limit: int = 6) -> list[dict[str, Any]]:
    """Other live streams that look like the same channel (failover list)."""
    key = normalize(channel["name"])
    # Same identity key or same tvg-id; the very same URL listed in another playlist is no backup.
    rows = db.query(
        "SELECT * FROM channels WHERE kind='live' AND id<>? AND url<>? "
        "AND ((? <> '' AND name_norm=?) OR (tvg_id<>'' AND tvg_id=?)) GROUP BY url LIMIT ?",
        (channel["id"], channel["url"], key, key, channel.get("tvg_id", ""), limit),
    )
    return [_row_to_channel(r) for r in rows]


def with_epg(chs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach now/next programmes to live channels. Bulk queries only (immutable: new dicts)."""
    live = [c for c in chs if c["kind"] == "live"]
    if not live:
        return list(chs)
    known = epg.known_ids({c["tvg_id"] for c in live})
    by_key = epg.ids_by_display({loose_key(c["name"]) for c in live})
    resolved = {
        c["id"]: (c["tvg_id"] if c["tvg_id"] in known else by_key.get(loose_key(c["name"]), ""))
        for c in live
    }
    programmes = epg.now_next_bulk(set(resolved.values()))
    out = []
    for c in chs:
        if c["kind"] != "live":
            out.append(c)
            continue
        eid = resolved.get(c["id"], "")
        nn = programmes.get(eid, []) if eid else []
        out.append({**c, "epg_id": eid, "now": nn[0] if nn else None, "next": nn[1] if len(nn) > 1 else None})
    return out


def search(q: str, limit: int = 60) -> dict[str, list[dict[str, Any]]]:
    return {
        "live": channels("live", q=q, limit=limit),
        "vod": channels("vod", q=q, limit=limit),
        "series": channels("series", q=q, limit=limit),
    }


def counts() -> dict[str, int]:
    rows = db.query(
        "SELECT c.kind, COUNT(*) AS n FROM channels c JOIN sources s ON s.id=c.source_id WHERE s.enabled=1 GROUP BY c.kind"
    )
    base = {"live": 0, "vod": 0, "series": 0}
    return {**base, **{r["kind"]: r["n"] for r in rows}}


# ---- favorites / history / my list ---------------------------------------

def favorites() -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT c.* FROM favorites f JOIN channels c ON c.id=f.channel_id GROUP BY c.id ORDER BY f.added DESC"
    )
    return [_row_to_channel(r) for r in rows]


def favorite_ids() -> set[str]:
    return {r["channel_id"] for r in db.query("SELECT channel_id FROM favorites")}


def toggle_favorite(channel_id: str) -> bool:
    if db.query_one("SELECT 1 FROM favorites WHERE channel_id=?", (channel_id,)):
        db.execute("DELETE FROM favorites WHERE channel_id=?", (channel_id,))
        return False
    db.execute("INSERT INTO favorites(channel_id, added) VALUES(?,?)", (channel_id, int(time.time())))
    return True


def record_history(channel_id: str, name: str, kind: str, position: float = 0, duration: float = 0) -> None:
    db.execute(
        "INSERT INTO history(channel_id,name,kind,ts,position,duration) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(channel_id) DO UPDATE SET ts=excluded.ts, position=excluded.position, "
        "duration=excluded.duration, name=excluded.name",
        (channel_id, name, kind, int(time.time()), position, duration),
    )


def history(limit: int = 20) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT h.*, c.logo, c.url, c.group_name, c.extra FROM history h LEFT JOIN channels c ON c.id=h.channel_id "
        "WHERE c.id IS NOT NULL OR h.channel_id IN (SELECT id FROM my_list) "
        "GROUP BY h.channel_id ORDER BY h.ts DESC LIMIT ?",
        (limit,),
    )
    out = []
    for r in rows:
        try:
            extra = json.loads(r.get("extra") or "{}")
        except json.JSONDecodeError:
            extra = {}
        out.append({**{k: r[k] for k in ("channel_id", "name", "kind", "ts", "position", "duration")}, "logo": r.get("logo") or "", "url": r.get("url") or "", "group": r.get("group_name") or "", "extra": extra})
    return out


def my_list() -> list[dict[str, Any]]:
    return db.query("SELECT * FROM my_list ORDER BY added DESC")


def add_to_my_list(title: str, url: str, kind: str = "vod", tmdb_id: int = 0, poster: str = "", year: str = "") -> dict[str, Any]:
    mid = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO my_list(id,title,url,kind,tmdb_id,poster,year,added) VALUES(?,?,?,?,?,?,?,?)",
        (mid, title.strip(), url.strip(), kind, tmdb_id or 0, poster, year, int(time.time())),
    )
    row = db.query_one("SELECT * FROM my_list WHERE id=?", (mid,))
    assert row is not None
    return row


def remove_from_my_list(mid: str) -> None:
    db.execute("DELETE FROM my_list WHERE id=?", (mid,))


def is_configured() -> bool:
    return bool(db.query_one("SELECT 1 FROM sources LIMIT 1"))


def default_headers() -> dict[str, str]:
    return {"User-Agent": SETTINGS.user_agent}

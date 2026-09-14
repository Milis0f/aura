"""XMLTV EPG: fetch (plain or gzip), stream-parse, store, query now/next.

XMLTV time format: 'YYYYMMDDHHMMSS +HHMM' (offset optional, then assumed UTC).
"""

from __future__ import annotations

import gzip
import io
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx

from .. import db
from ..models import EpgChannel, EpgProgramme
from .textutil import loose_key

log = logging.getLogger(__name__)

_TIME = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?\s*([+-]\d{4})?$")


def parse_xmltv_time(value: str) -> int:
    """'20260910200000 +0200' -> unix seconds. Returns 0 when unparsable."""
    m = _TIME.match((value or "").strip())
    if not m:
        return 0
    y, mo, d, h, mi, s, off = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0), int(s or 0), tzinfo=timezone.utc)
    if off:
        sign = 1 if off[0] == "+" else -1
        delta = timedelta(hours=int(off[1:3]), minutes=int(off[3:5]))
        dt = dt - sign * delta
    return int(dt.timestamp())


def _maybe_gunzip(data: bytes) -> bytes:
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def iter_xmltv(data: bytes) -> Iterator[EpgChannel | EpgProgramme]:
    """Stream-parse XMLTV bytes, yielding channels first (as they appear) then programmes."""
    stream = io.BytesIO(_maybe_gunzip(data))
    for _, el in ET.iterparse(stream, events=("end",)):
        if el.tag == "channel":
            names = [n.text or "" for n in el.findall("display-name")]
            icon = el.find("icon")
            yield EpgChannel(
                id=el.get("id", ""),
                display_name=(names[0] if names else el.get("id", "")).strip(),
                icon=icon.get("src", "") if icon is not None else "",
            )
            el.clear()
        elif el.tag == "programme":
            title = el.findtext("title") or ""
            desc = el.findtext("desc") or ""
            cat = el.findtext("category") or ""
            yield EpgProgramme(
                channel_id=el.get("channel", ""),
                start=parse_xmltv_time(el.get("start", "")),
                stop=parse_xmltv_time(el.get("stop", "")),
                title=title.strip(),
                description=desc.strip(),
                category=cat.strip(),
            )
            el.clear()


async def fetch_bytes(http: httpx.AsyncClient, url: str) -> bytes:
    r = await http.get(url, follow_redirects=True)
    r.raise_for_status()
    return r.content


def store_epg(epg_source_id: str, data: bytes, keep_days_past: int = 2) -> tuple[int, int]:
    """Replace all EPG rows of one source. Returns (channels, programmes) stored."""
    cutoff = int(time.time()) - keep_days_past * 86400
    n_ch = n_pr = 0
    with db.transaction() as con:
        con.execute("DELETE FROM epg_channels WHERE epg_source_id=?", (epg_source_id,))
        con.execute("DELETE FROM epg_programmes WHERE epg_source_id=?", (epg_source_id,))
        ch_batch: list[tuple] = []
        pr_batch: list[tuple] = []
        for obj in iter_xmltv(data):
            if isinstance(obj, EpgChannel):
                ch_batch.append(
                    (obj.id, epg_source_id, obj.display_name, loose_key(obj.display_name), obj.icon)
                )
                n_ch += 1
            else:
                if obj.stop and obj.stop < cutoff:
                    continue
                pr_batch.append(
                    (
                        obj.channel_id,
                        epg_source_id,
                        obj.start,
                        obj.stop,
                        obj.title,
                        obj.description,
                        obj.category,
                    )
                )
                n_pr += 1
            if len(ch_batch) >= 500:
                con.executemany(
                    "INSERT OR REPLACE INTO epg_channels VALUES(?,?,?,?,?)", ch_batch
                )
                ch_batch = []
            if len(pr_batch) >= 2000:
                con.executemany("INSERT INTO epg_programmes VALUES(?,?,?,?,?,?,?)", pr_batch)
                pr_batch = []
        if ch_batch:
            con.executemany("INSERT OR REPLACE INTO epg_channels VALUES(?,?,?,?,?)", ch_batch)
        if pr_batch:
            con.executemany("INSERT INTO epg_programmes VALUES(?,?,?,?,?,?,?)", pr_batch)
    return n_ch, n_pr


def resolve_epg_id(tvg_id: str, name: str) -> str | None:
    """Map a playlist channel to an XMLTV channel id: exact tvg-id, then loose name match."""
    if tvg_id:
        row = db.query_one("SELECT id FROM epg_channels WHERE id=? LIMIT 1", (tvg_id,))
        if row:
            return row["id"]
    key = loose_key(name)
    if not key:
        return None
    row = db.query_one(
        "SELECT id FROM epg_channels WHERE display_norm=? ORDER BY epg_source_id LIMIT 1", (key,)
    )
    return row["id"] if row else None


def now_next(epg_id: str, at: int | None = None, limit: int = 2) -> list[dict[str, Any]]:
    at = at or int(time.time())
    rows = db.query(
        "SELECT channel_id, start, stop, title, description, category FROM epg_programmes "
        "WHERE channel_id=? AND stop > ? ORDER BY start LIMIT ?",
        (epg_id, at, limit),
    )
    return rows


def programmes_between(epg_id: str, start: int, stop: int) -> list[dict[str, Any]]:
    return db.query(
        "SELECT channel_id, start, stop, title, description, category FROM epg_programmes "
        "WHERE channel_id=? AND stop > ? AND start < ? ORDER BY start",
        (epg_id, start, stop),
    )


def search_programmes(keywords: list[str], start: int, stop: int, limit: int = 200) -> list[dict[str, Any]]:
    """Case-insensitive title search over a time window (used by the sports matcher)."""
    if not keywords:
        return []
    clauses = " OR ".join("lower(title) LIKE ?" for _ in keywords)
    params: list[Any] = [f"%{k.lower()}%" for k in keywords]
    params += [start, stop, limit]
    return db.query(
        f"SELECT channel_id, start, stop, title, category FROM epg_programmes "
        f"WHERE ({clauses}) AND stop > ? AND start < ? ORDER BY start LIMIT ?",
        tuple(params),
    )


def purge_old(days: int = 2) -> int:
    return db.execute("DELETE FROM epg_programmes WHERE stop < ?", (int(time.time()) - days * 86400,))


# ---- bulk lookups (one query instead of one per channel) -------------------

def known_ids(ids: set[str]) -> set[str]:
    """Subset of `ids` that exist as XMLTV channel ids."""
    out: set[str] = set()
    for chunk in db.chunked({i for i in ids if i}):
        marks = ",".join("?" * len(chunk))
        out |= {r["id"] for r in db.query(f"SELECT DISTINCT id FROM epg_channels WHERE id IN ({marks})", tuple(chunk))}
    return out


def ids_by_display(keys: set[str]) -> dict[str, str]:
    """Map loose display-name keys to XMLTV channel ids."""
    out: dict[str, str] = {}
    for chunk in db.chunked({k for k in keys if k}):
        marks = ",".join("?" * len(chunk))
        for r in db.query(
            f"SELECT display_norm, id FROM epg_channels WHERE display_norm IN ({marks}) ORDER BY epg_source_id",
            tuple(chunk),
        ):
            out.setdefault(r["display_norm"], r["id"])
    return out


def now_next_bulk(ids: set[str], at: int | None = None, window: int = 6 * 3600) -> dict[str, list[dict[str, Any]]]:
    """{channel_id: [now, next]} for many channels in one query per chunk."""
    at = at or int(time.time())
    out: dict[str, list[dict[str, Any]]] = {}
    for chunk in db.chunked({i for i in ids if i}):
        marks = ",".join("?" * len(chunk))
        rows = db.query(
            f"SELECT channel_id, start, stop, title, description, category FROM epg_programmes "
            f"WHERE channel_id IN ({marks}) AND stop > ? AND start < ? ORDER BY channel_id, start",
            (*chunk, at, at + window),
        )
        for r in rows:
            bucket = out.setdefault(r["channel_id"], [])
            if len(bucket) < 2:
                bucket.append(r)
    return out

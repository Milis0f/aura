"""Local library: films and series found on drives and watched folders, plus films added by link.

One row per film, series or link in media_items, one row per video file in media_files (an episode, or one version
of a film), resume points in media_progress. Ids derive from content (drive + path, kind + title + year), so
unplugging a drive, losing a file row or rescanning never loses a resume point.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from .. import db
from . import mediaparse
from .storage import Volume
from .textutil import fold

FILM, SERIES, LINK = "film", "series", "link"
FINISHED_RATIO = 0.92
RESUME_MIN_SECONDS = 30
QUALITY_RANK = {"4K": 4, "1080p": 3, "720p": 2, "SD": 1, "": 0}
LANG_ORDER = ("MULTI", "VF", "VOSTFR")
SORTS = {
    "added": "i.last_added DESC, i.title_fold",
    "title": "i.title_fold, i.year",
    "year": "i.year DESC, i.title_fold",
    "rating": "i.rating DESC, i.last_added DESC",
}

# Availability as EXISTS rather than SUM(...) > 0: it stops at the first file on a mounted drive, where
# the aggregate had to read every file of every title before it could filter anything out.
_ONLINE = ("(i.kind = 'link' OR EXISTS (SELECT 1 FROM media_files f JOIN drives d ON d.id = f.drive_id "
           "WHERE f.item_id = i.id AND d.available = 1))")
_ANY_FILE = "(i.kind = 'link' OR EXISTS (SELECT 1 FROM media_files f WHERE f.item_id = i.id))"


def _present(online_only: bool) -> str:
    return _ONLINE if online_only else _ANY_FILE

_ITEM_SELECT = """
SELECT i.id, i.kind, i.title, i.year, i.poster, i.backdrop, i.overview, i.genres, i.rating, i.runtime, i.url,
       i.art, i.meta_state, i.added,
       COUNT(f.id) AS files,
       COALESCE(SUM(CASE WHEN d.available = 1 THEN 1 ELSE 0 END), 0) AS files_online,
       COALESCE(MAX(f.added), i.added) AS last_added,
       COUNT(DISTINCT CASE WHEN i.kind = 'series' THEN f.season END) AS seasons,
       group_concat(DISTINCT f.quality) AS qualities,
       group_concat(DISTINCT f.langs) AS langs_all,
       group_concat(DISTINCT d.label) AS drive_labels,
       COALESCE(MAX(f.duration), 0) AS duration
FROM media_items i
LEFT JOIN media_files f ON f.item_id = i.id
LEFT JOIN drives d ON d.id = f.drive_id
"""


class FoundLike(Protocol):
    rel_path: str
    size: int
    mtime: int
    art: str


def _digest(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def file_id_for(drive_id: str, rel_path: str) -> str:
    return "v" + _digest(drive_id, rel_path)[:15]


def item_id_for(kind: str, title: str, year: str = "") -> str:
    return ("s" if kind == SERIES else "f") + _digest(kind, fold(title), year)[:15]


# ---- drives ---------------------------------------------------------------

def upsert_drive(volume: Volume, available: bool) -> None:
    db.execute(
        "INSERT INTO drives(id, label, kind, fstype, device, mountpoint, size, removable, managed, available, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label, kind=excluded.kind, "
        "fstype=excluded.fstype, device=excluded.device, mountpoint=excluded.mountpoint, size=excluded.size, "
        "removable=excluded.removable, managed=excluded.managed, available=excluded.available, last_seen=excluded.last_seen",
        (volume.id, volume.label, volume.kind, volume.fstype, volume.device, volume.mountpoint, volume.size,
         int(volume.removable), volume.managed, int(available), int(time.time())),
    )


def mark_unavailable_except(available_ids: Iterable[str]) -> list[dict[str, Any]]:
    keep = set(available_ids)
    gone = [d for d in db.query("SELECT * FROM drives WHERE available = 1") if d["id"] not in keep]
    for d in gone:
        db.execute(
            "UPDATE drives SET available = 0, scan_state = CASE WHEN scan_state = 'scanning' THEN '' ELSE scan_state END WHERE id = ?",
            (d["id"],),
        )
    return gone


def get_drive(drive_id: str) -> dict[str, Any] | None:
    row = db.query_one("SELECT * FROM drives WHERE id = ?", (drive_id,))
    return _drive_dict(row) if row else None


def drives() -> list[dict[str, Any]]:
    return [_drive_dict(d) for d in db.query("SELECT * FROM drives ORDER BY available DESC, kind, label COLLATE NOCASE")]


def _drive_dict(d: dict[str, Any]) -> dict[str, Any]:
    return {**d, "available": bool(d["available"]), "removable": bool(d["removable"])}


def set_scan_state(drive_id: str, state: str) -> None:
    db.execute("UPDATE drives SET scan_state = ? WHERE id = ?", (state[:300], drive_id))


def finish_scan(drive_id: str) -> dict[str, int]:
    row = db.query_one(
        "SELECT COUNT(DISTINCT CASE WHEN i.kind = 'film' THEN i.id END) AS films, "
        "COALESCE(SUM(CASE WHEN i.kind = 'series' THEN 1 ELSE 0 END), 0) AS episodes "
        "FROM media_files f JOIN media_items i ON i.id = f.item_id WHERE f.drive_id = ?",
        (drive_id,),
    ) or {}
    films, episodes = int(row.get("films") or 0), int(row.get("episodes") or 0)
    db.execute(
        "UPDATE drives SET last_scan = ?, scan_state = 'done', films = ?, episodes = ? WHERE id = ?",
        (int(time.time()), films, episodes, drive_id),
    )
    return {"films": films, "episodes": episodes}


def _refresh_last_added(con: Any, item_ids: Sequence[str]) -> None:
    """Recompute the stored sort key for titles whose files just changed."""
    for chunk in db.chunked(list(item_ids)):
        marks = ",".join("?" * len(chunk))
        con.execute(
            "UPDATE media_items SET last_added = COALESCE("
            "  (SELECT MAX(f.added) FROM media_files f WHERE f.item_id = media_items.id), added) "
            f"WHERE id IN ({marks})",
            tuple(chunk),
        )


def forget_drive(drive_id: str) -> bool:
    """Drop an unplugged drive and what the library knew about it. Resume points stay."""
    d = get_drive(drive_id)
    if not d or d["available"]:
        return False
    with db.transaction() as con:
        touched = [r["item_id"] for r in
                   con.execute("SELECT DISTINCT item_id FROM media_files WHERE drive_id = ?", (drive_id,))]
        con.execute("DELETE FROM media_files WHERE drive_id = ?", (drive_id,))
        con.execute("DELETE FROM drives WHERE id = ?", (drive_id,))
        _refresh_last_added(con, touched)
        con.execute("DELETE FROM media_items WHERE kind <> 'link' AND NOT EXISTS (SELECT 1 FROM media_files f WHERE f.item_id = media_items.id)")
    return True


# ---- scan reconciliation --------------------------------------------------

def existing_files(drive_id: str) -> dict[str, dict[str, Any]]:
    rows = db.query("SELECT id, rel_path, size, mtime, art FROM media_files WHERE drive_id = ?", (drive_id,))
    return {r["rel_path"]: r for r in rows}


def apply_scan(drive_id: str, changed: Sequence[tuple[FoundLike, mediaparse.ParsedName]], removed_ids: Sequence[str], now: int) -> int:
    """Write one scan in a single transaction. Returns how many films or series were new."""
    new_items = 0
    with db.transaction() as con:
        for chunk in db.chunked(removed_ids):
            con.execute(f"DELETE FROM media_files WHERE id IN ({','.join('?' * len(chunk))})", chunk)
        for found, parsed in changed:
            kind = SERIES if parsed.kind == "episode" else FILM
            year = parsed.year if kind == FILM else ""
            item_id = item_id_for(kind, parsed.title, year)
            cur = con.execute(
                "INSERT OR IGNORE INTO media_items(id, kind, title, title_fold, year, added, updated) VALUES (?,?,?,?,?,?,?)",
                (item_id, kind, parsed.title, fold(parsed.title), year, now, now),
            )
            new_items += max(cur.rowcount, 0)
            art = f"{drive_id}|{found.art}" if found.art else ""
            if art:
                con.execute("UPDATE media_items SET art = ? WHERE id = ? AND art = ''", (art, item_id))
            con.execute(
                "INSERT INTO media_files(id, drive_id, rel_path, size, mtime, item_id, season, episode, episode_title, "
                "quality, langs, art, added) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "size=excluded.size, mtime=excluded.mtime, item_id=excluded.item_id, season=excluded.season, "
                "episode=excluded.episode, episode_title=excluded.episode_title, quality=excluded.quality, "
                "langs=excluded.langs, art=excluded.art, "
                "probed=CASE WHEN media_files.size = excluded.size THEN media_files.probed ELSE 0 END",
                (file_id_for(drive_id, found.rel_path), drive_id, found.rel_path, found.size, found.mtime, item_id,
                 parsed.season, parsed.episode, parsed.episode_title, parsed.quality, ",".join(parsed.langs), art, now),
            )
        con.execute("DELETE FROM media_items WHERE kind <> 'link' AND NOT EXISTS (SELECT 1 FROM media_files f WHERE f.item_id = media_items.id)")
        con.execute(
            "UPDATE media_items SET last_added = COALESCE("
            "  (SELECT MAX(f.added) FROM media_files f WHERE f.item_id = media_items.id), added) "
            "WHERE id IN (SELECT DISTINCT item_id FROM media_files WHERE drive_id = ?)",
            (drive_id,),
        )
    return new_items


# ---- reading --------------------------------------------------------------

def _split(value: str | None) -> list[str]:
    return [x for x in dict.fromkeys(p.strip() for p in (value or "").split(",")) if x]


def _genres(raw: str | None) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(g) for g in data][:6] if isinstance(data, list) else []


def _item_dict(r: dict[str, Any], progress: dict[str, Any] | None = None) -> dict[str, Any]:
    qualities = _split(r.get("qualities"))
    langs = set(_split(r.get("langs_all")))
    online = r["kind"] == LINK or int(r.get("files_online") or 0) > 0
    item = {
        "id": r["id"],
        "kind": r["kind"],
        "title": r["title"],
        "year": r["year"],
        "poster": f"/api/library/art/{r['id']}" if r.get("art") and online else (r.get("poster") or ""),
        "backdrop": r.get("backdrop") or "",
        "overview": r.get("overview") or "",
        "genres": _genres(r.get("genres")),
        "rating": float(r.get("rating") or 0),
        "runtime": int(r.get("runtime") or 0),
        "url": (r.get("url") or "") if r["kind"] == LINK else "",
        "added": int(r.get("last_added") or r.get("added") or 0),
        "files": int(r.get("files") or 0),
        "online": online,
        "seasons": int(r.get("seasons") or 0),
        "quality": max(qualities, key=lambda q: QUALITY_RANK.get(q, 0), default=""),
        "langs": [code for code in LANG_ORDER if code in langs],
        "drives": _split(r.get("drive_labels")),
        "duration": float(r.get("duration") or 0),
        "resume": None,
    }
    if progress:
        duration = float(progress.get("duration") or 0)
        position = float(progress.get("position") or 0)
        item["resume"] = {
            "file_id": progress["file_id"],
            "position": position,
            "duration": duration,
            "finished": bool(progress.get("finished")),
            "season": int(progress.get("season") or 0),
            "episode": int(progress.get("episode") or 0),
            "progress": min(1.0, position / duration) if duration else 0.0,
        }
    return item


def _item_row(item_id: str) -> dict[str, Any] | None:
    return db.query_one(_ITEM_SELECT + " WHERE i.id = ? GROUP BY i.id", (item_id,))


def _latest_progress(item_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for chunk in db.chunked(item_ids):
        marks = ",".join("?" * len(chunk))
        rows = db.query(
            f"SELECT p.*, f.season, f.episode FROM media_progress p LEFT JOIN media_files f ON f.id = p.file_id "
            f"WHERE p.item_id IN ({marks}) ORDER BY p.updated DESC",
            tuple(chunk),
        )
        for row in rows:
            out.setdefault(row["item_id"], row)
    return out


def items(kind: str | None = None, q: str | None = None, drive_id: str | None = None, sort: str = "added",
          limit: int = 60, offset: int = 0, online_only: bool = True) -> tuple[list[dict[str, Any]], int]:
    """Two steps on purpose.

    Finding which titles belong on the page needs no aggregation at all - it is a filter and a sort over
    media_items, both indexed. Only once the page is known (sixty rows at most) is the expensive part
    run: the DISTINCT roll-ups of qualities, languages, drives and seasons. The old single query paid
    that cost for the whole collection, twice, on every request.
    """
    where: list[str] = []
    params: list[Any] = []
    if kind in (FILM, SERIES, LINK):
        where.append("i.kind = ?")
        params.append(kind)
    needle = fold(q or "")
    if needle:
        where.append("i.title_fold LIKE ?")
        params.append(f"%{needle}%")
    if drive_id:
        where.append("EXISTS (SELECT 1 FROM media_files f2 WHERE f2.item_id = i.id AND f2.drive_id = ?)")
        params.append(drive_id)
    where.append(_present(online_only))
    clause = " WHERE " + " AND ".join(where)

    total = (db.query_one(f"SELECT COUNT(*) AS n FROM media_items i{clause}", tuple(params)) or {"n": 0})["n"]
    page = db.query(
        f"SELECT i.id FROM media_items i{clause} ORDER BY {SORTS.get(sort, SORTS['added'])} LIMIT ? OFFSET ?",
        (*params, max(1, min(limit, 500)), max(0, offset)),
    )
    return hydrate([r["id"] for r in page], online_only), int(total)


def hydrate(item_ids: Sequence[str], online_only: bool = True,
            progress: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Full detail for a known, short list of ids, in the order given.

    `progress` lets a caller supply its own resume marks - continue watching needs the next episode,
    which is not what the progress table stores.
    """
    ids = list(item_ids)
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = db.query(f"{_ITEM_SELECT} WHERE i.id IN ({marks}) GROUP BY i.id", tuple(ids))
    found = progress if progress is not None else _latest_progress(ids)
    by_id = {r["id"]: _item_dict(r, found.get(r["id"])) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def continue_watching(limit: int = 20, online_only: bool = True) -> list[dict[str, Any]]:
    """One row per title, most recent first.

    The progress table is read once and reduced to the newest mark per title before anything else
    happens, so the work is bounded by `limit` rather than by how much has ever been watched. The old
    version walked up to 300 marks and ran two queries inside the loop for each of them.
    """
    rows = db.query(
        "SELECT p.*, f.season, f.episode FROM media_progress p LEFT JOIN media_files f ON f.id = p.file_id "
        "ORDER BY p.updated DESC LIMIT 300"
    )
    newest: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in rows:
        if p["item_id"] in seen:
            continue
        seen.add(p["item_id"])
        newest.append(p)

    marks: list[dict[str, Any]] = []
    for p in newest:
        if not p["finished"] and p["position"] >= RESUME_MIN_SECONDS:
            marks.append(p)
        elif p["finished"] and p["episode"]:
            nxt = next_episode(p["file_id"], online_only)
            if nxt:
                marks.append({**p, "file_id": nxt["id"], "position": 0.0, "duration": nxt["duration"],
                              "finished": 0, "season": nxt["season"], "episode": nxt["episode"]})
        if len(marks) >= limit:
            break  # the rest would be thrown away, so it is never fetched

    by_item = {m["item_id"]: m for m in marks}
    found = {item["id"]: item for item in hydrate(list(by_item), online_only, by_item)}
    out = [found[m["item_id"]] for m in marks if m["item_id"] in found]
    if online_only:
        out = [item for item in out if item["online"]]
    return out[:limit]


def counts(online_only: bool = True) -> dict[str, int]:
    present = _present(online_only)
    rows = db.query(f"SELECT i.kind, COUNT(*) AS n FROM media_items i WHERE {present} GROUP BY i.kind")
    by_kind = {r["kind"]: int(r["n"]) for r in rows}
    episodes = db.query_one(
        "SELECT COUNT(*) AS n FROM media_files f JOIN media_items i ON i.id = f.item_id JOIN drives d ON d.id = f.drive_id "
        "WHERE i.kind = 'series'" + (" AND d.available = 1" if online_only else "")
    ) or {"n": 0}
    return {"films": by_kind.get(FILM, 0), "series": by_kind.get(SERIES, 0), "links": by_kind.get(LINK, 0), "episodes": int(episodes["n"])}


def home(online_only: bool = True) -> dict[str, Any]:
    recent, total = items(None, limit=24, online_only=online_only)
    films, n_films = items(FILM, limit=30, online_only=online_only)
    series, n_series = items(SERIES, limit=30, online_only=online_only)
    links, n_links = items(LINK, limit=30, online_only=online_only)
    resume = continue_watching(20, online_only)
    rows: list[dict[str, Any]] = []
    if resume:
        rows.append({"key": "continue", "title": "Reprendre", "items": resume})
    if recent and total > 6:
        rows.append({"key": "recent", "title": "Ajoutés récemment", "items": recent})
    if films:
        rows.append({"key": "films", "title": "Films", "count": n_films, "items": films})
    if series:
        rows.append({"key": "series", "title": "Séries", "count": n_series, "items": series})
    if links:
        rows.append({"key": "links", "title": "Mes liens", "count": n_links, "items": links})
    hero = next((i for i in [*resume, *recent] if i["backdrop"] or i["poster"]), None) or (recent[0] if recent else None)
    return {"total": total, "counts": counts(online_only), "rows": rows, "hero": hero, "drives": drives()}


def _file_dict(f: dict[str, Any], p: dict[str, Any] | None = None) -> dict[str, Any]:
    rel = PurePosixPath(f["rel_path"])
    return {
        "id": f["id"],
        "name": rel.name,
        "folder": "" if str(rel.parent) == "." else str(rel.parent),
        "drive_id": f["drive_id"],
        "drive": f.get("drive_label") or "",
        "online": bool(f.get("drive_online")),
        "size": int(f["size"]),
        "season": int(f["season"]),
        "episode": int(f["episode"]),
        "title": f["episode_title"],
        "quality": f["quality"],
        "langs": _split(f["langs"]),
        "duration": float((p or {}).get("duration") or f["duration"] or 0),
        "codec": f["video_codec"],
        "height": int(f["height"] or 0),
        "position": float((p or {}).get("position") or 0),
        "finished": bool((p or {}).get("finished")),
    }


def _rank(f: dict[str, Any]) -> tuple[bool, int, int]:
    return (f["online"], QUALITY_RANK.get(f["quality"], 0), f["size"])


def item_detail(item_id: str) -> dict[str, Any] | None:
    row = _item_row(item_id)
    if not row:
        return None
    progress = {p["file_id"]: p for p in db.query("SELECT * FROM media_progress WHERE item_id = ?", (item_id,))}
    latest = max(progress.values(), key=lambda p: p["updated"], default=None)
    item = _item_dict(row, latest)
    files = [
        _file_dict(f, progress.get(f["id"]))
        for f in db.query(
            "SELECT f.*, d.label AS drive_label, d.available AS drive_online FROM media_files f "
            "JOIN drives d ON d.id = f.drive_id WHERE f.item_id = ? ORDER BY f.season, f.episode",
            (item_id,),
        )
    ]
    if item["kind"] == SERIES:
        grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for f in files:
            grouped.setdefault((f["season"], f["episode"]), []).append(f)
        seasons: dict[int, list[dict[str, Any]]] = {}
        for (season, _), versions in sorted(grouped.items()):
            best = max(versions, key=_rank)
            seasons.setdefault(season, []).append({**best, "versions": len(versions)})
        item["seasons_list"] = [{"season": s, "episodes": eps} for s, eps in sorted(seasons.items())]
    else:
        item["versions"] = sorted(files, key=_rank, reverse=True)
    item["play"] = default_file(item_id)
    return item


def default_file(item_id: str) -> dict[str, Any] | None:
    """What 'Lire' starts: the file in progress, else the episode after the last one watched, else the first."""
    item = db.query_one("SELECT kind FROM media_items WHERE id = ?", (item_id,))
    if not item:
        return None
    if item["kind"] == LINK:
        p = db.query_one("SELECT * FROM media_progress WHERE file_id = ?", ("link:" + item_id,))
        resume = bool(p and not p["finished"] and p["position"] >= RESUME_MIN_SECONDS)
        return {"file_id": "link:" + item_id, "position": float(p["position"]) if resume and p else 0.0, "season": 0, "episode": 0, "resume": resume}
    files = db.query(
        "SELECT f.*, d.available AS drive_online FROM media_files f JOIN drives d ON d.id = f.drive_id "
        "WHERE f.item_id = ? AND d.available = 1 ORDER BY f.season, f.episode",
        (item_id,),
    )
    if not files:
        return None
    by_id = {f["id"]: f for f in files}
    for p in db.query("SELECT * FROM media_progress WHERE item_id = ? ORDER BY updated DESC", (item_id,)):
        f = by_id.get(p["file_id"])
        if f is None:
            continue
        if not p["finished"] and p["position"] >= RESUME_MIN_SECONDS:
            return {"file_id": f["id"], "position": float(p["position"]), "season": f["season"], "episode": f["episode"], "resume": True}
        if p["finished"] and f["episode"]:
            nxt = next_episode(f["id"])
            if nxt:
                return {"file_id": nxt["id"], "position": 0.0, "season": nxt["season"], "episode": nxt["episode"], "resume": False}
        break
    if files[0]["episode"]:
        first = next((f for f in files if f["season"] >= 1), files[0])
    else:
        first = max(files, key=lambda f: (QUALITY_RANK.get(f["quality"], 0), f["size"]))
    return {"file_id": first["id"], "position": 0.0, "season": first["season"], "episode": first["episode"], "resume": False}


def next_episode(file_id: str, online_only: bool = True) -> dict[str, Any] | None:
    cur = db.query_one("SELECT item_id, season, episode FROM media_files WHERE id = ?", (file_id,))
    if not cur or not cur["episode"]:
        return None
    rows = db.query(
        "SELECT f.*, d.available AS drive_online FROM media_files f JOIN drives d ON d.id = f.drive_id "
        "WHERE f.item_id = ? AND (f.season > ? OR (f.season = ? AND f.episode > ?)) ORDER BY f.season, f.episode",
        (cur["item_id"], cur["season"], cur["season"], cur["episode"]),
    )
    if online_only:
        rows = [r for r in rows if r["drive_online"]]
    if not rows:
        return None
    first = (rows[0]["season"], rows[0]["episode"])
    return max((r for r in rows if (r["season"], r["episode"]) == first), key=lambda r: (QUALITY_RANK.get(r["quality"], 0), r["size"]))


def playable_file(file_id: str) -> dict[str, Any] | None:
    row = db.query_one(
        "SELECT f.*, d.available AS drive_online, d.mountpoint, d.label AS drive_label, i.title, i.kind AS item_kind, "
        "i.poster, i.backdrop, i.art FROM media_files f JOIN drives d ON d.id = f.drive_id "
        "JOIN media_items i ON i.id = f.item_id WHERE f.id = ?",
        (file_id,),
    )
    if not row:
        return None
    path = Path(row["mountpoint"]) / PurePosixPath(row["rel_path"])
    label = row["title"]
    if row["episode"]:
        label = f"{row['title']} · S{row['season']:02d}E{row['episode']:02d}"
        if row["episode_title"]:
            label += f" · {row['episode_title']}"
    return {
        **row,
        "path": path,
        "online": bool(row["drive_online"]) and path.is_file(),
        "display_name": label,
        "poster_url": f"/api/library/art/{row['item_id']}" if row["art"] else (row["poster"] or ""),
    }


def get_link(item_id: str) -> dict[str, Any] | None:
    return db.query_one("SELECT * FROM media_items WHERE id = ? AND kind = 'link'", (item_id,))


def save_progress(file_id: str, position: float, duration: float) -> dict[str, Any] | None:
    if file_id.startswith("link:"):
        item_id = file_id[5:]
        if not get_link(item_id):
            return None
    else:
        row = db.query_one("SELECT item_id, duration FROM media_files WHERE id = ?", (file_id,))
        if not row:
            return None
        item_id = row["item_id"]
        if duration > 0 and not row["duration"]:
            db.execute("UPDATE media_files SET duration = ? WHERE id = ?", (duration, file_id))
    position, duration = max(0.0, float(position)), max(0.0, float(duration))
    if duration <= 0:
        known = db.query_one("SELECT duration FROM media_progress WHERE file_id = ?", (file_id,))
        duration = float(known["duration"]) if known else 0.0
    finished = int(duration > 0 and position >= duration * FINISHED_RATIO)
    db.execute(
        "INSERT INTO media_progress(file_id, item_id, position, duration, finished, updated) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(file_id) DO UPDATE SET position=excluded.position, "
        "duration=CASE WHEN excluded.duration > 0 THEN excluded.duration ELSE media_progress.duration END, "
        "finished=excluded.finished, updated=excluded.updated",
        (file_id, item_id, position, duration, finished, int(time.time())),
    )
    return {"file_id": file_id, "item_id": item_id, "position": position, "duration": duration, "finished": bool(finished)}


def mark_finished(file_id: str) -> None:
    known = db.query_one("SELECT duration FROM media_progress WHERE file_id = ?", (file_id,))
    row = db.query_one("SELECT duration FROM media_files WHERE id = ?", (file_id,))
    duration = float((known or {}).get("duration") or (row or {}).get("duration") or 1.0)
    save_progress(file_id, duration, duration)


# ---- links ----------------------------------------------------------------

def add_link(url: str, title: str = "") -> dict[str, Any]:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c.isspace() for c in url):
        raise ValueError("Adresse invalide : elle doit commencer par http:// ou https://")
    raw = title.strip() or PurePosixPath(unquote(parsed.path)).stem or parsed.netloc
    name, year = mediaparse.film_title(raw)
    name = (name or raw)[:200]
    item_id = "l" + _digest(LINK, url)[:15]
    now = int(time.time())
    db.execute(
        "INSERT INTO media_items(id, kind, title, title_fold, year, url, added, updated) VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, title_fold=excluded.title_fold, year=excluded.year, "
        "updated=excluded.updated, meta_state=''",
        (item_id, LINK, name, fold(name), year, url, now, now),
    )
    return item_detail(item_id) or {}


def remove_link(item_id: str) -> bool:
    removed = db.execute("DELETE FROM media_items WHERE id = ? AND kind = 'link'", (item_id,))
    if removed:
        db.execute("DELETE FROM media_progress WHERE item_id = ?", (item_id,))
    return removed > 0


# ---- metadata and file facts ----------------------------------------------

def pending_metadata(limit: int = 6) -> list[dict[str, Any]]:
    return db.query("SELECT id, kind, title, year FROM media_items WHERE meta_state = '' ORDER BY added DESC LIMIT ?", (limit,))


def save_metadata(item_id: str, meta: dict[str, Any]) -> None:
    now = int(time.time())
    if not meta or not (meta.get("poster") or meta.get("overview")):
        db.execute("UPDATE media_items SET meta_state = 'none', updated = ? WHERE id = ?", (now, item_id))
        return
    genres = meta.get("genres") or []
    if isinstance(genres, str):
        genres = [g.strip() for g in genres.split(",") if g.strip()]
    try:
        rating = float(meta.get("rating") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    try:
        runtime = int(meta.get("runtime") or 0)
    except (TypeError, ValueError):
        runtime = 0
    db.execute(
        "UPDATE media_items SET poster = ?, backdrop = ?, overview = ?, genres = ?, rating = ?, runtime = ?, "
        "year = CASE WHEN year = '' THEN ? ELSE year END, meta_state = 'done', updated = ? WHERE id = ?",
        (str(meta.get("poster") or "")[:1000], str(meta.get("backdrop") or "")[:1000], str(meta.get("overview") or "")[:4000],
         json.dumps([str(g) for g in list(genres)[:6]], ensure_ascii=False), rating, runtime,
         str(meta.get("year") or "")[:4], now, item_id),
    )


def art_path(item_id: str) -> Path | None:
    row = db.query_one("SELECT art FROM media_items WHERE id = ?", (item_id,))
    if not row or "|" not in (row["art"] or ""):
        return None
    drive_id, rel = row["art"].split("|", 1)
    drive = db.query_one("SELECT mountpoint, available FROM drives WHERE id = ?", (drive_id,))
    if not drive or not drive["available"]:
        return None
    path = Path(drive["mountpoint"]) / PurePosixPath(rel)
    return path if path.is_file() else None


def files_to_probe(limit: int = 4) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT f.id, f.rel_path, d.mountpoint FROM media_files f JOIN drives d ON d.id = f.drive_id "
        "WHERE f.probed = 0 AND d.available = 1 ORDER BY f.added DESC LIMIT ?",
        (limit,),
    )
    return [{**r, "path": Path(r["mountpoint"]) / PurePosixPath(r["rel_path"])} for r in rows]


def save_probe(file_id: str, duration: float, codec: str, height: int) -> None:
    db.execute(
        "UPDATE media_files SET duration = CASE WHEN ? > 0 THEN ? ELSE duration END, video_codec = ?, height = ?, probed = 1 WHERE id = ?",
        (duration, duration, codec[:32], int(height), file_id),
    )

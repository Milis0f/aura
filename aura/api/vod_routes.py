"""REST: movies & series — home rows, paged browsing, rich details, poster back-fill."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import db
from ..services import catalog, metadata, xtream
from .state import AppState, state_of

router = APIRouter(prefix="/api/vod")

KIND = Query("vod", pattern="^(vod|series)$")


class PosterIn(BaseModel):
    ids: list[str]


def _media(kind: str) -> str:
    return "tv" if kind == "series" else "movie"


def _resume_map() -> dict[str, dict[str, Any]]:
    return {h["channel_id"]: h for h in catalog.history(60) if h["kind"] != "live" and h.get("duration")}


def _with_resume(items: list[dict[str, Any]], resume: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in items:
        h = resume.get(c["id"])
        out.append({**c, "resume": {"position": h["position"], "duration": h["duration"]} if h else None})
    return out


@router.get("/home")
def home(kind: str = KIND) -> dict[str, Any]:
    total = catalog.count_channels(kind)
    groups = catalog.groups(kind)
    resume = _resume_map()
    rows: list[dict[str, Any]] = []
    continue_items = [
        {"id": h["channel_id"], "name": h["name"], "url": h["url"], "kind": h["kind"], "logo": h["logo"], "group": h["group"], "extra": h["extra"], "resume": {"position": h["position"], "duration": h["duration"]}}
        for h in catalog.history(30)
        if h["kind"] == kind and h.get("duration") and 0 < h["position"] < h["duration"] * 0.95
    ]
    if continue_items:
        rows.append({"key": "continue", "title": "Reprendre", "items": continue_items[:20]})
    recent = catalog.channels(kind, limit=24, sort="added")
    if recent and any(c["extra"].get("added") for c in recent[:5]):
        rows.append({"key": "recent", "title": "Ajouts récents", "items": _with_resume(recent, resume)})
    top = [c for c in catalog.channels(kind, limit=40, sort="rating") if _rating(c) >= 6.5][:24]
    if top:
        rows.append({"key": "top", "title": "Les mieux notés", "items": _with_resume(top, resume)})
    for g in groups[:12]:
        items = catalog.channels(kind, group=g["name"], limit=24, sort="added" if kind == "vod" else "default")
        if items:
            rows.append({"key": "group:" + g["name"], "title": g["name"] or "Autres", "group": g["name"], "count": g["count"], "items": _with_resume(items, resume)})
    pool = top or recent or (rows[-1]["items"] if rows else [])
    hero = random.Random(int(time.time() // 3600)).choice(pool) if pool else None
    return {"kind": kind, "total": total, "groups": groups, "hero": hero, "rows": rows}


def _rating(c: dict[str, Any]) -> float:
    try:
        return float(c["extra"].get("rating") or 0)
    except (TypeError, ValueError):
        return 0.0


@router.get("/browse")
def browse(
    kind: str = KIND,
    group: str | None = None,
    q: str | None = None,
    sort: str = Query("default", pattern="^(default|added|rating|year|name|popular)$"),
    limit: int = Query(60, le=200),
    offset: int = 0,
) -> dict[str, Any]:
    items = catalog.channels(kind, group, q, limit, offset, sort=sort)
    return {
        "items": _with_resume(items, _resume_map()),
        "total": catalog.count_channels(kind, group, q),
        "offset": offset,
        "limit": limit,
        "groups": catalog.groups(kind),
    }


@router.get("/{channel_id}")
async def details(channel_id: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    ch = catalog.get_channel(channel_id)
    if not ch or ch["kind"] == "live":
        raise HTTPException(404, "inconnu")
    kind = ch["kind"]
    provider: dict[str, Any] = {}
    episodes: list[dict[str, Any]] = []
    src = catalog.get_source(ch["source_id"])
    if src and src.type == "xtream" and ch["extra"].get("xtream_id") is not None:
        creds = xtream.XtreamCreds.from_any(src.url, src.username, src.password)
        client = xtream.XtreamClient(creds, st.http)
        cache_key = f"xinfo:{channel_id}"
        cached = db.cache_get(cache_key, 12 * 3600)
        if cached is None:
            try:
                if kind == "series":
                    info = await client.series_info(ch["extra"]["xtream_id"])
                    cached = {"meta": xtream.info_to_meta(info.get("info") or {}), "episodes": xtream.episodes_from_info(creds, info)}
                else:
                    info = await client.vod_info(ch["extra"]["xtream_id"])
                    cached = {"meta": xtream.info_to_meta(info.get("info") or {}), "episodes": []}
            except Exception:  # noqa: BLE001 - provider hiccup: fall through to generic metadata
                cached = {"meta": {}, "episodes": []}
            db.cache_set(cache_key, cached)
        provider = cached["meta"]
        episodes = cached["episodes"]
    meta = await metadata.details(st.http, ch["name"], _media(kind), ch["extra"].get("tmdb_id") or provider.get("tmdb_id"))
    merged = _merge_meta(ch, provider, meta)
    similar = [c for c in catalog.channels(kind, group=ch["group"], limit=25, sort="rating") if c["id"] != channel_id][:18]
    hist = next((h for h in catalog.history(60) if h["channel_id"] == channel_id), None)
    return {
        "item": ch,
        "meta": merged,
        "episodes": episodes,
        "similar": similar,
        "resume": {"position": hist["position"], "duration": hist["duration"]} if hist and hist.get("duration") else None,
        "favorite": channel_id in catalog.favorite_ids(),
    }


def _merge_meta(ch: dict[str, Any], provider: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Provider (Xtream info) wins for text the provider wrote, external metadata fills the gaps."""
    ex = ch["extra"]
    first = lambda *vals: next((v for v in vals if v), "")  # noqa: E731
    genres = provider.get("genres") or meta.get("genres") or []
    return {
        "title": first(meta.get("title") if meta.get("source") == "tmdb" else "", provider.get("title"), meta.get("title"), ch["name"]),
        "year": first(ex.get("year"), provider.get("year"), meta.get("year")),
        "poster": first(meta.get("poster") if meta.get("source") == "tmdb" else "", provider.get("poster"), ch["logo"], meta.get("poster")),
        "backdrop": first(meta.get("backdrop"), provider.get("backdrop"), provider.get("poster"), ch["logo"]),
        "overview": first(provider.get("overview"), meta.get("overview"), ex.get("plot")),
        "rating": provider.get("rating") or meta.get("rating") or ex.get("rating"),
        "genres": genres if isinstance(genres, list) else [g.strip() for g in str(genres).split(",") if g.strip()],
        "runtime": provider.get("runtime") or meta.get("runtime") or ex.get("runtime") or 0,
        "trailer": first(provider.get("trailer"), meta.get("trailer")),
        "cast": provider.get("cast") or meta.get("cast") or [],
        "director": first(provider.get("director"), ex.get("director")),
        "source": meta.get("source") or ("xtream" if provider else ""),
    }


@router.post("/posters")
async def posters(body: PosterIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    """Back-fill posters for catalogue items without a logo (max 24 per call, results cached)."""
    out: dict[str, str] = {}
    ids = body.ids[:24]
    chans = [c for c in (catalog.get_channel(i) for i in ids) if c and c["kind"] != "live"]

    async def one(c: dict[str, Any]) -> None:
        m = await metadata.lookup(st.http, c["name"], _media(c["kind"]), c["extra"].get("tmdb_id"))
        if m.get("poster"):
            out[c["id"]] = m["poster"]

    await asyncio.gather(*(one(c) for c in chans))
    return {"posters": out}

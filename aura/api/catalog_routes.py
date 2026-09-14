"""REST: sources, EPG, channels, search, favorites, history, my list, TMDB, sports, home."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from .. import db
from ..config import FREE_BUNDLES, FREE_EPG, SETTINGS, WEB_APPS
from ..services import catalog, epg, library, sports, tmdb, xtream
from ..services.sports import matcher
from .state import AppState, state_of

router = APIRouter(prefix="/api")


class SourceIn(BaseModel):
    type: str = Field(pattern="^(m3u_url|xtream|free)$")
    name: str = ""
    url: str = ""
    username: str = ""
    password: str = ""
    epg_url: str = ""
    bundle: str = ""


class SourcePatch(BaseModel):
    name: str | None = None
    url: str | None = None
    username: str | None = None
    password: str | None = None
    epg_url: str | None = None
    enabled: bool | None = None


class EpgIn(BaseModel):
    name: str = ""
    url: str = ""
    key: str = ""


class MyListIn(BaseModel):
    title: str
    url: str
    kind: str = "vod"
    tmdb_id: int = 0
    poster: str = ""
    year: str = ""


# ---- sources --------------------------------------------------------------

@router.get("/sources")
def list_sources() -> dict[str, Any]:
    return {
        "sources": [s.to_public_dict() for s in catalog.list_sources()],
        "bundles": list(FREE_BUNDLES),
        "counts": catalog.counts(),
    }


@router.post("/sources")
async def add_source(body: SourceIn, request: Request, st: AppState = Depends(state_of)) -> dict[str, Any]:
    if body.type == "free":
        src = catalog.add_free_bundle(body.bundle)
        if not src:
            raise HTTPException(400, "bundle inconnu")
    elif body.type == "xtream":
        if not (body.url and body.username and body.password):
            raise HTTPException(400, "URL, identifiant et mot de passe requis")
        creds = xtream.XtreamCreds.from_any(body.url, body.username, body.password)
        try:
            await xtream.XtreamClient(creds, st.http).auth()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"Connexion Xtream impossible : {exc}") from exc
        src = catalog.add_source("xtream", body.name or creds.base_url, creds.base_url, creds.username, creds.password, body.epg_url)
    else:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(400, "URL M3U invalide")
        src = catalog.add_source("m3u_url", body.name or body.url, body.url, epg_url=body.epg_url)
    refreshed = await catalog.refresh_source(st.http, src)
    asyncio.create_task(_after_source_change(st))
    return refreshed.to_public_dict()


@router.post("/sources/upload")
async def upload_source(request: Request, file: UploadFile = File(...), st: AppState = Depends(state_of)) -> dict[str, Any]:
    data = await file.read()
    if len(data) > 64 * 1024 * 1024:
        raise HTTPException(413, "fichier trop gros")
    dest = Path(SETTINGS.uploads_dir) / f"{int(time.time())}-{Path(file.filename or 'playlist.m3u').name}"
    dest.write_bytes(data)
    src = catalog.add_source("m3u_file", file.filename or dest.name, str(dest))
    refreshed = await catalog.refresh_source(st.http, src)
    asyncio.create_task(_after_source_change(st))
    return refreshed.to_public_dict()


@router.patch("/sources/{source_id}")
def patch_source(source_id: str, body: SourcePatch) -> dict[str, Any]:
    src = catalog.update_source(source_id, **body.model_dump())
    if not src:
        raise HTTPException(404, "source inconnue")
    return src.to_public_dict()


@router.delete("/sources/{source_id}")
def delete_source(source_id: str) -> dict[str, bool]:
    catalog.delete_source(source_id)
    return {"ok": True}


@router.post("/sources/{source_id}/refresh")
async def refresh_source(source_id: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    src = catalog.get_source(source_id)
    if not src:
        raise HTTPException(404, "source inconnue")
    return (await catalog.refresh_source(st.http, src)).to_public_dict()


@router.post("/refresh")
async def refresh_everything(st: AppState = Depends(state_of)) -> dict[str, Any]:
    if st.refresh_lock.locked():
        return {"ok": False, "message": "rafraîchissement déjà en cours"}
    async with st.refresh_lock:
        srcs = await catalog.refresh_all_sources(st.http)
        epgs = await catalog.refresh_epg(st.http)
        evs = await sports.refresh(st.http)
    await st.hub.broadcast({"type": "catalog_changed"})
    return {"ok": True, "sources": [s.to_public_dict() for s in srcs], "epg": epgs, "sports": len(evs)}


async def _after_source_change(st: AppState) -> None:
    async with st.refresh_lock:
        await catalog.refresh_epg(st.http)
    await st.hub.broadcast({"type": "catalog_changed"})


# ---- EPG ------------------------------------------------------------------

@router.get("/epg/sources")
def epg_sources() -> dict[str, Any]:
    return {"sources": catalog.list_epg_sources(), "free": list(FREE_EPG), "last_refresh": db.get_setting("epg_last_refresh", "0")}


@router.post("/epg/sources")
async def add_epg(body: EpgIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    if body.key:
        preset = next((e for e in FREE_EPG if e["key"] == body.key), None)
        if not preset:
            raise HTTPException(400, "EPG inconnu")
        row = catalog.add_epg_source(preset["name"], preset["url"])
    else:
        if not body.url.startswith(("http://", "https://")):
            raise HTTPException(400, "URL XMLTV invalide")
        row = catalog.add_epg_source(body.name or body.url, body.url)
    asyncio.create_task(_after_source_change(st))
    return row


@router.delete("/epg/sources/{eid}")
def delete_epg(eid: str) -> dict[str, bool]:
    catalog.delete_epg_source(eid)
    return {"ok": True}


@router.post("/epg/refresh")
async def refresh_epg(st: AppState = Depends(state_of)) -> dict[str, Any]:
    async with st.refresh_lock:
        return {"results": await catalog.refresh_epg(st.http)}


@router.get("/epg/{channel_id}")
def epg_for_channel(channel_id: str, hours: int = 24) -> dict[str, Any]:
    ch = catalog.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "chaîne inconnue")
    epg_id = epg.resolve_epg_id(ch["tvg_id"], ch["name"])
    now = int(time.time())
    return {
        "channel": ch,
        "epg_id": epg_id,
        "programmes": epg.programmes_between(epg_id, now - 3600, now + hours * 3600) if epg_id else [],
    }


@router.get("/epg-grid")
def epg_grid(group: str | None = None, hours: int = 6, limit: int = 60) -> dict[str, Any]:
    now = int(time.time())
    start = now - (now % 1800)
    chs = catalog.channels("live", group=group, limit=limit)
    rows = []
    for c in chs:
        epg_id = epg.resolve_epg_id(c["tvg_id"], c["name"])
        rows.append({**c, "epg_id": epg_id, "programmes": epg.programmes_between(epg_id, start, start + hours * 3600) if epg_id else []})
    return {"start": start, "hours": hours, "channels": rows}


# ---- channels -------------------------------------------------------------

@router.get("/channels")
def channels(
    kind: str = Query("live", pattern="^(live|vod|series)$"),
    group: str | None = None,
    q: str | None = None,
    limit: int = Query(300, le=2000),
    offset: int = 0,
    with_epg: bool = True,
) -> dict[str, Any]:
    chs = catalog.channels(kind, group, q, limit, offset)
    if kind == "live" and with_epg:
        chs = catalog.with_epg(chs)
    favs = catalog.favorite_ids()
    return {"items": [{**c, "favorite": c["id"] in favs} for c in chs], "groups": catalog.groups(kind)}


@router.get("/channels/{channel_id}")
def channel(channel_id: str) -> dict[str, Any]:
    ch = catalog.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "chaîne inconnue")
    enriched = catalog.with_epg([ch])[0] if ch["kind"] == "live" else ch
    return {**enriched, "favorite": channel_id in catalog.favorite_ids(), "alternatives": catalog.alternatives(ch)}


@router.get("/series/{channel_id}/episodes")
async def series_episodes(channel_id: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    ch = catalog.get_channel(channel_id)
    if not ch or ch["kind"] != "series":
        raise HTTPException(404, "série inconnue")
    if not ch["url"].startswith("xtream-series://"):
        return {"series": ch, "episodes": [{"season": 1, "episode": 1, "title": ch["name"], "url": ch["url"]}]}
    src = catalog.get_source(ch["source_id"])
    if not src:
        raise HTTPException(404, "source inconnue")
    creds = xtream.XtreamCreds.from_any(src.url, src.username, src.password)
    cache_key = f"series:{channel_id}"
    cached = db.cache_get(cache_key, 6 * 3600)
    if cached is None:
        info = await xtream.XtreamClient(creds, st.http).series_info(ch["extra"].get("xtream_id"))
        cached = {"info": info.get("info") or {}, "episodes": xtream.episodes_from_info(creds, info)}
        db.cache_set(cache_key, cached)
    return {"series": ch, **cached}


@router.get("/search")
def search(q: str = Query(min_length=1), limit: int = 40) -> dict[str, Any]:
    res = catalog.search(q, limit)
    res["live"] = catalog.with_epg(res["live"])
    evs = [e.to_dict() for e in sports.upcoming() if q.lower() in e.name.lower()]
    apps = [a for a in WEB_APPS if q.lower() in a["name"].lower()]
    return {**res, "sports": evs[:20], "apps": apps}


# ---- favorites / history / my list ---------------------------------------

@router.get("/favorites")
def favorites() -> dict[str, Any]:
    return {"items": catalog.with_epg(catalog.favorites())}


@router.post("/favorites/{channel_id}")
async def toggle_favorite(channel_id: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    fav = catalog.toggle_favorite(channel_id)
    await st.hub.broadcast({"type": "favorites_changed"})
    return {"favorite": fav}


@router.get("/history")
def history() -> dict[str, Any]:
    return {"items": catalog.history()}


@router.get("/mylist")
def my_list() -> dict[str, Any]:
    return {"items": catalog.my_list()}


@router.post("/mylist")
def add_my_list(body: MyListIn) -> dict[str, Any]:
    if not body.url.startswith(("http://", "https://", "rtmp://", "rtsp://")):
        raise HTTPException(400, "URL invalide")
    return catalog.add_to_my_list(body.title, body.url, body.kind, body.tmdb_id, body.poster, body.year)


@router.delete("/mylist/{mid}")
def del_my_list(mid: str) -> dict[str, bool]:
    catalog.remove_from_my_list(mid)
    return {"ok": True}


# ---- TMDB -----------------------------------------------------------------

@router.get("/tmdb/search")
async def tmdb_search(q: str, media: str = "multi", st: AppState = Depends(state_of)) -> dict[str, Any]:
    if not tmdb.api_key():
        return {"items": [], "configured": False}
    return {"items": await tmdb.search(st.http, q, media), "configured": True}


@router.get("/tmdb/{media}/{tmdb_id}")
async def tmdb_details(media: str, tmdb_id: int, st: AppState = Depends(state_of)) -> dict[str, Any]:
    if media not in ("movie", "tv"):
        raise HTTPException(400, "media invalide")
    d = await tmdb.details(st.http, media, tmdb_id)
    if not d:
        raise HTTPException(404, "introuvable")
    # streams from the catalogue that look like this title
    matches = catalog.channels("vod" if media == "movie" else "series", q=d["title"], limit=10)
    return {**d, "streams": matches}


@router.get("/vod/enrich/{channel_id}")
async def vod_enrich(channel_id: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    ch = catalog.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "inconnu")
    media = "tv" if ch["kind"] == "series" else "movie"
    return {"channel": ch, "tmdb": await tmdb.enrich(st.http, ch["name"], media, ch["extra"].get("tmdb_id"))}


# ---- sports ---------------------------------------------------------------

@router.get("/sports")
def sports_list(sport: str | None = None, days: int = 45) -> dict[str, Any]:
    evs = sports.upcoming(sport, days)
    now = int(time.time())
    return {"events": [{**e.to_dict(), "status": sports.status_of(e, now)} for e in evs], "last_refresh": db.get_setting("sports_last_refresh", "0")}


@router.get("/sports/{event_id}/streams")
def sports_streams(event_id: str) -> dict[str, Any]:
    ev = sports.find(event_id)
    if not ev:
        raise HTTPException(404, "événement inconnu")
    chs = catalog.with_epg(catalog.channels("live", limit=5000))
    return {"event": {**ev.to_dict(), "status": sports.status_of(ev)}, "streams": matcher.candidate_streams(ev, chs)}


@router.post("/sports/refresh")
async def sports_refresh(st: AppState = Depends(state_of)) -> dict[str, Any]:
    return {"events": len(await sports.refresh(st.http))}


# ---- home dashboard -------------------------------------------------------

@router.get("/home")
def home() -> dict[str, Any]:
    now = int(time.time())
    evs = sports.upcoming(days=30)
    next_f1 = next((e for e in evs if e.sport == "f1" and e.start >= now - 3600), None)
    next_ufc = next((e for e in evs if e.sport == "ufc" and e.start >= now - 3 * 3600), None)
    live_now = [e for e in evs if sports.status_of(e, now) == "live"][:6]
    favs = catalog.with_epg(catalog.favorites())[:20]
    recent_vod = catalog.channels("vod", limit=24)
    recent_series = catalog.channels("series", limit=24)
    return {
        "configured": catalog.is_configured(),
        "counts": catalog.counts(),
        "history": catalog.history(12),
        "favorites": favs,
        "live_sports": [{**e.to_dict(), "status": "live"} for e in live_now],
        "next_f1": {**next_f1.to_dict(), "status": sports.status_of(next_f1, now)} if next_f1 else None,
        "next_ufc": {**next_ufc.to_dict(), "status": sports.status_of(next_ufc, now)} if next_ufc else None,
        "movies": recent_vod,
        "series": recent_series,
        "apps": list(WEB_APPS),
        "mylist": catalog.my_list()[:12],
        "library": {
            "counts": library.counts(),
            "resume": library.continue_watching(12),
            "recent": library.items(None, limit=20)[0],
        },
    }

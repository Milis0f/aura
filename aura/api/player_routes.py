"""REST: playback control, remote input, web apps, and the stream proxy.

Playback latency is the whole point of this module, so two things happen before a stream reaches
the screen: candidate sources are probed in parallel and the fastest live one wins, and the local
proxy is skipped entirely when the origin sends CORS headers the browser can use on its own.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from .. import db
from ..config import SETTINGS, WEB_APPS
from ..services import archive_films, catalog, library, probe, resolver, system
from ..services.player import decide_backend
from .state import AppState, state_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class PlayIn(BaseModel):
    channel_id: str = ""
    url: str = ""
    name: str = ""
    kind: str = "live"
    prefer: str | None = None
    position: float = 0
    fast: bool = True  # probe candidates before playing
    ref: str = ""  # library reference ("lib:link:<id>") so progress is saved against the title


class ReportIn(BaseModel):
    position: float | None = None
    duration: float | None = None
    paused: bool | None = None
    error: str | None = None
    ended: bool = False


class KeyIn(BaseModel):
    key: str
    text: str = ""


class TextIn(BaseModel):
    text: str


class PointerIn(BaseModel):
    dx: float = 0
    dy: float = 0
    click: str = ""
    scroll: float = 0


class VolumeIn(BaseModel):
    delta: int = 0
    percent: int | None = None
    mute: bool = False


class TrailerIn(BaseModel):
    url: str


class WarmIn(BaseModel):
    url: str = ""
    channel_id: str = ""


def proxied(url: str) -> str:
    return f"/api/proxy?url={quote(url, safe='')}"


def playable(item: dict[str, Any], direct: bool = False) -> dict[str, Any]:
    """Decorate an item with what the TV page needs to play it."""
    backend = decide_backend(item["url"], item.get("extra"), item.get("kind", "live"))
    play_url = ""
    if backend == "browser":
        play_url = item["url"] if direct else proxied(item["url"])
    return {**item, "backend": backend, "play_url": play_url, "direct": direct}


# ---- playback -------------------------------------------------------------

async def _resolve_archive(st: AppState, item: dict[str, Any]) -> dict[str, Any]:
    if not item["url"].startswith("archive://"):
        return item
    try:
        url = await archive_films.resolve(st.http, item["url"].split("://", 1)[1])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"film indisponible sur Internet Archive : {exc}") from exc
    return {**item, "url": url}


def _load_item(body: PlayIn) -> dict[str, Any]:
    if body.channel_id:
        item = catalog.get_channel(body.channel_id)
        if item:
            return item
        row = next((m for m in catalog.my_list() if m["id"] == body.channel_id), None)
        if not row:
            raise HTTPException(404, "inconnu")
        return {"id": row["id"], "name": row["title"], "url": row["url"], "kind": row["kind"], "logo": row["poster"], "group": "", "extra": {}}
    if body.url:
        return {"id": "", "name": body.name or body.url, "url": body.url, "kind": body.kind, "logo": "", "group": "", "extra": {}}
    raise HTTPException(400, "channel_id ou url requis")


def _with_format(item: dict[str, Any], fmt: str) -> dict[str, Any]:
    if not fmt:
        return item
    return {**item, "extra": {**(item.get("extra") or {}), "format": fmt}}


async def _resolve_page(item: dict[str, Any]) -> dict[str, Any]:
    try:
        manifest = await resolver.resolve(item["url"])
    except Exception as exc:  # noqa: BLE001 - surfaced to the TV and the phone
        raise HTTPException(502, f"direct YouTube indisponible : {exc}") from exc
    extra = {**(item.get("extra") or {}), "format": "hls", "page_url": item["url"]}
    return {**item, "url": manifest, "extra": extra}


@router.post("/play")
async def play(body: PlayIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    item = _load_item(body)
    if item["kind"] == "series" and item["url"].startswith("xtream-series://"):
        raise HTTPException(400, "choisir un épisode")
    item = await _resolve_archive(st, item)

    alternatives = catalog.alternatives(item) if item.get("id") else []
    candidates = [item, *alternatives][:5]
    chosen, direct, tried = item, False, []
    fast = body.fast and db.get_setting("fast_start", "1") != "0"
    # Probe for both engines: mpv also wastes 10 s on a dead URL before giving up.
    if fast and item["url"].startswith(("http://", "https://")):
        streams = [c for c in candidates if not resolver.needs_resolve(c["url"])]
        results = await probe.race(st.http, [c["url"] for c in streams], budget=3.5) if streams else []
        by_url = {r.url: r for r in results}
        alive = sorted(
            (c for c in streams if c["url"] in by_url and by_url[c["url"]].ok),
            key=lambda c: by_url[c["url"]].latency_ms,
        )
        tried = [
            {"name": c["name"], "latency_ms": by_url[c["url"]].latency_ms, "ok": by_url[c["url"]].ok}
            for c in streams
            if c["url"] in by_url
        ]
        if alive:
            verdict = by_url[alive[0]["url"]]
            chosen, direct = _with_format(alive[0], verdict.format), verdict.cors
            alternatives = [c for c in [*alive[1:], *alternatives] if c["url"] != chosen["url"]]
        else:
            # No direct stream answered: a YouTube live page is still worth resolving.
            chosen = next((c for c in candidates if resolver.needs_resolve(c["url"])), item)
            if results:
                log.info("no direct stream answered for %s", item["name"])
    if resolver.needs_resolve(chosen["url"]):
        chosen = await _resolve_page(chosen)
        verdict = await probe.probe_url(st.http, chosen["url"])
        direct = verdict.ok and verdict.cors
    # The TV page cannot resolve pages itself, so they are useless as in-page failover.
    alternatives = [a for a in alternatives if not resolver.needs_resolve(a["url"])]

    if body.ref:
        chosen = {**chosen, "extra": {**(chosen.get("extra") or {}), "start": body.position}}
    state = await st.player.play(chosen, body.prefer)
    decorated = playable(chosen, direct)
    if body.ref:
        state = st.player.tag(body.ref)
        decorated = {**decorated, "id": body.ref, "extra": {**(decorated.get("extra") or {}), "library": True, "file_id": body.ref[4:]}}
    # The TV page may receive this playback twice (its own HTTP reply and the WebSocket broadcast).
    # A token lets it start the stream once instead of tearing down and reloading the manifest.
    token = uuid.uuid4().hex[:12]
    alt_payload = [playable(a, False) for a in alternatives[:5]]
    if item.get("id"):
        catalog.record_history(item["id"], item["name"], item["kind"], body.position)
    if state.backend == "browser":
        if await kiosk_away(st):
            await st.kiosk.home()
        payload = {"type": "play", "item": decorated, "position": body.position, "alternatives": alt_payload, "token": token}
        if await st.hub.to_tv(payload) == 0:
            # The TV page is loading (it was showing Netflix, or just rebooted): deliver on reconnect.
            st.pending_play = (time.monotonic(), payload)
    else:
        await st.hub.to_tv({"type": "external_player", "item": decorated})
    await st.hub.to_remotes({"type": "player", "state": state.to_dict()})
    return {"state": state.to_dict(), "item": decorated, "alternatives": alt_payload, "token": token, "probed": tried}


async def kiosk_away(st: AppState) -> bool:
    """True when the kiosk browser shows a web app instead of the Aura TV page.

    While the TV page holds its WebSocket it is on screen by definition, so Chrome is not asked.
    """
    if st.hub.tv_connected:
        return False
    url = await st.kiosk.current_url()
    return bool(url) and "/tv/" not in url


_kiosk_away = kiosk_away


async def save_library_position(st: AppState) -> None:
    """Remember where a library title stopped before the player goes away."""
    state = await st.player.poll()
    if state.item_id.startswith("lib:") and state.duration:
        library.save_progress(state.item_id[4:], state.position, state.duration)


@router.post("/stop")
async def stop(st: AppState = Depends(state_of)) -> dict[str, Any]:
    st.pending_play = None
    await save_library_position(st)
    state = await st.player.stop()
    await st.hub.to_tv({"type": "stop"})
    await st.hub.to_remotes({"type": "player", "state": state.to_dict()})
    return {"state": state.to_dict()}


@router.get("/player")
async def player_state(st: AppState = Depends(state_of)) -> dict[str, Any]:
    return {"state": (await st.player.poll()).to_dict(), "tv_connected": st.hub.tv_connected}


@router.post("/player/report")
async def report(body: ReportIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    changes = {k: v for k, v in body.model_dump().items() if v is not None and k != "ended"}
    state = st.player.report(**changes)
    if state.item_id.startswith("lib:") and body.position is not None:
        library.save_progress(state.item_id[4:], body.position, body.duration or state.duration)
    elif state.item_id and body.position is not None and state.kind != "live":
        catalog.record_history(state.item_id, state.name, state.kind, body.position, body.duration or state.duration)
    if body.ended:
        state = await st.player.stop()
    await st.hub.to_remotes({"type": "player", "state": state.to_dict()})
    return {"state": state.to_dict()}


@router.post("/player/pause")
async def pause(st: AppState = Depends(state_of)) -> dict[str, Any]:
    state = await st.player.pause_toggle()
    await st.hub.to_tv({"type": "pause_toggle"})
    return {"state": state.to_dict()}


@router.post("/player/seek/{seconds}")
async def seek(seconds: float, st: AppState = Depends(state_of)) -> dict[str, bool]:
    await st.player.seek(seconds)
    await st.hub.to_tv({"type": "seek", "seconds": seconds})
    return {"ok": True}


@router.post("/player/cycle/{what}")
async def cycle_track(what: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    """Next audio or subtitle track (mpv only: the TV page has a single track)."""
    if what not in ("audio", "sub"):
        raise HTTPException(404, "piste inconnue")
    text = await st.player.cycle(what)
    return {"ok": bool(text), "label": text}


@router.post("/player/warm")
async def warm(body: WarmIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    """Open the connection to a stream the user is hovering, so pressing OK starts instantly."""
    url = body.url
    if body.channel_id and not url:
        ch = catalog.get_channel(body.channel_id)
        url = ch["url"] if ch else ""
    if not url.startswith(("http://", "https://")):
        return {"ok": False}
    asyncio.create_task(probe.warm(st.http, url))
    return {"ok": True}


@router.get("/player/probe")
async def probe_channel(channel_id: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    ch = catalog.get_channel(channel_id)
    if not ch:
        raise HTTPException(404, "chaîne inconnue")
    candidates = [ch, *catalog.alternatives(ch)][:6]
    results = await probe.race(st.http, [c["url"] for c in candidates], use_cache=False)
    by_url = {r.url: r for r in results}
    return {
        "channel": ch["name"],
        "sources": [
            {"name": c["name"], **(by_url[c["url"]].to_dict() if c["url"] in by_url else {"ok": False, "error": "timeout", "latency_ms": 0})}
            for c in candidates
        ],
    }


# ---- remote input ---------------------------------------------------------

@router.post("/remote/key")
async def remote_key(body: KeyIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    if st.player.state.backend == "app" or await _kiosk_away(st):
        return {"ok": await st.kiosk.key(body.key, body.text), "target": "browser"}
    if st.player.state.backend == "mpv":
        return {"ok": await _mpv_key(st, body.key), "target": "mpv"}
    sent = await st.hub.to_tv({"type": "key", "key": body.key, "text": body.text})
    return {"ok": sent > 0, "target": "tv"}


async def _mpv_key(st: AppState, key: str) -> bool:
    if key in ("Escape", "Backspace", "BrowserBack"):
        await save_library_position(st)
        await st.player.stop()
        await st.hub.to_tv({"type": "stop"})
        return True
    if key in ("Enter", "Space", "MediaPlayPause"):
        await st.player.pause_toggle()
        return True
    if key in ("a", "A"):
        await st.player.cycle("audio")
        return True
    if key in ("s", "S"):
        await st.player.cycle("sub")
        return True
    if key == "ArrowRight":
        await st.player.seek(30)
    elif key == "ArrowLeft":
        await st.player.seek(-15)
    elif key == "ArrowUp":
        await st.player.volume(5)
    elif key == "ArrowDown":
        await st.player.volume(-5)
    return True


@router.post("/remote/text")
async def remote_text(body: TextIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    if st.player.state.backend == "app" or await _kiosk_away(st):
        return {"ok": await st.kiosk.type_text(body.text)}
    return {"ok": (await st.hub.to_tv({"type": "text", "text": body.text})) > 0}


@router.post("/remote/pointer")
async def remote_pointer(body: PointerIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    ok = True
    if body.dx or body.dy:
        ok = await st.kiosk.move(body.dx, body.dy)
    if body.scroll:
        ok = await st.kiosk.scroll(body.scroll) and ok
    if body.click:
        ok = await st.kiosk.click(body.click) and ok
    return {"ok": ok}


@router.post("/remote/volume")
async def remote_volume(body: VolumeIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    if body.mute:
        code, out = await system.mute_toggle()
    elif body.percent is not None:
        code, out = await system.set_volume(body.percent)
    else:
        code, out = await system.volume_step(body.delta)
    await st.hub.to_tv({"type": "volume", "delta": body.delta, "percent": body.percent, "mute": body.mute})
    return {"ok": code == 0, "out": out}


@router.post("/remote/navigate/{view}")
async def remote_navigate(view: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    if await _kiosk_away(st):
        await st.kiosk.home()
        await asyncio.sleep(1.5)
    return {"ok": (await st.hub.to_tv({"type": "navigate", "view": view})) > 0}


# ---- web apps -------------------------------------------------------------

@router.get("/apps")
async def apps(st: AppState = Depends(state_of)) -> dict[str, Any]:
    return {"apps": list(WEB_APPS), "kiosk": await st.kiosk.available(), "current": st.player.state.app}


@router.post("/apps/trailer")
async def open_trailer(body: TrailerIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    if not body.url.startswith("https://"):
        raise HTTPException(400, "url invalide")
    if not await st.kiosk.available():
        raise HTTPException(503, "bande-annonce disponible uniquement sur le boîtier")
    await st.player.set_app("trailer")
    await st.hub.to_tv({"type": "stop"})
    return {"ok": await st.kiosk.navigate(body.url)}


@router.post("/apps/{key}")
async def open_app(key: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    app = next((a for a in WEB_APPS if a["key"] == key), None)
    if not app:
        raise HTTPException(404, "app inconnue")
    if not await st.kiosk.available():
        raise HTTPException(503, "le navigateur kiosque n'est pas joignable (mode développement ?)")
    await st.player.set_app(key)
    await st.hub.to_tv({"type": "stop"})
    ok = await st.kiosk.navigate(app["url"])
    await st.kiosk.refresh_viewport()
    await st.hub.to_remotes({"type": "player", "state": st.player.state.to_dict()})
    return {"ok": ok, "app": app}


@router.post("/apps-home")
async def apps_home(st: AppState = Depends(state_of)) -> dict[str, Any]:
    await st.player.stop()
    ok = await st.kiosk.home()
    await st.hub.to_remotes({"type": "player", "state": st.player.state.to_dict()})
    return {"ok": ok}


# ---- stream proxy ---------------------------------------------------------

_HLS_URI = re.compile(r'(URI=")([^"]+)(")')
_ALLOWED_SCHEMES = ("http", "https")
_HOP = {
    "connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
    "proxy-authorization", "proxy-authenticate", "content-encoding", "content-length",
}
CHUNK = 256 * 1024


def rewrite_playlist(text: str, base_url: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif s.startswith("#"):
            out.append(_HLS_URI.sub(lambda m: m.group(1) + proxied(urljoin(base_url, m.group(2))) + m.group(3), line))
        else:
            out.append(proxied(urljoin(base_url, s)))
    return "\n".join(out) + "\n"


@router.get("/proxy")
async def proxy(url: str, request: Request, st: AppState = Depends(state_of)) -> Response:
    p = urlparse(url)
    if p.scheme not in _ALLOWED_SCHEMES or not p.netloc:
        raise HTTPException(400, "url invalide")
    headers = {"User-Agent": SETTINGS.user_agent, "Accept": "*/*"}
    if request.headers.get("range"):
        headers["Range"] = request.headers["range"]
    client = st.stream_http  # short connect timeout, large pool, keep-alive
    req = client.build_request("GET", url, headers=headers)
    try:
        upstream = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"flux injoignable : {type(exc).__name__}") from exc

    ctype = upstream.headers.get("content-type", "").lower()
    is_playlist = "mpegurl" in ctype or p.path.lower().endswith((".m3u8", ".m3u")) or "m3u8" in p.query.lower()

    if is_playlist:
        try:
            body = await upstream.aread()
        finally:
            await upstream.aclose()
        text = body.decode("utf-8", "replace")
        if not text.lstrip().startswith("#EXTM3U"):
            raise HTTPException(502, "playlist HLS invalide")
        return Response(
            rewrite_playlist(text, str(upstream.url)),
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"},
        )

    if upstream.status_code >= 400:
        await upstream.aclose()
        raise HTTPException(502, f"flux en erreur HTTP {upstream.status_code}")

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
    resp_headers["Access-Control-Allow-Origin"] = "*"
    resp_headers.setdefault("Accept-Ranges", "bytes")
    # Media segments are immutable once published; letting the browser reuse them avoids refetching
    # the same bytes when the player seeks or the level switches back.
    resp_headers["Cache-Control"] = "public, max-age=30" if p.path.lower().endswith((".ts", ".m4s", ".mp4", ".aac")) else "no-store"

    async def body_iter():
        try:
            async for chunk in upstream.aiter_raw(CHUNK):
                yield chunk
        except httpx.HTTPError as exc:
            log.debug("proxy stream ended early: %s", exc)
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=ctype or "application/octet-stream",
    )

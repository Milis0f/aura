"""REST: the local library (films and series on drives, films by link), local playback, drives, watched folders."""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import db
from ..config import IS_LINUX, SETTINGS
from ..services import accounts, files, library, storage
from . import guard
from .state import AppState, state_of

router = APIRouter(prefix="/api")
FORBIDDEN_ROOTS = ("/etc", "/proc", "/sys", "/dev", "/run", "/boot", "/root", "/usr", "/bin", "/sbin", "/lib", "/var/lib", "/opt/aura")


class PlayIn(BaseModel):
    item_id: str = ""
    file_id: str = ""
    position: float | None = None


class ProgressIn(BaseModel):
    file_id: str
    position: float
    duration: float = 0


class EndedIn(BaseModel):
    file_id: str


class LinkIn(BaseModel):
    url: str
    title: str = ""


class FolderIn(BaseModel):
    path: str
    label: str = ""


def _jobs(st: AppState):  # noqa: ANN202 - LibraryJobs, created in the lifespan
    if not st.library:
        raise HTTPException(503, "Bibliothèque en cours de démarrage.")
    return st.library


# ---- browsing -------------------------------------------------------------

@router.get("/library/home")
def library_home(all: int = 0, _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    return library.home(online_only=not all)


@router.get("/library/items")
def library_items(kind: str | None = None, q: str | None = None, drive: str | None = None,
                  sort: str = Query("added", pattern="^(added|title|year|rating)$"), limit: int = Query(60, ge=1, le=500),
                  offset: int = Query(0, ge=0), all: int = 0, _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    items, total = library.items(kind, q, drive, sort, limit, offset, online_only=not all)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/library/items/{item_id}")
def library_item(item_id: str, _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    item = library.item_detail(item_id)
    if not item:
        raise HTTPException(404, "Titre inconnu.")
    return item


@router.get("/library/art/{item_id}")
def library_art(item_id: str, _: Any = Depends(guard.home_or_account)) -> FileResponse:
    path = library.art_path(item_id)
    if not path:
        raise HTTPException(404, "Pas d'affiche.")
    return FileResponse(path, media_type=files.media_type(path), headers={"Cache-Control": "private, max-age=86400"})


# ---- playback -------------------------------------------------------------

async def play_on_tv(st: AppState, item_id: str = "", file_id: str = "", position: float | None = None) -> dict[str, Any]:
    """Start a library title on the TV: mpv straight from the disk on the appliance, the TV page elsewhere."""
    if not file_id:
        if not item_id:
            raise HTTPException(400, "item_id ou file_id requis.")
        start = library.default_file(item_id)
        if not start:
            if not library.item_detail(item_id):
                raise HTTPException(404, "Titre inconnu.")
            raise HTTPException(409, "Le disque de ce titre n'est pas branché.")
        file_id = start["file_id"]
        position = start["position"] if position is None else position
    pos = max(0.0, float(position or 0))

    if file_id.startswith("link:"):
        from . import player_routes

        link = library.get_link(file_id[5:])
        if not link:
            raise HTTPException(404, "Lien inconnu.")
        body = player_routes.PlayIn(url=link["url"], name=link["title"], kind="vod", position=pos, ref="lib:" + file_id)
        return await player_routes.play(body, st)

    f = library.playable_file(file_id)
    if not f:
        raise HTTPException(404, "Fichier inconnu.")
    if not f["online"]:
        raise HTTPException(409, f"Branche le disque « {f['drive_label']} » pour lire ce titre.")
    ref = "lib:" + file_id
    title = f["display_name"]
    if st.player.mpv.available:
        item = {"id": ref, "name": title, "url": str(f["path"]), "kind": "vod", "logo": f["poster_url"],
                "extra": {"local": True, "start": pos, "duration": f["duration"]}}
        state = await st.player.play(item, prefer="mpv")
        await st.hub.to_tv({"type": "external_player", "item": {"name": title, "logo": f["poster_url"], "backdrop": f["backdrop"], "library": True}})
        await st.hub.to_remotes({"type": "player", "state": state.to_dict()})
        return {"state": state.to_dict(), "backend": "mpv", "position": pos}

    from . import player_routes

    stream = f"/api/library/stream/{file_id}"
    item = {"id": ref, "name": title, "url": stream, "kind": "vod", "logo": f["poster_url"], "group": f["drive_label"],
            "extra": {"format": "native", "library": True, "file_id": file_id, "duration": f["duration"]}}
    state = await st.player.play(item, prefer="browser")
    decorated = {**item, "backend": "browser", "play_url": stream, "direct": True}
    token = uuid.uuid4().hex[:12]
    payload = {"type": "play", "item": decorated, "position": pos, "alternatives": [], "token": token}
    if await player_routes.kiosk_away(st):
        await st.kiosk.home()
    if await st.hub.to_tv(payload) == 0:
        st.pending_play = (time.monotonic(), payload)
    await st.hub.to_remotes({"type": "player", "state": state.to_dict()})
    return {"state": state.to_dict(), "item": decorated, "token": token, "position": pos, "backend": "browser"}


@router.post("/library/play")
async def library_play(body: PlayIn, st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    return await play_on_tv(st, body.item_id, body.file_id, body.position)


@router.get("/library/stream/{file_id}")
def library_stream(file_id: str, _: Any = Depends(guard.local_or_account)) -> FileResponse:
    f = library.playable_file(file_id)
    if not f or not f["online"]:
        raise HTTPException(404, "Fichier indisponible : le disque est-il branché ?")
    return FileResponse(f["path"], media_type=files.media_type(f["path"]), headers={"Cache-Control": "no-store"})


@router.post("/library/progress")
def library_progress(body: ProgressIn, _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    saved = library.save_progress(body.file_id, body.position, body.duration)
    if not saved:
        raise HTTPException(404, "Fichier inconnu.")
    return saved


@router.post("/library/ended")
def library_ended(body: EndedIn, _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    library.mark_finished(body.file_id)
    nxt = None if body.file_id.startswith("link:") else library.next_episode(body.file_id)
    return {
        "autoplay": db.get_setting("autoplay_next", "1") != "0",
        "next": {"file_id": nxt["id"], "season": nxt["season"], "episode": nxt["episode"], "title": nxt["episode_title"]} if nxt else None,
    }


# ---- links ----------------------------------------------------------------

@router.post("/library/links")
async def add_link(body: LinkIn, st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    try:
        item = library.add_link(body.url, body.title)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _jobs(st).wake_metadata()
    await st.hub.broadcast({"type": "library_changed"})
    return item


@router.delete("/library/links/{item_id}")
async def delete_link(item_id: str, st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, bool]:
    if not library.remove_link(item_id):
        raise HTTPException(404, "Lien inconnu.")
    await st.hub.broadcast({"type": "library_changed"})
    return {"ok": True}


# ---- drives ---------------------------------------------------------------

def _usage(path: str) -> dict[str, int]:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return {}
    return {"total": u.total, "used": u.used, "free": u.free}


@router.get("/drives")
async def list_drives(st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    jobs = _jobs(st)
    known = {d["id"]: d for d in library.drives()}
    in_library = {v.id for v in jobs.eligible()}
    out: list[dict[str, Any]] = []
    for vol in jobs.watcher.volumes:
        if vol.system:
            continue
        row = known.pop(vol.id, {})
        out.append({
            **vol.to_dict(), "available": vol.mounted, "in_library": vol.id in in_library,
            "films": int(row.get("films") or 0), "episodes": int(row.get("episodes") or 0),
            "last_scan": int(row.get("last_scan") or 0), "scan_state": row.get("scan_state") or "",
            "scanning": jobs.scanning(vol.id), "scanned_files": jobs.progress.get(vol.id), "usage": _usage(vol.mountpoint) if vol.mounted else {},
        })
    for row in known.values():
        out.append({**row, "mounted": False, "readable": True, "system": False, "in_library": False, "scanning": False, "scanned_files": None, "usage": {}})
    return {
        "drives": out,
        "automount": SETTINGS.automount and db.get_setting("automount", "1") != "0",
        "can_mount": IS_LINUX and shutil.which("udisksctl") is not None,
    }


@router.post("/library/scan")
async def scan_everything(st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, int]:
    return {"scheduled": _jobs(st).rescan_all(announce=True)}


@router.post("/drives/{drive_id}/scan")
async def scan_drive(drive_id: str, st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, bool]:
    jobs = _jobs(st)
    vol = next((v for v in jobs.eligible() if v.id == drive_id), None)
    if not vol:
        raise HTTPException(409, "Ce disque n'est pas branché ou n'est pas lisible.")
    return {"ok": True, "started": jobs.schedule_scan(vol, announce=True)}


def _data_volume(st: AppState, drive_id: str) -> storage.Volume:
    """A drive the owner may mount or eject. System partitions (EFI, root, boot) are never exposed."""
    vol = _jobs(st).volume(drive_id)
    if not vol or vol.system:
        raise HTTPException(404, "Disque introuvable.")
    return vol


@router.post("/drives/{drive_id}/mount")
async def mount_drive(drive_id: str, st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    jobs = _jobs(st)
    vol = _data_volume(st, drive_id)
    ok, detail = await storage.mount(vol)
    if not ok:
        raise HTTPException(502, f"Montage impossible : {detail}")
    jobs.watcher.poke()
    return {"ok": True, "mountpoint": detail}


@router.post("/drives/{drive_id}/eject")
async def eject_drive(drive_id: str, st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, Any]:
    jobs = _jobs(st)
    vol = _data_volume(st, drive_id)
    playing = st.player.state.item_id
    if playing.startswith("lib:v"):
        current = library.playable_file(playing[4:])
        if current and current["drive_id"] == vol.id:
            await st.player.stop()
            await st.hub.to_tv({"type": "stop"})
    ok, message = await storage.eject(vol, jobs.watcher.volumes)
    if not ok:
        raise HTTPException(409, message)
    jobs.watcher.poke()
    return {"ok": True, "message": message}


@router.delete("/drives/{drive_id}")
async def forget_drive(drive_id: str, st: AppState = Depends(state_of), _: Any = Depends(guard.home_or_account)) -> dict[str, bool]:
    if not library.forget_drive(drive_id):
        raise HTTPException(409, "Seul un disque débranché peut être oublié.")
    await st.hub.broadcast({"type": "library_changed"})
    return {"ok": True}


# ---- watched folders ------------------------------------------------------

def _folders() -> list[dict[str, str]]:
    try:
        data = json.loads(db.get_setting("library_dirs", "[]") or "[]")
    except json.JSONDecodeError:
        return []
    return [{"label": str(e.get("label") or ""), "path": str(e.get("path") or "")} for e in data if isinstance(e, dict)]


def _forbidden(path: Path) -> bool:
    text = path.as_posix()
    data = SETTINGS.data_dir.resolve()
    return (
        path.parent == path
        or path == data or data in path.parents or path in data.parents
        or any(text == root or text.startswith(root + "/") for root in FORBIDDEN_ROOTS)
    )


@router.get("/library/folders")
def list_folders(_: accounts.Session = Depends(guard.need_account)) -> dict[str, Any]:
    configured = [{"label": label, "path": path} for label, path in SETTINGS.file_roots]
    if SETTINGS.media_root:
        configured.append({"label": "Médias", "path": SETTINGS.media_root})
    return {"folders": _folders(), "configured": configured}


@router.post("/library/folders")
async def add_folder(body: FolderIn, st: AppState = Depends(state_of), _: accounts.Session = Depends(guard.need_write)) -> dict[str, Any]:
    try:
        path = Path(body.path.strip()).expanduser().resolve()
    except OSError as exc:
        raise HTTPException(400, "Chemin invalide.") from exc
    if not path.is_dir():
        raise HTTPException(400, "Ce dossier n'existe pas sur le boîtier.")
    if _forbidden(path):
        raise HTTPException(403, "Ce dossier système ne peut pas être partagé.")
    folders = [f for f in _folders() if f["path"] != str(path)]
    folders.append({"label": body.label.strip()[:60] or path.name, "path": str(path)})
    db.set_setting("library_dirs", json.dumps(folders, ensure_ascii=False))
    _jobs(st).watcher.poke()
    return {"folders": folders}


@router.delete("/library/folders")
async def remove_folder(path: str, st: AppState = Depends(state_of), _: accounts.Session = Depends(guard.need_write)) -> dict[str, Any]:
    folders = [f for f in _folders() if f["path"] != path]
    db.set_setting("library_dirs", json.dumps(folders, ensure_ascii=False))
    _jobs(st).watcher.poke()
    return {"folders": folders}

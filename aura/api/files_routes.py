"""REST: NAS file manager and machine stats (ported from NAS Dashboard v2, behind accounts)."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .. import __version__, db
from ..services import accounts, files, security
from . import guard
from .state import AppState, state_of

router = APIRouter(prefix="/api")
CHUNK = 1024 * 1024


def _roots(st: AppState) -> dict[str, Path]:
    return files.roots(st.library.watcher.volumes if st.library else [])


def _http(exc: files.FileError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _resolve(st: AppState, root: str, path: str) -> tuple[dict[str, Path], Path]:
    roots = _roots(st)
    try:
        return roots, files.resolve(roots, root or next(iter(roots)), path)
    except files.FileError as exc:
        raise _http(exc) from exc


def _audit(request: Request, session: accounts.Session, action: str, detail: str = "") -> None:
    accounts.audit(session.username, security.client_ip(request), action, detail)


@router.get("/fs/roots")
def fs_roots(st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_account)) -> dict[str, Any]:
    return {"roots": list(_roots(st)), "writable": session.can_write}


@router.get("/fs/list")
def fs_list(root: str = "", path: str = "", hidden: int = 0, sort: str = "name", desc: int = 0,
            st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_account)) -> dict[str, Any]:
    roots, target = _resolve(st, root, path)
    root = root or next(iter(roots))
    try:
        items = files.list_dir(target, bool(hidden), sort, bool(desc))
    except files.FileError as exc:
        raise _http(exc) from exc
    usage = shutil.disk_usage(target)
    rel = target.relative_to(roots[root]).as_posix()
    return {
        "root": root, "path": "" if rel == "." else rel, "writable": session.can_write, "items": items,
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
    }


@router.get("/fs/search")
def fs_search(root: str, q: str, path: str = "", st: AppState = Depends(state_of), _: accounts.Session = Depends(guard.need_account)) -> dict[str, Any]:
    roots, base = _resolve(st, root, path)
    try:
        items, truncated = files.search(roots[root], base, q)
    except files.FileError as exc:
        raise _http(exc) from exc
    return {"items": items, "truncated": truncated}


@router.get("/fs/download")
def fs_download(root: str, path: str, st: AppState = Depends(state_of), _: accounts.Session = Depends(guard.need_account)) -> FileResponse:
    _, target = _resolve(st, root, path)
    if not target.is_file():
        raise HTTPException(404, "Fichier introuvable.")
    return FileResponse(target, filename=target.name, media_type=files.media_type(target))


@router.get("/fs/stream")
def fs_stream(root: str, path: str, st: AppState = Depends(state_of), _: accounts.Session = Depends(guard.need_account)) -> FileResponse:
    _, target = _resolve(st, root, path)
    if not target.is_file():
        raise HTTPException(404, "Fichier introuvable.")
    return FileResponse(target, media_type=files.media_type(target), headers={"Cache-Control": "no-store"})


def _copy_upload(source: Any, dest: Path) -> int:
    part = dest.with_name(dest.name + ".part")
    try:
        with open(part, "wb") as out:
            shutil.copyfileobj(source, out, CHUNK)
            size = out.tell()
        part.replace(dest)
    except OSError:
        part.unlink(missing_ok=True)
        raise
    return size


@router.post("/fs/upload")
async def fs_upload(request: Request, root: str = Form(...), path: str = Form(""), file: UploadFile = File(...),
                    st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_write)) -> dict[str, Any]:
    _, folder = _resolve(st, root, path)
    if not folder.is_dir():
        raise HTTPException(400, "Dossier de destination introuvable.")
    try:
        dest = files.unique_destination(folder, files.safe_name(file.filename or "sans-nom"))
        size = await asyncio.to_thread(_copy_upload, file.file, dest)
    except files.FileError as exc:
        raise _http(exc) from exc
    except OSError as exc:
        raise HTTPException(500, f"Écriture impossible : {exc.strerror or exc}") from exc
    _audit(request, session, "upload", f"{dest} ({size})")
    if st.library:
        vol = st.library.volume_for_path(str(dest))
        if vol and dest.suffix.lower() in files.EXT["video"]:
            st.library.schedule_scan(vol, announce=True)
    return {"ok": True, "name": dest.name, "size": size}


@router.post("/fs/mkdir")
def fs_mkdir(request: Request, root: str = Form(...), path: str = Form(""), name: str = Form(...),
             st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_write)) -> dict[str, bool]:
    _, folder = _resolve(st, root, path)
    try:
        target = folder / files.safe_name(name)
    except files.FileError as exc:
        raise _http(exc) from exc
    if target.exists():
        raise HTTPException(409, "Un élément porte déjà ce nom.")
    target.mkdir()
    _audit(request, session, "mkdir", str(target))
    return {"ok": True}


@router.post("/fs/rename")
def fs_rename(request: Request, root: str = Form(...), path: str = Form(""), name: str = Form(...), new_name: str = Form(...),
              st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_write)) -> dict[str, bool]:
    _, folder = _resolve(st, root, path)
    try:
        src, dst = folder / files.safe_name(name), folder / files.safe_name(new_name)
    except files.FileError as exc:
        raise _http(exc) from exc
    if not src.exists():
        raise HTTPException(404, "Élément introuvable.")
    if dst.exists():
        raise HTTPException(409, "Un élément porte déjà ce nom.")
    src.rename(dst)
    _audit(request, session, "rename", f"{src} -> {dst.name}")
    return {"ok": True}


@router.post("/fs/move")
def fs_move(request: Request, root: str = Form(...), path: str = Form(""), names: str = Form(...), dest: str = Form(...),
            st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_write)) -> dict[str, Any]:
    roots, src_dir = _resolve(st, root, path)
    try:
        dst_dir = files.resolve(roots, root, dest)
    except files.FileError as exc:
        raise _http(exc) from exc
    if not dst_dir.is_dir():
        raise HTTPException(400, "Destination introuvable.")
    moved = 0
    for name in filter(None, names.split("\n")):
        try:
            src = src_dir / files.safe_name(name)
        except files.FileError:
            continue
        target = dst_dir / src.name
        if not src.exists() or target.exists() or target == src or src in target.parents:
            continue
        shutil.move(str(src), str(target))
        moved += 1
    _audit(request, session, "move", f"{moved} vers {dst_dir}")
    return {"ok": True, "moved": moved}


@router.post("/fs/delete")
def fs_delete(request: Request, root: str = Form(...), path: str = Form(""), names: str = Form(...),
              st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_write)) -> dict[str, Any]:
    roots, folder = _resolve(st, root, path)
    removed = 0
    for name in filter(None, names.split("\n")):
        try:
            target = folder / files.safe_name(name)
        except files.FileError:
            continue
        if not target.exists() or target in roots.values():
            continue
        shutil.rmtree(target) if target.is_dir() else target.unlink()
        removed += 1
    _audit(request, session, "delete", f"{removed} dans {folder}")
    return {"ok": True, "removed": removed}


@router.get("/stats")
def stats(st: AppState = Depends(state_of), _: accounts.Session = Depends(guard.need_account)) -> dict[str, Any]:
    out: dict[str, Any] = {"disks": [], "load": None, "uptime": None, "mem": None, "version": __version__}
    seen_devices: set[int] = set()
    for label, path in _roots(st).items():
        try:
            device = os.stat(path).st_dev
            usage = shutil.disk_usage(path)
        except OSError:
            continue
        if device in seen_devices:
            continue
        seen_devices.add(device)
        out["disks"].append({"label": label, "total": usage.total, "used": usage.used, "free": usage.free})
    try:
        out["load"] = os.getloadavg()[0]
    except (OSError, AttributeError):
        pass
    try:
        out["uptime"] = float(Path("/proc/uptime").read_text().split()[0])
    except OSError:
        pass
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, value = line.partition(":")
            info[key] = int(value.strip().split()[0]) * 1024
        out["mem"] = {"total": info.get("MemTotal", 0), "available": info.get("MemAvailable", 0)}
    except (OSError, ValueError, IndexError):
        pass
    row = db.query_one("SELECT COUNT(*) AS n FROM sessions WHERE expires > ?", (int(time.time()),))
    out["sessions"] = int(row["n"]) if row else 0
    return out

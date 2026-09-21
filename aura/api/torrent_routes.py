"""REST: NAS downloads through qBittorrent (links the owner provides, or picks from a search)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

from ..config import SETTINGS
from ..services import accounts, security, torrent_cards, torrent_search
from ..services.torrents import QBittorrent, TorrentError
from . import guard
from .state import AppState, state_of

router = APIRouter(prefix="/api")
_HASHES = re.compile(r"^(?:all|[0-9A-Fa-f]{32,64}(?:\|[0-9A-Fa-f]{32,64})*)$")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
TORRENT_MAX_BYTES = 4 * 1024 * 1024  # a .torrent is a few dozen KB; anything bigger is not one


def _client(st: AppState) -> QBittorrent:
    if not st.qbit:
        raise HTTPException(503, "qBittorrent n'est pas configuré sur ce boîtier.")
    return st.qbit


def _folder(category: str) -> str:
    return "Series" if category == "Series" else "Films"


def _targets(st: AppState) -> list[dict[str, Any]]:
    """Where a download may be written: the media root, then every mounted data volume."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    if SETTINGS.media_root:
        found.append({"id": "", "label": "Dossier médias", "path": SETTINGS.media_root, "default": True})
        seen.add(str(Path(SETTINGS.media_root)))
    if st.library:
        for volume in st.library.watcher.volumes:
            if volume.system or not volume.mounted or not volume.readable or str(Path(volume.mountpoint)) in seen:
                continue
            seen.add(str(Path(volume.mountpoint)))
            found.append({"id": volume.id, "label": volume.label, "path": volume.mountpoint, "default": False})
    for target in found:
        try:
            target["free"] = shutil.disk_usage(target["path"]).free
        except OSError:
            target["free"] = 0
    return found


def _save_path(st: AppState, volume_id: str, category: str) -> str:
    """The browser picks a volume by id, never by path: nothing a caller sends becomes a directory."""
    if not volume_id:
        return str(Path(SETTINGS.media_root) / _folder(category)) if SETTINGS.media_root else ""
    target = next((t for t in _targets(st) if t["id"] == volume_id), None)
    if target is None:
        raise HTTPException(400, "Ce disque n'est plus disponible.")
    return str(Path(target["path"]) / _folder(category))


@router.get("/torrents")
async def torrents(st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_account)) -> list[dict[str, Any]]:
    if not session.can_torrent:
        return []
    try:
        return await _client(st).list()
    except TorrentError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@router.get("/torrents/targets")
async def torrent_targets(st: AppState = Depends(state_of), _: accounts.Session = Depends(guard.need_torrent)) -> dict[str, Any]:
    return {"targets": _targets(st)}


@router.get("/torrents/search")
async def search_torrents(q: str = "", limit: int = torrent_search.DEFAULT_LIMIT, st: AppState = Depends(state_of),
                          _: accounts.Session = Depends(guard.need_torrent)) -> dict[str, Any]:
    """Metadata only: the answer feeds the grid, the download still goes through /torrents/add."""
    answer = await torrent_search.search(st.http, q, limit)
    cards = torrent_cards.group(answer["results"])
    answer["cards"] = await torrent_cards.enrich(st.http, cards)
    return answer


@router.get("/torrents/file/{result_id}")
async def torrent_file(result_id: str, st: AppState = Depends(state_of),
                       _: accounts.Session = Depends(guard.need_torrent)) -> Response:
    """Hand the browser a .torrent it cannot fetch itself (cross-origin), for a client on another machine.

    Only ids this box returned in a recent search resolve to a URL, so the endpoint cannot be pointed at
    the local network.
    """
    known = torrent_search.recall(result_id)
    if known is None:
        raise HTTPException(404, "Ce résultat n'est plus en mémoire, relance la recherche.")
    url, name = known
    try:
        upstream = await st.http.get(url, timeout=20.0)
        upstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Fichier .torrent injoignable ({type(exc).__name__}).") from exc
    if len(upstream.content) > TORRENT_MAX_BYTES:
        raise HTTPException(502, "Le fichier renvoyé est trop gros pour être un .torrent.")
    stem = _SAFE_NAME.sub(".", name or result_id).strip(".")[:120] or "aura"
    filename = f"{stem}.torrent"
    return Response(
        upstream.content,
        media_type="application/x-bittorrent",
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
            "Cache-Control": "no-store",
        },
    )


@router.post("/torrents/add")
async def torrent_add(request: Request, magnet: str = Form(...), category: str = Form("Films"), volume: str = Form(""),
                      st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_torrent)) -> dict[str, bool]:
    link = magnet.strip()
    if not (link.startswith("magnet:?") or link.startswith(("http://", "https://"))) or any(c.isspace() for c in link):
        raise HTTPException(400, "Colle un lien magnet ou une URL .torrent.")
    save_path = _save_path(st, volume.strip(), category)
    try:
        await _client(st).add(link, save_path, _folder(category))
    except TorrentError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    accounts.audit(session.username, security.client_ip(request), "torrent_add", link[:120])
    return {"ok": True}


@router.post("/torrents/action")
async def torrent_action(request: Request, hashes: str = Form(...), do: str = Form(...), delete_files: str = Form("false"),
                         st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_torrent)) -> dict[str, bool]:
    if not _HASHES.match(hashes):
        raise HTTPException(400, "Identifiant de téléchargement invalide.")
    try:
        await _client(st).action(hashes, do, delete_files == "true")
    except TorrentError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    accounts.audit(session.username, security.client_ip(request), f"torrent_{do}")
    return {"ok": True}

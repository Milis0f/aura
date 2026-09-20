"""REST: NAS downloads through qBittorrent (links the owner provides)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from ..config import SETTINGS
from ..services import accounts, security
from ..services import torrent_search
from ..services.torrents import QBittorrent, TorrentError
from . import guard
from .state import AppState, state_of

router = APIRouter(prefix="/api")
_HASHES = re.compile(r"^(?:all|[0-9A-Fa-f]{32,64}(?:\|[0-9A-Fa-f]{32,64})*)$")


def _client(st: AppState) -> QBittorrent:
    if not st.qbit:
        raise HTTPException(503, "qBittorrent n'est pas configuré sur ce boîtier.")
    return st.qbit


@router.get("/torrents")
async def torrents(st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_account)) -> list[dict[str, Any]]:
    if not session.can_torrent:
        return []
    try:
        return await _client(st).list()
    except TorrentError as exc:
        raise HTTPException(exc.status, exc.message) from exc


@router.get("/torrents/search")
async def search_torrents(q: str = "", limit: int = 30, st: AppState = Depends(state_of),
                         _: accounts.Session = Depends(guard.need_torrent)) -> dict[str, Any]:
    """Metadata only: the answer feeds the table, the download still goes through /torrents/add."""
    return await torrent_search.search(st.http, q, limit)


@router.post("/torrents/add")
async def torrent_add(request: Request, magnet: str = Form(...), category: str = Form("Films"),
                      st: AppState = Depends(state_of), session: accounts.Session = Depends(guard.need_torrent)) -> dict[str, bool]:
    link = magnet.strip()
    if not (link.startswith("magnet:?") or link.startswith(("http://", "https://"))) or any(c.isspace() for c in link):
        raise HTTPException(400, "Colle un lien magnet ou une URL .torrent.")
    folder = "Series" if category == "Series" else "Films"
    save_path = str(Path(SETTINGS.media_root) / folder) if SETTINGS.media_root else ""
    try:
        await _client(st).add(link, save_path, folder)
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

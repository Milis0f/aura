"""qBittorrent Web API client for the NAS downloads.

One long-lived client keeps the SID cookie, so the login happens once instead of on every refresh: the old dashboard
logged in every three seconds and got 127.0.0.1 banned by qBittorrent after a single wrong password. A failed login
is not retried for a minute for the same reason. qBittorrent 5 renamed pause/resume to stop/start; both work.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

ACTIONS = {
    "pause": ("/api/v2/torrents/stop", "/api/v2/torrents/pause"),
    "resume": ("/api/v2/torrents/start", "/api/v2/torrents/resume"),
    "delete": ("/api/v2/torrents/delete",),
    "recheck": ("/api/v2/torrents/recheck",),
    "topprio": ("/api/v2/torrents/topPrio",),
}


class TorrentError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class QBittorrent:
    def __init__(self, base_url: str, username: str = "", password: str = "", transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.username = username
        self._password = password
        self._logged_in = not username  # LocalHostAuth=false: qBittorrent trusts 127.0.0.1 without a login
        self._failed_at = 0.0
        self._lock = asyncio.Lock()
        self.client = httpx.AsyncClient(base_url=self.base, timeout=httpx.Timeout(15.0, connect=3.0), transport=transport)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _login(self) -> None:
        async with self._lock:
            if self._logged_in:
                return
            if time.monotonic() - self._failed_at < 60:
                raise TorrentError(502, "qBittorrent a refusé les identifiants. Nouvel essai dans une minute.")
            try:
                r = await self.client.post("/api/v2/auth/login", data={"username": self.username, "password": self._password}, headers={"Referer": self.base})
            except httpx.HTTPError as exc:
                raise TorrentError(502, f"qBittorrent injoignable sur {self.base} ({type(exc).__name__}).") from exc
            if r.text.strip() != "Ok.":
                self._failed_at = time.monotonic()
                raise TorrentError(502, "qBittorrent refuse la connexion (identifiants).")
            self._logged_in = True

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if not self._logged_in:
            await self._login()
        try:
            r = await self.client.request(method, path, headers={"Referer": self.base}, **kwargs)
            if r.status_code == 403 and self.username:
                self._logged_in = False
                await self._login()
                r = await self.client.request(method, path, headers={"Referer": self.base}, **kwargs)
        except httpx.HTTPError as exc:
            raise TorrentError(502, f"qBittorrent injoignable sur {self.base} ({type(exc).__name__}).") from exc
        if r.status_code == 403:
            raise TorrentError(502, "qBittorrent refuse l'accès : vérifie l'option « Contourner l'authentification pour localhost ».")
        return r

    async def list(self) -> list[dict[str, Any]]:
        r = await self._request("GET", "/api/v2/torrents/info")
        try:
            data = r.json()
        except ValueError as exc:
            raise TorrentError(502, "Réponse inattendue de qBittorrent.") from exc
        return [
            {
                "hash": t.get("hash"), "name": t.get("name"), "progress": round(float(t.get("progress") or 0) * 100, 1),
                "state": t.get("state"), "dl": t.get("dlspeed", 0), "up": t.get("upspeed", 0), "size": t.get("size", 0),
                "done": t.get("completed", 0), "eta": t.get("eta", 0), "seeds": t.get("num_seeds", 0),
                "peers": t.get("num_leechs", 0), "ratio": round(float(t.get("ratio") or 0), 2),
                "category": t.get("category", ""), "save_path": t.get("save_path", ""), "content_path": t.get("content_path", ""),
            }
            for t in data
            if isinstance(t, dict)
        ]

    async def add(self, urls: str, save_path: str, category: str) -> None:
        data = {"urls": urls, "category": category}
        if save_path:
            data["savepath"] = save_path
        r = await self._request("POST", "/api/v2/torrents/add", data=data)
        if r.status_code != 200 or r.text.strip() == "Fails.":
            raise TorrentError(502, "qBittorrent a refusé ce lien.")

    async def action(self, hashes: str, do: str, delete_files: bool = False) -> None:
        paths = ACTIONS.get(do)
        if not paths:
            raise TorrentError(400, "Action inconnue.")
        data = {"hashes": hashes}
        if do == "delete":
            data["deleteFiles"] = "true" if delete_files else "false"
        for path in paths:
            r = await self._request("POST", path, data=data)
            if r.status_code != 404:
                if r.status_code >= 400:
                    raise TorrentError(502, f"qBittorrent a refusé l'action ({r.status_code}).")
                return
        raise TorrentError(502, "Action non prise en charge par cette version de qBittorrent.")

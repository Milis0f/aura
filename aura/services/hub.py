"""WebSocket hub: the TV page and phone remotes are connected clients; the server relays commands."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger(__name__)


class Hub:
    def __init__(self) -> None:
        self.tv: set[WebSocket] = set()
        self.remotes: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket, role: str) -> None:
        async with self._lock:
            (self.tv if role == "tv" else self.remotes).add(ws)
        log.info("ws %s connected (%d tv, %d remotes)", role, len(self.tv), len(self.remotes))

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self.tv.discard(ws)
            self.remotes.discard(ws)

    @property
    def tv_connected(self) -> bool:
        return bool(self.tv)

    async def _send_all(self, targets: set[WebSocket], payload: dict[str, Any]) -> int:
        data = json.dumps(payload, ensure_ascii=False)
        dead = []
        sent = 0
        for ws in list(targets):
            try:
                await ws.send_text(data)
                sent += 1
            except Exception:  # noqa: BLE001 - client gone
                dead.append(ws)
        for ws in dead:
            await self.remove(ws)
        return sent

    async def to_tv(self, payload: dict[str, Any]) -> int:
        return await self._send_all(self.tv, payload)

    async def to_remotes(self, payload: dict[str, Any]) -> int:
        return await self._send_all(self.remotes, payload)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        await self.to_tv(payload)
        await self.to_remotes(payload)

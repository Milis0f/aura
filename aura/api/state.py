"""Shared application state (one instance per process) and FastAPI dependencies."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx
from fastapi import Request

from ..config import SETTINGS
from ..services.hub import Hub
from ..services.kiosk import Kiosk
from ..services.library_jobs import LibraryJobs
from ..services.player import Player
from ..services.torrents import QBittorrent


@dataclass
class AppState:
    http: httpx.AsyncClient
    stream_http: httpx.AsyncClient
    player: Player = field(default_factory=Player)
    hub: Hub = field(default_factory=Hub)
    kiosk: Kiosk = field(default_factory=Kiosk)
    refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    tasks: list[asyncio.Task] = field(default_factory=list)
    pending_play: tuple[float, dict] | None = None  # play order waiting for the TV page to reconnect
    library: LibraryJobs | None = None
    qbit: QBittorrent | None = None


def make_http() -> httpx.AsyncClient:
    """General purpose client: catalogues, EPG, metadata. Patience is fine here."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(SETTINGS.http_timeout, connect=10),
        headers={"User-Agent": SETTINGS.user_agent},
        follow_redirects=True,
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
    )


def make_stream_http() -> httpx.AsyncClient:
    """Video path: fail fast, keep connections hot, allow many parallel segments.

    A 10 s connect timeout on a dead CDN is 10 s of black screen, so it drops to 3 s and the
    failover list takes over. Keep-alive matters more than anything else: re-handshaking TLS for
    every 6-second segment is what makes cheap boxes stutter.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=3.0, read=15.0, pool=5.0),
        headers={"User-Agent": SETTINGS.user_agent},
        follow_redirects=True,
        limits=httpx.Limits(max_connections=64, max_keepalive_connections=32, keepalive_expiry=90),
    )


def state_of(request: Request) -> AppState:
    return request.app.state.aura

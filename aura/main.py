"""FastAPI application: routers, static UIs, background work (catalogue, drives, library, downloads)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db
from .api import (
    auth_routes,
    catalog_routes,
    files_routes,
    guard,
    library_routes,
    player_routes,
    system_routes,
    torrent_routes,
    vod_routes,
)
from .api.state import AppState, make_http, make_stream_http
from .config import SETTINGS, STATIC_DIR
from .services import accounts, catalog, library, network, sports
from .services.library_jobs import LibraryJobs
from .services.player import PlayerState
from .services.torrents import QBittorrent

log = logging.getLogger("aura")


class RevalidatingStatic(StaticFiles):
    """Static files the browser must revalidate on every load (cheap on a LAN thanks to ETags).

    Without it the kiosk Chrome keeps yesterday's CSS and JavaScript after a nightly update.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


async def scheduler(st: AppState) -> None:
    """Periodic refresh of sources, EPG and sports. Waits for network; never dies on errors."""
    await asyncio.sleep(5)
    while True:
        try:
            net = await network.status()
            if not net.online:
                await asyncio.sleep(30)
                continue
            now = int(time.time())
            epg_age = now - int(db.get_setting("epg_last_refresh", "0") or 0)
            sports_age = now - int(db.get_setting("sports_last_refresh", "0") or 0)
            src_age = now - int(db.get_setting("sources_last_refresh", "0") or 0)
            if src_age > SETTINGS.sources_refresh_hours * 3600 and not st.refresh_lock.locked():
                async with st.refresh_lock:
                    await catalog.refresh_all_sources(st.http)
                    db.set_setting("sources_last_refresh", str(now))
                await st.hub.broadcast({"type": "catalog_changed"})
            if epg_age > SETTINGS.epg_refresh_hours * 3600 and not st.refresh_lock.locked():
                async with st.refresh_lock:
                    await catalog.refresh_epg(st.http)
                await st.hub.broadcast({"type": "epg_changed"})
            if sports_age > SETTINGS.sports_refresh_hours * 3600:
                await sports.refresh(st.http)
            if accounts.purge_expired():
                log.debug("expired sessions purged")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler error: %s", exc)
        await asyncio.sleep(300)


async def library_finished(st: AppState, last: PlayerState) -> None:
    """mpv exited on its own: remember where it stopped, and chain the next episode after a finished one."""
    file_id = last.item_id[4:]
    if not last.duration or last.position < last.duration * library.FINISHED_RATIO:
        if last.position > 0 and last.duration:
            library.save_progress(file_id, last.position, last.duration)
        return
    library.mark_finished(file_id)
    if file_id.startswith("link:") or db.get_setting("autoplay_next", "1") == "0":
        return
    nxt = library.next_episode(file_id)
    if nxt:
        await library_routes.play_on_tv(st, file_id=nxt["id"], position=0)


async def mpv_watch(st: AppState) -> None:
    """Keep player state in sync while mpv runs, save library progress, notify clients when it exits."""
    saved_at = 0.0
    while True:
        try:
            before = st.player.state
            state = await st.player.poll()
            if before.backend == "mpv":
                await st.hub.to_remotes({"type": "player", "state": state.to_dict()})
                if state.backend == "mpv" and before.item_id.startswith("lib:") and state.duration and abs(state.position - saved_at) >= 5:
                    library.save_progress(before.item_id[4:], state.position, state.duration)
                    saved_at = state.position
                if state.backend == "idle":
                    saved_at = 0.0
                    await st.hub.to_tv({"type": "stop"})
                    if before.item_id.startswith("lib:"):
                        await library_finished(st, before)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("mpv watch: %s", exc)
        await asyncio.sleep(2)


async def onboarding_hotspot(st: AppState) -> None:
    """If no network after boot, try to open the setup hotspot (best effort, may be unsupported)."""
    await asyncio.sleep(40)
    if not network.managed():
        return
    try:
        net = await network.status()
        if not net.online and not net.hotspot_active:
            ok, msg = await network.start_hotspot()
            log.info("onboarding hotspot: %s %s", ok, msg)
            await st.hub.broadcast({"type": "network_changed"})
    except Exception as exc:  # noqa: BLE001
        log.debug("hotspot: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    removed = catalog.dedupe_sources()
    if removed:
        log.info("removed %d duplicate playlist(s)", removed)
    purged = catalog.purge_invalid_channels()
    if purged:
        log.info("removed %d invalid channel row(s)", purged)
    renamed = catalog.migrate_names()
    if renamed:
        log.info("recomputed name keys for %d channel(s)", renamed)
    accounts.purge_expired()
    st = AppState(http=make_http(), stream_http=make_stream_http())
    st.library = LibraryJobs(st.http, st.hub)
    if SETTINGS.qb_url:
        st.qbit = QBittorrent(SETTINGS.qb_url, SETTINGS.qb_user, SETTINGS.qb_pass)
    app.state.aura = st
    st.tasks = [
        asyncio.create_task(scheduler(st)),
        asyncio.create_task(mpv_watch(st)),
        asyncio.create_task(onboarding_hotspot(st)),
        asyncio.create_task(st.library.watcher.run(st.library.on_change)),
        asyncio.create_task(st.library.metadata_loop()),
        asyncio.create_task(st.library.periodic()),
    ]
    if st.qbit:
        st.tasks.append(asyncio.create_task(st.library.torrent_loop(st.qbit)))
    log.info("Aura %s ready on %s:%s (data: %s)", __version__, SETTINGS.host, SETTINGS.port, SETTINGS.data_dir)
    try:
        yield
    finally:
        for task in st.tasks:
            task.cancel()
        await asyncio.gather(*st.tasks, return_exceptions=True)
        await st.player.stop()
        await st.http.aclose()
        await st.stream_http.aclose()
        if st.qbit:
            await st.qbit.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Aura", version=__version__, lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
    for router in (
        auth_routes.router,
        catalog_routes.router,
        files_routes.router,
        library_routes.router,
        player_routes.router,
        system_routes.router,
        torrent_routes.router,
        vod_routes.router,
    ):
        app.include_router(router)
    app.add_middleware(guard.Gate)

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "web" / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.mount("/vendor", RevalidatingStatic(directory=STATIC_DIR / "vendor"), name="vendor")
    app.mount("/shared", RevalidatingStatic(directory=STATIC_DIR / "shared"), name="shared")
    app.mount("/web", RevalidatingStatic(directory=STATIC_DIR / "web", html=True), name="web")
    app.mount("/tv", RevalidatingStatic(directory=STATIC_DIR / "tv", html=True), name="tv")
    app.mount("/remote", RevalidatingStatic(directory=STATIC_DIR / "remote", html=True), name="remote")
    return app


app = create_app()


def run(argv: list[str] | None = None) -> None:
    """Start the server. The port comes from AURA_PORT, then PORT (set by dev tooling), then 8000."""
    parser = argparse.ArgumentParser(prog="aura", description="Aura: TV, films, drives and NAS on one box")
    parser.add_argument("--host", default=SETTINGS.host, help="bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=SETTINGS.port, help="HTTP port (default: %(default)s)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("aura.main:app", host=args.host, port=args.port, log_level="info", proxy_headers=False)


if __name__ == "__main__":
    run()

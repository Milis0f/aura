"""Background work around the library: react to drives, scan them, fetch posters, read durations."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from .. import db
from . import library, library_scan, metadata, network, storage
from .hub import Hub

log = logging.getLogger(__name__)

RESCAN_EVERY = 6 * 3600
STALE_AFTER = 6 * 3600
LOOKUP_PAUSE = 3.0  # iTunes allows about twenty searches a minute
LOOKUP_PAUSE_TMDB = 0.4


async def probe_video(path: Path) -> tuple[float, str, int]:
    """Duration, video codec and height through ffprobe. Zeros when unreadable."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "format=duration:stream=codec_name,height", "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 40)
        data = json.loads(out or b"{}")
    except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
        if proc and proc.returncode is None:
            proc.kill()
        return 0.0, "", 0
    stream = (data.get("streams") or [{}])[0]
    try:
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return duration, str(stream.get("codec_name") or ""), int(stream.get("height") or 0)


class LibraryJobs:
    def __init__(self, http: httpx.AsyncClient, hub: Hub) -> None:
        self.http = http
        self.hub = hub
        self.watcher = storage.DriveWatcher()
        self.progress: dict[str, int] = {}
        self.ready = asyncio.Event()  # set once the first drive listing has been handled
        self._scans: dict[str, asyncio.Task[None]] = {}
        self._meta_wake = asyncio.Event()

    async def settle(self, timeout: float = 60) -> None:
        """Wait for the first drive listing and for the scans running now (first boot, tests)."""
        await asyncio.wait_for(self.ready.wait(), timeout)
        pending = [t for t in self._scans.values() if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)

    # ---- drives ------------------------------------------------------------

    def volume(self, drive_id: str) -> storage.Volume | None:
        return self.watcher.find(drive_id)

    def eligible(self) -> list[storage.Volume]:
        return storage.library_volumes(self.watcher.volumes)

    def volume_for_path(self, path: str) -> storage.Volume | None:
        if not path:
            return None
        target = Path(path)
        best: storage.Volume | None = None
        for vol in self.eligible():
            root = Path(vol.mountpoint)
            if (target == root or root in target.parents) and (best is None or len(vol.mountpoint) > len(best.mountpoint)):
                best = vol
        return best

    @staticmethod
    def _payload(vol: storage.Volume) -> dict[str, Any]:
        return {"id": vol.id, "label": vol.label, "kind": vol.kind, "size": vol.size, "removable": vol.removable}

    async def on_change(self, appeared: list[storage.Volume], disappeared: list[storage.Volume], initial: bool) -> None:
        eligible = self.eligible()
        for vol in eligible:
            library.upsert_drive(vol, available=True)
        gone = library.mark_unavailable_except(v.id for v in eligible)
        for drive in gone:
            task = self._scans.pop(drive["id"], None)
            if task:
                task.cancel()
            self.progress.pop(drive["id"], None)
            if not initial:
                await self.hub.broadcast({"type": "drive_removed", "drive": {"id": drive["id"], "label": drive["label"]}})
        if gone and not initial:
            await self.hub.broadcast({"type": "library_changed"})
        for vol in appeared:
            if initial:
                last = int((library.get_drive(vol.id) or {}).get("last_scan") or 0)
                if time.time() - last > STALE_AFTER:
                    self.schedule_scan(vol, announce=False)
            else:
                await self.hub.broadcast({"type": "drive_added", "drive": self._payload(vol)})
                self.schedule_scan(vol, announce=True)
        if initial:
            self.ready.set()

    # ---- scans -------------------------------------------------------------

    def scanning(self, drive_id: str) -> bool:
        task = self._scans.get(drive_id)
        return bool(task and not task.done())

    def schedule_scan(self, vol: storage.Volume, announce: bool = True) -> bool:
        if self.scanning(vol.id):
            return False
        self._scans[vol.id] = asyncio.create_task(self._scan(vol, announce))
        return True

    def rescan_all(self, announce: bool = False) -> int:
        return sum(self.schedule_scan(vol, announce) for vol in self.eligible())

    async def _scan(self, vol: storage.Volume, announce: bool) -> None:
        library.set_scan_state(vol.id, "scanning")
        self.progress[vol.id] = 0
        if announce:
            await self.hub.broadcast({"type": "library_scan", "state": "scanning", "drive": self._payload(vol)})

        def on_progress(count: int) -> None:
            self.progress[vol.id] = count

        try:
            report = await asyncio.to_thread(library_scan.scan, vol.id, Path(vol.mountpoint), on_progress)
        except asyncio.CancelledError:
            library.set_scan_state(vol.id, "")
            raise
        except (library_scan.EmptyVolume, FileNotFoundError) as exc:
            log.info("scan of %s skipped: %s", vol.label, exc)
            library.set_scan_state(vol.id, "absent")
            return
        except Exception as exc:  # noqa: BLE001 - a broken disk must not take the service down
            log.warning("scan of %s failed: %s", vol.label, exc)
            library.set_scan_state(vol.id, f"erreur : {exc}")
            if announce:
                await self.hub.broadcast({"type": "library_scan", "state": "error", "drive": self._payload(vol), "error": str(exc)[:200]})
            return
        finally:
            self.progress.pop(vol.id, None)
        counts = library.finish_scan(vol.id)
        self._meta_wake.set()
        log.info("scan of %s: %d files, %d changed, %d removed, %d new titles", vol.label, report.files, report.changed, report.removed, report.new_items)
        if announce or report.new_items or report.removed:
            await self.hub.broadcast({
                "type": "library_scan", "state": "done", "drive": self._payload(vol),
                "new_items": report.new_items, "removed": report.removed, "files": report.files, **counts,
            })
        if report.changed or report.removed:
            await self.hub.broadcast({"type": "library_changed"})

    async def periodic(self) -> None:
        while True:
            await asyncio.sleep(RESCAN_EVERY)
            try:
                self.rescan_all(announce=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("periodic rescan: %s", exc)

    # ---- posters and durations ---------------------------------------------

    def wake_metadata(self) -> None:
        self._meta_wake.set()

    async def metadata_loop(self) -> None:
        await asyncio.sleep(10)
        while True:
            try:
                worked = await self._metadata_step() or await self._probe_step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("library metadata: %s", exc)
                worked = False
            if not worked:
                self._meta_wake.clear()
                try:
                    await asyncio.wait_for(self._meta_wake.wait(), 900)
                except asyncio.TimeoutError:
                    pass

    async def _metadata_step(self) -> bool:
        pending = library.pending_metadata(6)
        if not pending or not await network.internet_reachable():
            return False
        pause = LOOKUP_PAUSE_TMDB if db.get_setting("tmdb_api_key", "") else LOOKUP_PAUSE
        for item in pending:
            media = "tv" if item["kind"] == library.SERIES else "movie"
            name = f"{item['title']} ({item['year']})" if item["year"] else item["title"]
            library.save_metadata(item["id"], await metadata.lookup(self.http, name, media))
            await asyncio.sleep(pause)
        if not library.pending_metadata(1):
            await self.hub.broadcast({"type": "library_meta"})
        return True

    async def _probe_step(self) -> bool:
        if not shutil.which("ffprobe"):
            return False
        files = library.files_to_probe(4)
        for f in files:
            library.save_probe(f["id"], *(await probe_video(f["path"])))
        return bool(files)

    # ---- downloads ---------------------------------------------------------

    async def torrent_loop(self, client: Any) -> None:
        """Rescan the volume a torrent finished on, so the film shows up without touching anything."""
        done: set[str] | None = None
        while True:
            await asyncio.sleep(90)
            try:
                torrents = await client.list()
            except Exception:  # noqa: BLE001 - qBittorrent absent or restarting
                continue
            finished = {t["hash"]: t for t in torrents if float(t.get("progress") or 0) >= 100}
            if done is not None:
                for key, torrent in finished.items():
                    if key not in done:
                        vol = self.volume_for_path(torrent.get("content_path") or torrent.get("save_path") or "")
                        if vol:
                            self.schedule_scan(vol, announce=True)
            done = set(finished)

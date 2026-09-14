"""Playback routing.

Two backends:
  - 'browser': the TV page plays the stream itself (native HLS/MP4, hls.js, mpegts.js).
  - 'mpv'    : the server spawns mpv fullscreen (RTMP/RTSP/UDP/MKV/anything browsers can't decode),
               controlled through its JSON IPC socket. The kiosk compositor (cage) shows the newest
               window on top, so mpv covers the browser and the browser reappears when mpv exits.
On non-Linux hosts the mpv backend is a no-op logger so the whole app can be developed on Windows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, replace
from typing import Any
from urllib.parse import urlparse

from ..config import IS_LINUX, SETTINGS

log = logging.getLogger(__name__)

MPV_SCHEMES = ("rtmp", "rtmps", "rtsp", "udp", "rtp", "mms", "srt")
# mpv sits on top of the kiosk and receives the keyboard: a small, predictable set of keys for a TV remote or a USB keyboard.
MPV_INPUT = """SPACE cycle pause
ENTER cycle pause
KP_ENTER cycle pause
PLAYPAUSE cycle pause
MBTN_LEFT cycle pause
RIGHT seek 30
LEFT seek -15
UP add volume 5
DOWN add volume -5
a cycle audio
s cycle sub
i show-progress
ESC quit
BS quit
q quit
MBTN_BACK quit
"""


def mpv_input_conf() -> str:
    path = SETTINGS.data_dir / "mpv-input.conf"
    try:
        if not path.exists() or path.read_text(encoding="utf-8") != MPV_INPUT:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(MPV_INPUT, encoding="utf-8")
    except OSError:
        return ""
    return str(path)
MPV_EXT = (".mkv", ".avi", ".wmv", ".flv", ".mov", ".m2ts", ".vob", ".iso", ".mpd")  # always mpv (.mpd = DASH)
TS_EXT = (".ts", ".m2t", ".mts")  # MPEG-TS: mpv only when the CPU cannot afford mpegts.js
BROWSER_EXT = (".m3u8", ".m3u", ".mp4", ".m4v", ".webm", ".ogv")


def live_backend_pref() -> str:
    """'auto' | 'browser' | 'mpv' — user setting, 'auto' decides from the URL and the hardware."""
    from .. import db

    return db.get_setting("live_backend", "auto") or "auto"


def weak_hardware() -> bool:
    """True on the kind of machine this appliance targets (old dual-core Intel, no modern GPU).

    It matters because mpegts.js demuxes MPEG-TS in JavaScript: fine on a recent laptop, a
    slideshow on a 2012 Mac mini. Those streams go to mpv, which decodes in hardware.
    """
    if not IS_LINUX:
        return False
    try:
        cores = os.cpu_count() or 2
    except NotImplementedError:
        cores = 2
    return cores <= 4


def decide_backend(url: str, extra: dict[str, Any] | None = None, kind: str = "live") -> str:
    p = urlparse(url)
    path = p.path.lower()
    if p.scheme in MPV_SCHEMES:
        return "mpv"
    if (extra or {}).get("force_mpv"):
        return "mpv"
    if path.endswith(BROWSER_EXT):
        return "browser"
    if path.endswith(MPV_EXT):
        return "mpv"
    pref = live_backend_pref()
    if pref == "mpv":
        return "mpv"
    if pref == "browser":
        return "browser"
    # auto: raw MPEG-TS (explicit .ts, or an extension-less live URL) is cheap for mpv and
    # expensive for the browser. Everything else stays in the page.
    fmt = (extra or {}).get("format", "")
    if fmt in ("hls", "mp4"):
        return "browser"  # sniffed by the probe: HLS hides behind many extension-less URLs
    mpeg_ts = fmt == "mpegts" or path.endswith(TS_EXT) or (kind == "live" and not fmt and not re.search(r"\.[a-z0-9]{2,4}$", path))
    if mpeg_ts and weak_hardware() and shutil.which("mpv"):
        return "mpv"
    return "browser"


@dataclass(frozen=True)
class PlayerState:
    backend: str = "idle"  # idle | browser | mpv | app
    item_id: str = ""
    name: str = ""
    url: str = ""
    kind: str = "live"
    started: int = 0
    paused: bool = False
    position: float = 0
    duration: float = 0
    error: str = ""
    app: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MpvBackend:
    """Spawn/control mpv through its JSON IPC socket."""

    def __init__(self, socket_path: str):
        self.socket_path = socket_path
        self.proc: asyncio.subprocess.Process | None = None

    @property
    def available(self) -> bool:
        return IS_LINUX and shutil.which("mpv") is not None

    async def play(
        self, url: str, headers: dict[str, str] | None = None, title: str = "", kind: str = "live",
        start: float = 0.0, local: bool = False,
    ) -> None:
        await self.stop()
        live = kind == "live"
        args = [
            "mpv",
            "--fullscreen",
            "--no-osc",
            "--no-input-default-bindings",
            "--input-ipc-server=" + self.socket_path,
            "--force-window=immediate",
            "--keep-open=no",
            "--osd-level=" + ("1" if local else "0"),
            "--msg-level=all=error",
            # hardware decoding: the difference between 8 fps and 60 fps on an HD 4000
            "--hwdec=auto-safe",
            "--vo=gpu",
            "--profile=fast",
            "--framedrop=vo",
            "--video-sync=audio",
            "--title=" + (title or "Aura"),
        ]
        input_conf = mpv_input_conf()
        if input_conf:
            args.append("--input-conf=" + input_conf)
        if local:
            # A film on a USB disk: read ahead generously, prefer French audio and subtitles, pick up .srt files.
            args += [
                "--cache=yes",
                "--demuxer-max-bytes=256MiB",
                "--demuxer-max-back-bytes=64MiB",
                "--demuxer-readahead-secs=30",
                "--alang=fr,fre,fra,fr-FR,en,eng",
                "--slang=fr,fre,fra,fr-FR",
                "--sub-auto=fuzzy",
                "--osd-font-size=42",
            ]
            if start > 5:
                args.append(f"--start={int(start)}")
        else:
            args += [
                # buffering: enough to ride out a CDN hiccup, not so much that zapping feels slow
                "--cache=yes",
                f"--cache-secs={30 if live else 120}",
                "--demuxer-max-bytes=" + ("64MiB" if live else "192MiB"),
                "--demuxer-max-back-bytes=32MiB",
                f"--demuxer-readahead-secs={5 if live else 20}",
                # reconnect instead of dying when the stream blips
                "--network-timeout=10",
                "--stream-lavf-o=reconnect=1,reconnect_at_eof=1,reconnect_streamed=1,"
                "reconnect_on_network_error=1,reconnect_delay_max=5",
                "--user-agent=" + (headers or {}).get("User-Agent", SETTINGS.user_agent),
            ]
            if headers and headers.get("Referer"):
                args.append("--referrer=" + headers["Referer"])
            if start > 5 and not live:
                args.append(f"--start={int(start)}")
        args.append(url)
        log.info("mpv: %s", url)
        self.proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

    async def command(self, *cmd: Any) -> Any:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.socket_path), 2)  # type: ignore[attr-defined]
        except (OSError, asyncio.TimeoutError):
            return None
        try:
            writer.write((json.dumps({"command": list(cmd)}) + "\n").encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), 2)
            return json.loads(line or b"{}").get("data")
        except (OSError, asyncio.TimeoutError, json.JSONDecodeError):
            return None
        finally:
            writer.close()

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            await self.command("quit")
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
        self.proc = None

    def running(self) -> bool:
        return bool(self.proc and self.proc.returncode is None)


class Player:
    def __init__(self) -> None:
        self.state = PlayerState()
        self.mpv = MpvBackend(SETTINGS.mpv_socket)
        self._lock = asyncio.Lock()

    def _set(self, **changes: Any) -> PlayerState:
        self.state = replace(self.state, **changes)
        return self.state

    async def play(self, item: dict[str, Any], prefer: str | None = None) -> PlayerState:
        async with self._lock:
            url = item["url"]
            extra = item.get("extra") or {}
            backend = prefer or decide_backend(url, extra, item.get("kind", "live"))
            if backend == "mpv" and not self.mpv.available:
                backend = "browser"
                log.warning("mpv unavailable, falling back to browser for %s", url)
            if backend == "mpv":
                headers = {
                    "User-Agent": extra.get("http-user-agent", SETTINGS.user_agent),
                    "Referer": extra.get("http-referrer", ""),
                }
                await self.mpv.play(
                    url, headers, item.get("name", ""), item.get("kind", "live"),
                    start=float(extra.get("start") or 0), local=bool(extra.get("local")),
                )
            else:
                await self.mpv.stop()
            return self._set(
                backend=backend,
                item_id=item.get("id", ""),
                name=item.get("name", ""),
                url=url,
                kind=item.get("kind", "live"),
                started=int(time.time()),
                paused=False,
                position=float(extra.get("start") or 0),
                duration=float(extra.get("duration") or 0),
                error="",
                app="",
            )

    async def stop(self) -> PlayerState:
        async with self._lock:
            await self.mpv.stop()
            return self._set(backend="idle", item_id="", name="", url="", error="", app="")

    def tag(self, item_id: str) -> PlayerState:
        """Attach a library reference to what is playing, so progress lands on the right title."""
        return self._set(item_id=item_id)

    async def set_app(self, key: str) -> PlayerState:
        async with self._lock:
            await self.mpv.stop()
            return self._set(backend="app", app=key, item_id="", name=key, url="")

    async def pause_toggle(self) -> PlayerState:
        if self.state.backend == "mpv":
            await self.mpv.command("cycle", "pause")
            paused = await self.mpv.command("get_property", "pause")
            return self._set(paused=bool(paused))
        return self._set(paused=not self.state.paused)

    async def seek(self, seconds: float) -> None:
        if self.state.backend == "mpv":
            await self.mpv.command("seek", seconds, "relative")
            if self.state.kind != "live":
                await self.mpv.command("show-progress")

    async def cycle(self, what: str) -> str:
        """Next audio or subtitle track in mpv (VF, VO, VOSTFR…). Returns what the TV now shows."""
        prop = {"audio": "aid", "sub": "sid"}.get(what)
        if not prop or self.state.backend != "mpv":
            return ""
        await self.mpv.command("cycle", prop)
        track = await self.mpv.command("get_property", f"current-tracks/{'audio' if prop == 'aid' else 'sub'}")
        if isinstance(track, dict):
            label = " · ".join(str(x) for x in (track.get("lang"), track.get("title")) if x) or f"piste {track.get('id', '?')}"
        else:
            label = "désactivés" if prop == "sid" else "par défaut"
        text = f"{'Audio' if prop == 'aid' else 'Sous-titres'} : {label}"
        await self.mpv.command("show-text", text, 2500)
        return text

    async def volume(self, delta: int) -> None:
        if self.state.backend == "mpv":
            await self.mpv.command("add", "volume", delta)

    def report(self, **changes: Any) -> PlayerState:
        """The browser reports its own progress/errors here."""
        allowed = {k: v for k, v in changes.items() if k in ("position", "duration", "paused", "error")}
        return self._set(**allowed)

    async def poll(self) -> PlayerState:
        if self.state.backend == "mpv":
            if not self.mpv.running():
                return self._set(backend="idle")
            pos = await self.mpv.command("get_property", "time-pos")
            dur = await self.mpv.command("get_property", "duration")
            return self._set(position=float(pos or 0), duration=float(dur or 0))
        return self.state

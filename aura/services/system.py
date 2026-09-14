"""System actions: reboot, shutdown, update, logs, hardware info. Linux-only side effects."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import IS_LINUX, SETTINGS

log = logging.getLogger(__name__)

_BOOT = time.time()


async def _run(*args: str, timeout: float = 600) -> tuple[int, str]:
    if not IS_LINUX:
        return 0, f"(mock) {' '.join(args)}"
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode or 0, out.decode(errors="replace")
    except (OSError, asyncio.TimeoutError) as exc:
        return 1, str(exc)


def info() -> dict[str, Any]:
    disk = shutil.disk_usage(SETTINGS.data_dir if SETTINGS.data_dir.exists() else Path.cwd())
    mem_total = mem_free = 0
    if IS_LINUX:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    mem_free = int(line.split()[1]) * 1024
        except OSError:
            pass
    model = ""
    for p in ("/sys/devices/virtual/dmi/id/product_name", "/sys/firmware/devicetree/base/model"):
        try:
            model = Path(p).read_text(errors="replace").strip("\x00\n ")
            break
        except OSError:
            continue
    return {
        "version": __version__,
        "hostname": platform.node(),
        "platform": platform.platform(),
        "model": model,
        "uptime_app": int(time.time() - _BOOT),
        "disk_total": disk.total,
        "disk_free": disk.free,
        "mem_total": mem_total,
        "mem_free": mem_free,
        "data_dir": str(SETTINGS.data_dir),
        "mpv": bool(shutil.which("mpv")),
        "chrome": bool(shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")),
        "linux": IS_LINUX,
    }


async def reboot() -> tuple[int, str]:
    return await _run("sudo", "-n", "systemctl", "reboot")


async def shutdown() -> tuple[int, str]:
    return await _run("sudo", "-n", "systemctl", "poweroff")


async def restart_kiosk() -> tuple[int, str]:
    return await _run("sudo", "-n", "systemctl", "restart", "aura-kiosk.service")


async def update() -> tuple[int, str]:
    script = os.environ.get("AURA_UPDATE_SCRIPT", "/opt/aura/os/scripts/update.sh")
    return await _run("sudo", "-n", script, timeout=900)


async def logs(lines: int = 200) -> str:
    _, out = await _run("journalctl", "-u", "aura", "-u", "aura-kiosk", "-n", str(lines), "--no-pager", timeout=20)
    return out


async def audio_sinks() -> list[dict[str, str]]:
    """List PipeWire/PulseAudio sinks via wpctl or pactl."""
    if not IS_LINUX:
        return [{"id": "1", "name": "HDMI (mock)", "default": "yes"}]
    code, out = await _run("pactl", "list", "short", "sinks", timeout=10)
    sinks = []
    if code == 0:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                sinks.append({"id": parts[0], "name": parts[1], "default": "no"})
    _, default = await _run("pactl", "get-default-sink", timeout=10)
    return [{**s, "default": "yes" if s["name"] == default.strip() else "no"} for s in sinks]


async def set_default_sink(name: str) -> tuple[int, str]:
    return await _run("pactl", "set-default-sink", name, timeout=10)


async def set_volume(percent: int) -> tuple[int, str]:
    percent = max(0, min(150, percent))
    return await _run("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%", timeout=10)


async def volume_step(delta: int) -> tuple[int, str]:
    sign = "+" if delta >= 0 else "-"
    return await _run("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{abs(delta)}%", timeout=10)


async def mute_toggle() -> tuple[int, str]:
    return await _run("pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle", timeout=10)

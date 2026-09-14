"""Kiosk browser control through the Chrome DevTools Protocol (CDP).

Chrome runs with --remote-debugging-port=<port> bound to localhost. Through CDP we can:
  - navigate the kiosk tab (open Netflix / Prime / Canal+, come back home)
  - inject keyboard and mouse events (phone acts as trackpad + keyboard on DRM sites)
No uinput/ydotool needed; input goes through the browser's own pipeline, so it works on DRM pages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import websockets

from ..config import SETTINGS

log = logging.getLogger(__name__)

KEY_MAP: dict[str, tuple[str, str, int]] = {
    # name -> (key, code, windowsVirtualKeyCode)
    "ArrowUp": ("ArrowUp", "ArrowUp", 38),
    "ArrowDown": ("ArrowDown", "ArrowDown", 40),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft", 37),
    "ArrowRight": ("ArrowRight", "ArrowRight", 39),
    "Enter": ("Enter", "Enter", 13),
    "Escape": ("Escape", "Escape", 27),
    "Backspace": ("Backspace", "Backspace", 8),
    "Tab": ("Tab", "Tab", 9),
    "Space": (" ", "Space", 32),
    "F11": ("F11", "F11", 122),
    "MediaPlayPause": ("MediaPlayPause", "MediaPlayPause", 179),
    "AudioVolumeUp": ("AudioVolumeUp", "AudioVolumeUp", 175),
    "AudioVolumeDown": ("AudioVolumeDown", "AudioVolumeDown", 174),
    "AudioVolumeMute": ("AudioVolumeMute", "AudioVolumeMute", 173),
    "BrowserBack": ("BrowserBack", "BrowserBack", 166),
}


class Kiosk:
    def __init__(self, port: int = SETTINGS.chrome_debug_port):
        self.port = port
        self._id = 0
        self._down_until = 0.0
        self._ws: Any = None
        self._lock = asyncio.Lock()
        self.pointer = [640.0, 360.0]
        self.viewport = (1920, 1080)

    async def _target(self) -> str | None:
        # A refused localhost connection can take ~2 s (Windows retries it). Remember the failure for a
        # few seconds instead of paying that on every remote key press.
        if time.monotonic() < self._down_until:
            return None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=0.5)) as http:
                r = await http.get(f"http://127.0.0.1:{self.port}/json")
                pages = [p for p in r.json() if p.get("type") == "page"]
        except (httpx.HTTPError, ValueError):
            self._down_until = time.monotonic() + 15
            return None
        return pages[0]["webSocketDebuggerUrl"] if pages else None

    def mark_up(self) -> None:
        """The TV page just connected, so the kiosk browser is running."""
        self._down_until = 0.0

    async def available(self) -> bool:
        return await self._target() is not None

    async def _send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        async with self._lock:
            if self._ws is None or getattr(self._ws, "closed", False):
                url = await self._target()
                if not url:
                    raise ConnectionError("kiosk browser not reachable")
                self._ws = await websockets.connect(url, max_size=8 * 1024 * 1024)
            self._id += 1
            await self._ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await asyncio.wait_for(self._ws.recv(), 5))
                if msg.get("id") == self._id:
                    if "error" in msg:
                        raise RuntimeError(msg["error"].get("message", "cdp error"))
                    return msg.get("result", {})

    async def safe(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        try:
            return await self._send(method, params)
        except (ConnectionError, RuntimeError, OSError, asyncio.TimeoutError, websockets.WebSocketException) as exc:
            log.debug("cdp %s failed: %s", method, exc)
            self._ws = None
            return None

    async def navigate(self, url: str) -> bool:
        return await self.safe("Page.navigate", {"url": url}) is not None

    async def current_url(self) -> str:
        r = await self.safe("Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
        return ((r or {}).get("result") or {}).get("value", "")

    async def home(self) -> bool:
        return await self.navigate(f"http://127.0.0.1:{SETTINGS.port}/tv/")

    async def key(self, name: str, text: str = "") -> bool:
        if name in KEY_MAP:
            key, code, vk = KEY_MAP[name]
        elif len(name) == 1:
            key, code, vk = name, f"Key{name.upper()}" if name.isalpha() else f"Digit{name}", ord(name.upper())
        else:
            return False
        base: dict[str, Any] = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
        down = {**base, "type": "keyDown"}
        if text or (len(key) == 1):
            down["text"] = text or key
            down["unmodifiedText"] = text or key
        ok = await self.safe("Input.dispatchKeyEvent", down) is not None
        await self.safe("Input.dispatchKeyEvent", {**base, "type": "keyUp"})
        return ok

    async def type_text(self, text: str) -> bool:
        return await self.safe("Input.insertText", {"text": text}) is not None

    async def move(self, dx: float, dy: float) -> bool:
        w, h = self.viewport
        self.pointer = [min(max(self.pointer[0] + dx, 0), w - 1), min(max(self.pointer[1] + dy, 0), h - 1)]
        x, y = self.pointer
        return await self.safe("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}) is not None

    async def click(self, button: str = "left") -> bool:
        x, y = self.pointer
        p = {"x": x, "y": y, "button": button, "clickCount": 1}
        ok = await self.safe("Input.dispatchMouseEvent", {**p, "type": "mousePressed"}) is not None
        await self.safe("Input.dispatchMouseEvent", {**p, "type": "mouseReleased"})
        return ok

    async def scroll(self, dy: float) -> bool:
        x, y = self.pointer
        return await self.safe("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy}) is not None

    async def refresh_viewport(self) -> None:
        r = await self.safe("Runtime.evaluate", {"expression": "[innerWidth, innerHeight]", "returnByValue": True})
        val = ((r or {}).get("result") or {}).get("value")
        if isinstance(val, list) and len(val) == 2:
            self.viewport = (int(val[0]), int(val[1]))
            self.pointer = [self.viewport[0] / 2, self.viewport[1] / 2]

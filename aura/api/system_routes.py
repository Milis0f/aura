"""REST: network, settings, system, websocket."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import db
from ..config import SETTINGS
from ..core import settings as registry
from ..services import catalog, library, network, system
from . import guard
from .state import AppState, state_of

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class WifiIn(BaseModel):
    ssid: str
    password: str = ""


class StaticIn(BaseModel):
    ip_cidr: str
    gateway: str
    dns: str = "1.1.1.1 9.9.9.9"


class SettingsIn(BaseModel):
    values: dict[str, str]


PUBLIC_SETTINGS = (
    "tmdb_api_key", "language", "ui_theme", "device_name", "autoplay_last", "setup_done",
    "epg_last_refresh", "sports_last_refresh", "parental_pin", "live_backend", "fast_start",
    "ui_accent", "autoplay_next", "automount",
)


# ---- network --------------------------------------------------------------

@router.get("/network")
async def net_status() -> dict[str, Any]:
    s = await network.status()
    urls = [f"http://{s.hostname}.local:{SETTINGS.port}/" if SETTINGS.port != 80 else f"http://{s.hostname}.local/"]
    urls += [f"http://{ip}:{SETTINGS.port}/" if SETTINGS.port != 80 else f"http://{ip}/" for ip in s.addresses]
    return {**s.to_dict(), "urls": urls, "hotspot_ssid": SETTINGS.hotspot_ssid, "hotspot_password": SETTINGS.hotspot_password}


@router.get("/network/wifi")
async def wifi_scan() -> dict[str, Any]:
    return {"networks": await network.scan()}


@router.post("/network/wifi")
async def wifi_connect(body: WifiIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    ok, out = await network.connect_wifi(body.ssid, body.password)
    if not ok:
        raise HTTPException(400, out or "connexion impossible")
    await st.hub.broadcast({"type": "network_changed"})
    return {"ok": True, "message": out}


@router.delete("/network/wifi/{ssid}")
async def wifi_forget(ssid: str) -> dict[str, bool]:
    await network.forget_wifi(ssid)
    return {"ok": True}


@router.post("/network/hotspot")
async def hotspot_start(st: AppState = Depends(state_of)) -> dict[str, Any]:
    ok, out = await network.start_hotspot()
    await st.hub.broadcast({"type": "network_changed"})
    return {"ok": ok, "message": out}


@router.delete("/network/hotspot")
async def hotspot_stop(st: AppState = Depends(state_of)) -> dict[str, bool]:
    await network.stop_hotspot()
    await st.hub.broadcast({"type": "network_changed"})
    return {"ok": True}


@router.post("/network/ethernet/static")
async def eth_static(body: StaticIn) -> dict[str, Any]:
    ok, out = await network.set_ethernet_static(body.ip_cidr, body.gateway, body.dns)
    if not ok:
        raise HTTPException(400, out)
    return {"ok": True, "message": out}


@router.post("/network/ethernet/dhcp")
async def eth_dhcp() -> dict[str, Any]:
    ok, out = await network.set_ethernet_dhcp()
    if not ok:
        raise HTTPException(400, out)
    return {"ok": True, "message": out}


# ---- settings / setup -----------------------------------------------------

@router.get("/settings")
def get_settings() -> dict[str, Any]:
    vals = db.all_settings()
    out = {k: vals.get(k, "") for k in PUBLIC_SETTINGS}
    if out.get("tmdb_api_key"):
        out["tmdb_api_key_set"] = "1"
        out["tmdb_api_key"] = ""
    return {"settings": out}


@router.get("/settings/schema")
def settings_schema() -> dict[str, Any]:
    """What the settings screen renders itself from. Developer entries are absent unless the mode is on."""
    return {"settings": registry.schema(), "developer": registry.developer_on()}


@router.put("/settings")
async def put_settings(request: Request, body: SettingsIn, st: AppState = Depends(state_of)) -> dict[str, Any]:
    declared = {k: v for k, v in body.values.items() if k in registry.BY_KEY}
    legacy = {k: v for k, v in body.values.items() if k not in registry.BY_KEY}

    # A developer knob can make the box behave in ways nobody else can explain: it needs a real account
    # with write rights, where an ordinary setting keeps the access it has always had.
    if any(registry.BY_KEY[k].scope == registry.DEVELOPER for k in declared):
        guard.need_write(request)

    leaving_developer = (
        registry.DEVELOPER_KEY in declared
        and registry.developer_on()
        and not registry.BY_KEY[registry.DEVELOPER_KEY].coerce(declared[registry.DEVELOPER_KEY])
    )

    for k, v in declared.items():
        if registry.BY_KEY[k].secret and v == "":
            continue  # an empty secret means "keep the one you have"
        try:
            registry.put(k, v)
        except ValueError as exc:
            raise HTTPException(400, f"{registry.BY_KEY[k].label} : {exc}") from exc

    for k, v in legacy.items():
        if k not in PUBLIC_SETTINGS:
            raise HTTPException(400, f"réglage inconnu : {k}")
        db.set_setting(k, str(v)[:500])

    if leaving_developer:
        registry.reset_scope(registry.DEVELOPER)

    await st.hub.broadcast({"type": "settings_changed"})
    return get_settings()


@router.get("/setup")
async def setup_status() -> dict[str, Any]:
    net = await network.status()
    return {
        "network_online": net.online,
        "hotspot_active": net.hotspot_active,
        "configured": catalog.is_configured(),
        "setup_done": db.get_setting("setup_done", "") == "1",
        "counts": catalog.counts(),
        "library": library.counts(),
        "tmdb": bool(db.get_setting("tmdb_api_key", "")),
    }


# ---- system ---------------------------------------------------------------

@router.get("/system")
async def system_info() -> dict[str, Any]:
    return {**system.info(), "audio": await system.audio_sinks()}


@router.post("/system/{action}")
async def system_action(action: str, st: AppState = Depends(state_of)) -> dict[str, Any]:
    actions = {"reboot": system.reboot, "shutdown": system.shutdown, "update": system.update, "restart-kiosk": system.restart_kiosk}
    if action not in actions:
        raise HTTPException(404, "action inconnue")
    if action in ("reboot", "shutdown"):
        await st.hub.broadcast({"type": "system", "action": action})
    code, out = await actions[action]()
    return {"ok": code == 0, "output": out[-4000:]}


@router.get("/system/logs")
async def system_logs(lines: int = 200) -> dict[str, str]:
    return {"logs": await system.logs(lines)}


@router.post("/system/audio/default")
async def audio_default(name: str) -> dict[str, Any]:
    code, out = await system.set_default_sink(name)
    return {"ok": code == 0, "output": out}


# ---- websocket ------------------------------------------------------------

@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, role: str = "remote") -> None:
    st: AppState = ws.app.state.aura
    await ws.accept()
    await st.hub.add(ws, "tv" if role == "tv" else "remote")
    try:
        await ws.send_text(json.dumps({"type": "hello", "role": role, "player": st.player.state.to_dict()}))
        if role == "tv":
            st.kiosk.mark_up()
            pending = st.pending_play
            if pending and time.monotonic() - pending[0] < 30:
                st.pending_play = None
                await ws.send_text(json.dumps(pending[1]))
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _handle_ws(st, ws, role, msg)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.debug("ws closed: %s", exc)
    finally:
        await st.hub.remove(ws)


async def _handle_ws(st: AppState, ws: WebSocket, role: str, msg: dict[str, Any]) -> None:
    t = msg.get("type")
    if role == "tv":
        if t == "progress":
            state = st.player.report(position=float(msg.get("position") or 0), duration=float(msg.get("duration") or 0), paused=bool(msg.get("paused")))
            if state.item_id.startswith("lib:") and state.position > 0:
                library.save_progress(state.item_id[4:], state.position, state.duration)
            await st.hub.to_remotes({"type": "player", "state": state.to_dict()})
        elif t == "view":
            await st.hub.to_remotes({"type": "tv_view", "view": msg.get("view"), "focus": msg.get("focus")})
        elif t == "error":
            st.player.report(error=str(msg.get("error") or ""))
            await st.hub.to_remotes({"type": "player", "state": st.player.state.to_dict()})
        elif t == "stopped":
            await st.player.stop()
            await st.hub.to_remotes({"type": "player", "state": st.player.state.to_dict()})
    else:
        if t == "key":
            await st.hub.to_tv({"type": "key", "key": msg.get("key"), "text": msg.get("text", "")})
        elif t == "ping":
            await ws.send_text(json.dumps({"type": "pong"}))
